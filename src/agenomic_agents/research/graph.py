import json
from typing import Literal, TypedDict

from langchain.chat_models import init_chat_model
from langgraph.graph import END, START, StateGraph

from agenomic_agents.common.ledger import SignedLedger
from agenomic_agents.research.models import (
    AnalysisResult,
    ComplianceResult,
    EvidenceItem,
    EvidencePackage,
    FinalReport,
    ResearchFindings,
    ResearchRequest,
    SourceCheck,
    VerificationResult,
)


class ResearchState(TypedDict, total=False):
    request: ResearchRequest
    findings: ResearchFindings
    verification: VerificationResult
    analysis: AnalysisResult
    compliance: ComplianceResult
    evidence: EvidencePackage
    final_report: FinalReport
    revision_count: int


def build_research_graph(model: str, ledger: SignedLedger, run_id: str):  # type: ignore[no-untyped-def]
    llm = init_chat_model(model, temperature=0)
    researcher = llm.with_structured_output(ResearchFindings).with_config(
        {"run_name": "research_agent"}
    )
    verifier = llm.with_structured_output(VerificationResult).with_config(
        {"run_name": "source_verification_agent"}
    )
    analyst = llm.with_structured_output(AnalysisResult).with_config({"run_name": "analysis_agent"})
    compliance_agent = llm.with_structured_output(ComplianceResult).with_config(
        {"run_name": "compliance_agent"}
    )
    editor = llm.with_structured_output(FinalReport).with_config({"run_name": "editor_agent"})

    def research_node(state: ResearchState) -> dict[str, object]:
        request = state["request"]
        result = researcher.invoke(
            _prompt(
                "Extract supported findings only. Every finding must cite one or more source_id "
                "values. Never invent sources or facts.",
                request,
            )
        )
        validated = ResearchFindings.model_validate(result)
        _event(ledger, run_id, "research", "findings_created", len(validated.findings))
        return {"findings": validated}

    def verify_node(state: ResearchState) -> dict[str, object]:
        request = state["request"]
        result = verifier.invoke(
            _prompt(
                "Assess whether each supplied source is usable. Flag missing dates, unsupported "
                "authority, promotional language, and internal contradictions. "
                "Check every source_id.",
                request,
            )
        )
        validated = VerificationResult.model_validate(result)
        supplied = {source.source_id for source in request.sources}
        returned = {check.source_id for check in validated.checks}
        for missing_id in sorted(supplied - returned):
            validated.checks.append(
                SourceCheck(
                    source_id=missing_id, usable=False, concerns=["Not checked by verifier"]
                )
            )
        _event(ledger, run_id, "source_verification", "sources_checked", len(validated.checks))
        return {"verification": validated}

    def analysis_node(state: ResearchState) -> dict[str, object]:
        result = analyst.invoke(
            json.dumps(
                {
                    "instruction": (
                        "Synthesize only claims supported by findings and usable sources. Preserve "
                        "source_ids and state limitations."
                    ),
                    "topic": state["request"].topic,
                    "findings": state["findings"].model_dump(mode="json"),
                    "verification": state["verification"].model_dump(mode="json"),
                }
            )
        )
        validated = AnalysisResult.model_validate(result)
        _event(ledger, run_id, "analysis", "analysis_created", len(validated.claims))
        return {"analysis": validated, "revision_count": state.get("revision_count", 0)}

    def compliance_node(state: ResearchState) -> dict[str, object]:
        request = state["request"]
        result = compliance_agent.invoke(
            json.dumps(
                {
                    "instruction": (
                        "Apply every policy requirement. Block unsupported, misleading, legal, "
                        "medical, financial, or absolute claims. Do not rewrite the report."
                    ),
                    "jurisdiction": request.jurisdiction,
                    "policy_requirements": request.policy_requirements,
                    "analysis": state["analysis"].model_dump(mode="json"),
                    "valid_source_ids": [source.source_id for source in request.sources],
                }
            )
        )
        validated = ComplianceResult.model_validate(result)
        _event(ledger, run_id, "compliance", "compliance_checked", int(validated.allowed))
        return {"compliance": validated}

    def revise_node(state: ResearchState) -> dict[str, object]:
        analysis = state["analysis"]
        risky = set(state["compliance"].risky_claims)
        safe_claims = [claim for claim in analysis.claims if claim.statement not in risky]
        revised = analysis.model_copy(
            update={
                "claims": safe_claims,
                "limitations": [
                    *analysis.limitations,
                    *state["compliance"].required_changes,
                ],
            }
        )
        count = state.get("revision_count", 0) + 1
        _event(ledger, run_id, "analysis", "analysis_revised", count)
        return {"analysis": revised, "revision_count": count}

    def evidence_node(state: ResearchState) -> dict[str, object]:
        valid_ids = {source.source_id for source in state["request"].sources}
        items = [
            EvidenceItem(
                claim=claim.statement,
                source_ids=[source_id for source_id in claim.source_ids if source_id in valid_ids],
            )
            for claim in state["analysis"].claims
        ]
        supported = sum(bool(item.source_ids) for item in items)
        coverage = supported / len(items) if items else 0.0
        package = EvidencePackage(items=items, coverage=round(coverage, 4))
        _event(ledger, run_id, "evidence", "evidence_packaged", supported)
        return {"evidence": package}

    def editor_node(state: ResearchState) -> dict[str, object]:
        result = editor.invoke(
            json.dumps(
                {
                    "instruction": (
                        "Write a concise Markdown report without changing claim meaning. "
                        "Cite sources "
                        "inline as [source_id]. Include limitations and compliance notes."
                    ),
                    "topic": state["request"].topic,
                    "analysis": state["analysis"].model_dump(mode="json"),
                    "evidence": state["evidence"].model_dump(mode="json"),
                    "compliance": state["compliance"].model_dump(mode="json"),
                }
            )
        )
        report = FinalReport.model_validate(result).model_copy(update={"blocked": False})
        validate_final_report(report, state["evidence"], state["request"])
        _event(ledger, run_id, "editor", "report_created", len(report.cited_source_ids))
        return {"final_report": report}

    def blocked_node(state: ResearchState) -> dict[str, object]:
        compliance = state["compliance"]
        report = FinalReport(
            title=f"Blocked report: {state['request'].topic[:120]}",
            markdown=(
                "# Report blocked by compliance\n\nThe report could not be safely produced after "
                "the permitted revision cycle."
            ),
            cited_source_ids=[],
            blocked=True,
            compliance_notes=[compliance.rationale, *compliance.required_changes],
        )
        return {"final_report": report, "evidence": EvidencePackage(items=[], coverage=0)}

    graph = StateGraph(ResearchState)
    graph.add_node("research", research_node)
    graph.add_node("verify", verify_node)
    graph.add_node("analysis", analysis_node)
    graph.add_node("compliance", compliance_node)
    graph.add_node("revise", revise_node)
    graph.add_node("evidence", evidence_node)
    graph.add_node("editor", editor_node)
    graph.add_node("blocked", blocked_node)
    graph.add_edge(START, "research")
    graph.add_edge(START, "verify")
    graph.add_edge(["research", "verify"], "analysis")
    graph.add_edge("analysis", "compliance")
    graph.add_conditional_edges(
        "compliance",
        _route_compliance,
        {"evidence": "evidence", "revise": "revise", "blocked": "blocked"},
    )
    graph.add_edge("revise", "compliance")
    graph.add_edge("evidence", "editor")
    graph.add_edge("editor", END)
    graph.add_edge("blocked", END)
    return graph.compile()


