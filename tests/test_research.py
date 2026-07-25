import pytest

from agenomic_agents.common.agenomic import AgenomicRuntime
from agenomic_agents.common.config import Settings
from agenomic_agents.common.ledger import SignedLedger
from agenomic_agents.research import graph as graph_module
from agenomic_agents.research.graph import build_research_graph, validate_final_report
from agenomic_agents.research.models import (
    AnalysisResult,
    ComplianceResult,
    EvidenceItem,
    EvidencePackage,
    FinalReport,
    Finding,
    ResearchFindings,
    ResearchRequest,
    SourceCheck,
    SourceDocument,
    VerificationResult,
)
from agenomic_agents.research.service import ResearchService


def _request() -> ResearchRequest:
    return ResearchRequest(
        report_id="RPT-1",
        topic="AI governance market analysis",
        sources=[
            SourceDocument(
                source_id="src-1",
                title="Authoritative source",
                content="A sufficiently long authoritative source body for the test suite.",
            )
        ],
    )


def test_editor_cannot_introduce_unknown_source() -> None:
    report = FinalReport(
        title="Report",
        markdown="A report body citing an unknown source [fake].",
        cited_source_ids=["fake"],
    )
    with pytest.raises(ValueError, match="unknown source"):
        validate_final_report(report, EvidencePackage(items=[], coverage=0), _request())


def test_editor_must_preserve_evidence_citations() -> None:
    report = FinalReport(title="Report", markdown="A long enough report body.")
    evidence = EvidencePackage(
        items=[EvidenceItem(claim="Supported claim", source_ids=["src-1"])], coverage=1
    )
    with pytest.raises(ValueError, match="omitted"):
        validate_final_report(report, evidence, _request())


class _FakeStructuredModel:
    def __init__(self, schema: type) -> None:
        self.schema = schema

    def with_config(self, _config):  # type: ignore[no-untyped-def]
        return self

    def invoke(self, _prompt):  # type: ignore[no-untyped-def]
        finding = Finding(statement="Supported claim", source_ids=["src-1"])
        responses = {
            ResearchFindings: ResearchFindings(findings=[finding]),
            VerificationResult: VerificationResult(
                checks=[SourceCheck(source_id="src-1", usable=True)]
            ),
            AnalysisResult: AnalysisResult(
                executive_summary="A supported executive summary.", claims=[finding]
            ),
            ComplianceResult: ComplianceResult(allowed=True, rationale="All claims have evidence."),
            FinalReport: FinalReport(
                title="Compliant report",
                markdown="# Compliant report\n\nSupported claim [src-1].",
                cited_source_ids=["src-1"],
            ),
        }
        return responses[self.schema]


class _FakeModel:
    def with_structured_output(self, schema: type) -> _FakeStructuredModel:
        return _FakeStructuredModel(schema)


def test_langgraph_happy_path_packages_evidence(
    monkeypatch: pytest.MonkeyPatch, ledger: SignedLedger
) -> None:
    monkeypatch.setattr(graph_module, "init_chat_model", lambda *args, **kwargs: _FakeModel())
    workflow = build_research_graph("openai:test", ledger, "run-1")
    state = workflow.invoke({"request": _request(), "revision_count": 0})
    assert state["final_report"].blocked is False
    assert state["evidence"].coverage == 1
    assert state["final_report"].cited_source_ids == ["src-1"]
    assert ledger.verify()


def test_research_service_records_langgraph_nodes_in_agenomic(
    monkeypatch: pytest.MonkeyPatch, settings: Settings, ledger: SignedLedger
) -> None:
    monkeypatch.setattr(graph_module, "init_chat_model", lambda *args, **kwargs: _FakeModel())
    runtime = AgenomicRuntime(settings)
    try:
        result = ResearchService(settings, ledger, runtime).create_report(_request())
        traces = runtime.find_by_domain_run(result.run_id)
    finally:
        runtime.close()

    assert result.agenomic_run_id
    assert len(traces) == 1
    assert traces[0]["labels"]["framework"] == "langgraph"
    assert {call["server"] for call in traces[0]["tool_calls"]} == {"langgraph"}
    assert {call["tool"] for call in traces[0]["tool_calls"]} >= {
        "research",
        "verify",
        "analysis",
        "compliance",
        "evidence",
        "editor",
    }
