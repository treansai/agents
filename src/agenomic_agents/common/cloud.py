"""Connected mode: mirror traces and live-tracking events to Agenomic Cloud.

The local JSONL + ATEP exporters remain the source of truth. Every call here
is best-effort: network or API failures are logged and never fail a business
run. Payloads follow the same redaction rule as the local exporters — only
correlations, hashes, and metadata leave the process, never claim contents.
"""

from __future__ import annotations

import threading
from typing import Any

import httpx

from agenomic.exporters.base import Exporter
from agenomic.types.envelope import TraceEnvelope

from agenomic_agents.common.config import Settings
from agenomic_agents.common.logging import get_logger

logger = get_logger(__name__)

_TIMEOUT = httpx.Timeout(10.0, connect=5.0)

CLAIMS_ALLOWED_TOOLS = ["claims.agent.run", "claims.policy.evaluate"]
CLAIMS_POLICY_ID = "claims.deterministic_gate"
CLAIMS_ALLOWED_INTENTS = [
    "claims.decision:approve",
    "claims.decision:reject",
    "claims.decision:request_documents",
    "claims.decision:human_review",
]
CLAIMS_FORBIDDEN_INTENTS = ["auto_approve:high_value_claim"]


class AgenomicCloud:
    """Bridge to the Agenomic Cloud API gateway (api.agenomic.io).

    Enabled only when endpoint, API key, and the cloud agent UUID are all
    configured; otherwise every method is a no-op so offline development
    and the test suite behave exactly as before.
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._lock = threading.RLock()
        self._session_id: str | None = settings.agenomic_tracking_session or None
        self._client: httpx.Client | None = None
        self.enabled = bool(
            settings.agenomic_endpoint
            and settings.agenomic_api_key
            and settings.agenomic_agent_uuid
        )
        if self.enabled:
            assert settings.agenomic_endpoint is not None
            assert settings.agenomic_api_key is not None
            # The gateway's auth middleware reads `x-api-key` (the Bearer
            # scheme is legacy); send both for forward compatibility.
            key = settings.agenomic_api_key.get_secret_value()
            self._client = httpx.Client(
                base_url=settings.agenomic_endpoint.rstrip("/"),
                headers={"x-api-key": key, "Authorization": f"Bearer {key}"},
                timeout=_TIMEOUT,
            )

    # -- traces ---------------------------------------------------------

    def upload_trace(self, envelope: TraceEnvelope) -> None:
        """Mirror one redacted trace envelope to POST /v1/traces.

        Trace events feed the platform's behavioral-drift detector (BDS over
        the event-type distribution), so each tool call and the enforced
        decision are projected as events — hashes and labels only.
        """
        if self._client is None:
            return
        events: list[dict[str, Any]] = [
            {
                "event_type": "tool.call.completed",
                "payload": {
                    "tool_name": call.tool,
                    "input_hash": call.input_hash,
                    "status": str(call.status.value if hasattr(call.status, "value") else call.status),
                },
            }
            for call in envelope.tool_calls
        ]
        decision = envelope.labels.get("decision")
        if decision:
            events.append(
                {"event_type": f"decision.{decision}", "payload": {"decision": decision}}
            )
        body = {
            "agent_id": self._settings.agenomic_agent_uuid,
            "external_id": envelope.run_id,
            "payload": envelope.model_dump(mode="json", exclude_none=True),
            "events": events,
        }
        if self._post("/v1/traces", body, what="trace") is None and events:
            # Older gateways reject trace events (org_id bug, corrigé côté
            # plateforme) : la trace elle-même vaut mieux que rien.
            body.pop("events")
            self._post("/v1/traces", body, what="trace (sans events)")

    # -- live tracking ----------------------------------------------------

    def tracking_session_id(self) -> str | None:
        """Return the active tracking session id, creating one if needed."""
        if self._client is None:
            return None
        with self._lock:
            if self._session_id:
                return self._session_id
            body = {
                "agent_id": self._settings.agenomic_agent_uuid,
                "release_id": self._settings.agenomic_release,
                "environment": self._settings.agenomic_environment,
                "baseline": {
                    "allowed_tools": CLAIMS_ALLOWED_TOOLS,
                    "model_provider": "google",
                    "model_id": self._settings.agenomic_baseline_model_id,
                    "policy_ids": [CLAIMS_POLICY_ID],
                },
                "tracking_config": {
                    "intent": {
                        "allowed_intents": CLAIMS_ALLOWED_INTENTS,
                        "forbidden_intents": CLAIMS_FORBIDDEN_INTENTS,
                    },
                },
            }
            created = self._post("/v1/tracking/sessions", body, what="tracking session")
            if created:
                self._session_id = created.get("session", {}).get("session_id")
                logger.info("agenomic_tracking_session_started", session_id=self._session_id)
            return self._session_id

    def emit(self, event: dict[str, Any]) -> None:
        """Ingest one runtime event; surface any alerts the detector raised."""
        session_id = self.tracking_session_id()
        if session_id is None:
            return
        result = self._post(
            f"/v1/tracking/sessions/{session_id}/events", event, what="tracking event"
        )
        for alert in (result or {}).get("alerts") or []:
            logger.warning(
                "agenomic_tracking_alert",
                severity=alert.get("severity"),
                kind=alert.get("kind"),
                title=alert.get("title"),
                message=alert.get("message"),
                blocks_release=alert.get("blocks_release"),
                requires_human_review=alert.get("requires_human_review"),
            )

    # -- plumbing ---------------------------------------------------------

    def _post(self, path: str, body: dict[str, Any], *, what: str) -> dict[str, Any] | None:
        if self._client is None:
            return None
        try:
            response = self._client.post(path, json=body)
            response.raise_for_status()
            return response.json() if response.content else {}
        except httpx.HTTPError as exc:
            logger.warning("agenomic_cloud_upload_failed", what=what, error=str(exc))
            return None

    def close(self) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None


class CloudTraceExporter(Exporter):
    """SDK exporter that mirrors envelopes to the cloud after local export."""

    def __init__(self, cloud: AgenomicCloud) -> None:
        self._cloud = cloud

    def export(self, envelope: TraceEnvelope) -> None:
        self._cloud.upload_trace(envelope)

    def close(self) -> None:  # the runtime owns the shared client lifecycle
        return