def _route_compliance(state: ResearchState) -> Literal["evidence", "revise", "blocked"]:
    if state["compliance"].allowed:
        return "evidence"
    if state.get("revision_count", 0) < 2:
        return "revise"
    return "blocked"


def validate_final_report(
    report: FinalReport, evidence: EvidencePackage, request: ResearchRequest
) -> None:
    """Deterministic integrity gate executed after the editor LLM."""
    valid_ids = {source.source_id for source in request.sources}
    cited = set(report.cited_source_ids)
    if not cited.issubset(valid_ids):
        raise ValueError("Editor returned unknown source identifiers")
    required = {source_id for item in evidence.items for source_id in item.source_ids}
    if required and not required.issubset(cited):
        raise ValueError("Editor omitted sources present in the evidence package")
    if evidence.items and evidence.coverage < 1:
        raise ValueError("Evidence coverage is incomplete")


def _prompt(instruction: str, request: ResearchRequest) -> str:
    return json.dumps(
        {"instruction": instruction, "request": request.model_dump(mode="json")},
        separators=(",", ":"),
    )


def _event(ledger: SignedLedger, run_id: str, agent: str, action: str, count: int) -> None:
    ledger.append(
        run_id=run_id,
        agent=f"research.{agent}",
        action=action,
        payload={"count": count},
    )
