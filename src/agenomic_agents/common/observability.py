import os
import threading
from contextlib import nullcontext
from typing import Any

from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider

from agenomic_agents.common.logging import get_logger

logger = get_logger(__name__)
_lock = threading.Lock()
_initialized: set[str] = set()


def _has_langfuse() -> bool:
    return bool(os.getenv("LANGFUSE_PUBLIC_KEY") and os.getenv("LANGFUSE_SECRET_KEY"))


def _has_langsmith() -> bool:
    return bool(os.getenv("LANGSMITH_API_KEY"))


def _provider() -> TracerProvider:
    current = trace.get_tracer_provider()
    if isinstance(current, TracerProvider):
        return current
    provider = TracerProvider()
    trace.set_tracer_provider(provider)
    return provider


def configure_adk_observability() -> None:
    """Instrument Google ADK once and fan spans out to configured backends."""
    with _lock:
        if "adk" in _initialized:
            return
        if _has_langfuse():
            from langfuse import get_client
            from openinference.instrumentation.google_adk import GoogleADKInstrumentor

            get_client()
            GoogleADKInstrumentor().instrument(tracer_provider=_provider())
        if _has_langsmith():
            from langsmith.integrations.google_adk import configure_google_adk

            configure_google_adk(
                metadata={"service": "claims-reviewer"}, tags=["google-adk", "claims"]
            )
        _initialized.add("adk")


def configure_crewai_observability() -> None:
    """Instrument CrewAI once. Both Langfuse and LangSmith share the OTel provider."""
    with _lock:
        if "crewai" in _initialized:
            return
        provider = _provider()
        if _has_langfuse():
            from langfuse import get_client

            get_client()
        if _has_langsmith():
            from langsmith.integrations.otel import OtelSpanProcessor

            provider.add_span_processor(OtelSpanProcessor())
        if _has_langfuse() or _has_langsmith():
            from openinference.instrumentation.crewai import CrewAIInstrumentor

            CrewAIInstrumentor().instrument(tracer_provider=provider, skip_dep_check=True)
        _initialized.add("crewai")


def langgraph_config(run_id: str) -> dict[str, Any]:
    """Return callbacks/config that enable Langfuse; LangSmith is env-driven."""
    callbacks: list[Any] = []
    if _has_langfuse():
        from langfuse import get_client
        from langfuse.langchain import CallbackHandler

        get_client()
        callbacks.append(CallbackHandler())
    return {
        "callbacks": callbacks,
        "run_name": "research-compliance-team",
        "configurable": {"thread_id": run_id},
        "metadata": {"run_id": run_id, "framework": "langgraph"},
        "tags": ["research", "compliance", "production"],
    }


def observation(name: str):  # type: ignore[no-untyped-def]
    if not _has_langfuse():
        return nullcontext()
    from langfuse import get_client

    return get_client().start_as_current_observation(as_type="span", name=name)


def flush_observability() -> None:
    if _has_langfuse():
        from langfuse import get_client

        get_client().flush()
