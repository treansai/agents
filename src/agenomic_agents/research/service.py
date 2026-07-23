from uuid import uuid4

from agenomic_agents.common.config import Settings
from agenomic_agents.common.ledger import SignedLedger
from agenomic_agents.common.observability import langgraph_config, observation
from agenomic_agents.research.graph import build_research_graph
from agenomic_agents.research.models import ResearchRequest, ResearchResponse


class ResearchService:
    def __init__(self, settings: Settings, ledger: SignedLedger) -> None:
        self.settings = settings
        self.ledger = ledger

    def create_report(self, request: ResearchRequest) -> ResearchResponse:
        if len(request.sources) > self.settings.research_max_sources:
            raise ValueError(
                f"At most {self.settings.research_max_sources} sources are allowed per run"
            )
        run_id = str(uuid4())
        self.ledger.append(
            run_id=run_id,
            agent="research.intake",
            action="report_requested",
            payload={"report_id": request.report_id, "source_count": len(request.sources)},
        )
        graph = build_research_graph(self.settings.langgraph_model, self.ledger, run_id)
        with observation("research-compliance-report"):
            state = graph.invoke(
                {"request": request, "revision_count": 0}, config=langgraph_config(run_id)
            )
        event = self.ledger.append(
            run_id=run_id,
            agent="research.workflow",
            action="workflow_completed",
            payload={
                "report_id": request.report_id,
                "blocked": state["final_report"].blocked,
                "coverage": state["evidence"].coverage,
            },
        )
        return ResearchResponse(
            run_id=run_id,
            report_id=request.report_id,
            report=state["final_report"],
            evidence=state["evidence"],
            audit_signature=event.signature,
        )
