"""Agenomic tracing, signed ATEP evidence, and cross-framework correlation."""

from __future__ import annotations

import json
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any, TypeVar, cast

from agenomic.atep.store import AtepStore
from agenomic.crypto.hashing import blake3_hex
from agenomic.crypto.signing import SigningKey
from agenomic.exporters.atep_local import AtepLocalExporter
from agenomic.exporters.base import Exporter
from agenomic.exporters.jsonl import JsonlExporter
from agenomic.exporters.multi import MultiExporter
from agenomic.integrations.langgraph import instrument_langgraph
from agenomic.trace.context import current_recorder
from agenomic.trace.decorator import trace_agent_run
from agenomic.types.envelope import TraceEnvelope
from agenomic.types.trace import CallStatus, ToolCall

from agenomic_agents.common.cloud import AgenomicCloud, CloudTraceExporter
from agenomic_agents.common.config import Settings

R = TypeVar("R")

AGENT_IDS = {
    "claims": "agent://treansai/claims-reviewer",
    "devops": "agent://treansai/incident-responder",
    "research": "agent://treansai/research-compliance-team",
}


class _LockedExporter(Exporter):
    """Serialize SDK exporters, whose v0.1 local stores are process-local writers."""

    def __init__(self, exporter: Exporter, lock: Any) -> None:
        self._exporter = exporter
        self._lock = lock

    def export(self, envelope: TraceEnvelope) -> None:
        with self._lock:
            self._exporter.export(envelope)

    def close(self) -> None:
        with self._lock:
            self._exporter.close()


class AgenomicRuntime:
    """Own the local Agenomic exporters and persistent Ed25519 identity."""

    def __init__(self, settings: Settings) -> None:
        self.root = settings.agenomic_data_path
        self.release = settings.agenomic_release
        self.root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._signing_key = _load_or_create_signing_key(self.root / "signing-key.pem")
        self.cloud = AgenomicCloud(settings)
        self._stores: dict[str, AtepStore] = {}
        self._exporters: dict[str, Exporter] = {}
        for name, agent_id in AGENT_IDS.items():
            agent_root = self.root / name
            store = AtepStore.open_or_init(agent_root / "atep", agent_id)
            sinks: list[Exporter] = [
                JsonlExporter(agent_root / "traces.jsonl"),
                AtepLocalExporter(store, self._signing_key),
            ]
            if name == "claims" and self.cloud.enabled:
                sinks.append(CloudTraceExporter(self.cloud))
            exporter = MultiExporter(*sinks)
            self._stores[name] = store
            self._exporters[name] = _LockedExporter(exporter, self._lock)

    def traced(self, name: str, function: Callable[..., R]) -> Callable[..., R]:
        """Wrap one agent entrypoint without persisting raw input or output payloads."""
        return trace_agent_run(
            AGENT_IDS[name],
            release=self.release,
            exporter=self._exporters[name],
            capture_input=False,
            capture_output=False,
        )(function)

    def verify(self) -> dict[str, dict[str, Any]]:
        """Verify every local ATEP segment, signature, Merkle root, and causal hash."""
        with self._lock:
            verifying_key = self._signing_key.verifying_key()
            return {
                name: {
                    "ok": report.ok,
                    "segments_checked": report.segments_checked,
                    "events_checked": report.events_checked,
                    "failures": report.failures,
                    "root_hash": store.compute_root_hash().hex(),
                    "key_id": verifying_key.key_id,
                }
                for name, store in self._stores.items()
                if (report := store.verify_all(verifying_key))
            }

    def find_by_domain_run(self, run_id: str) -> list[dict[str, Any]]:
        """Return redacted trace envelopes correlated with an application run id."""
        matches: list[dict[str, Any]] = []
        with self._lock:
            for name in AGENT_IDS:
                path = self.root / name / "traces.jsonl"
                if not path.exists():
                    continue
                with path.open(encoding="utf-8") as traces:
                    for line in traces:
                        envelope = json.loads(line)
                        if envelope.get("metadata", {}).get("domain_run_id") == run_id:
                            matches.append(envelope)
        return matches

    def close(self) -> None:
        for exporter in self._exporters.values():
            exporter.close()
        self.cloud.close()


def label_decision(decision: str) -> None:
    """Attach the enforced decision as a redacted label on the active run."""
    recorder = current_recorder()
    if recorder is not None:
        recorder.add_label("decision", decision)


def correlate_run(domain_run_id: str, *, framework: str, workflow: str) -> tuple[str, str]:
    """Attach safe domain correlation and return Agenomic run/trace identifiers."""
    recorder = current_recorder()
    if recorder is None:
        raise RuntimeError("Agenomic trace context is not active")
    recorder.add_label("framework", framework)
    recorder.add_label("workflow", workflow)
    recorder.add_metadata("domain_run_id", domain_run_id)
    return recorder.run_id, recorder.trace_id


def instrument_langgraph_1x(graph: Any) -> Any:
    """Instrument LangGraph, including Runnable nodes skipped by Agenomic SDK 0.1."""
    instrument_langgraph(graph)
    nodes = getattr(graph, "nodes", {})
    for name, node in nodes.items():
        runnable = getattr(node, "runnable", None)
        if runnable is None or callable(runnable):
            continue
        invoke = getattr(runnable, "invoke", None)
        if not callable(invoke):
            continue

        node.runnable = _instrumented_runnable(name, cast("Callable[..., Any]", invoke))
    return graph


def _instrumented_runnable(name: str, invoke: Callable[..., Any]) -> Any:
    from langchain_core.runnables import RunnableLambda

    def wrapped(state: Any, config: Any = None) -> Any:
        with trace_step(name, server="langgraph", input_value=state):
            return invoke(state, config)

    return RunnableLambda(wrapped, name=name)


@contextmanager
def trace_step(
    tool: str,
    *,
    server: str,
    input_value: Any,
    requires_human_approval: bool = False,
) -> Iterator[None]:
    """Record a hashed local/framework step inside the active Agenomic run."""
    recorder = current_recorder()
    started = time.perf_counter()
    status = CallStatus.SUCCESS
    try:
        yield
    except Exception:
        status = CallStatus.ERROR
        raise
    finally:
        if recorder is not None:
            recorder.record_tool_call(
                ToolCall(
                    tool=tool,
                    protocol="local",
                    server=server,
                    input_hash=_stable_hash(input_value),
                    latency_ms=int((time.perf_counter() - started) * 1000),
                    status=status,
                    requires_human_approval=requires_human_approval,
                )
            )


def _stable_hash(value: Any) -> str:
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    blob = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()
    return blake3_hex(blob)


def _load_or_create_signing_key(path: Path) -> SigningKey:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        return SigningKey.from_pem_file(path)
    key = SigningKey.generate()
    key.write_pem_file(path)
    key.write_public_pem_file(path.with_suffix(".pub.pem"))
    return key
