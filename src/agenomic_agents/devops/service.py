from uuid import uuid4

from agenomic_agents.common.config import Settings
from agenomic_agents.common.ledger import SignedLedger
from agenomic_agents.common.observability import configure_crewai_observability, observation
from agenomic_agents.devops.crew import build_incident_crew
from agenomic_agents.devops.guards import blocked_commands
from agenomic_agents.devops.models import (
    IncidentAssessment,
    IncidentRequest,
    IncidentResponse,
    Severity,
)


class IncidentService:
    def __init__(self, settings: Settings, ledger: SignedLedger) -> None:
        self.settings = settings
        self.ledger = ledger

    def respond(self, incident: IncidentRequest, *, use_llm: bool = True) -> IncidentResponse:
        run_id = str(uuid4())
        self.ledger.append(
            run_id=run_id,
            agent="devops.intake",
            action="alert_received",
            payload={"incident_id": incident.incident_id, "service": incident.service},
        )
        with observation("incident-response"):
            assessment = self._run_crew(incident) if use_llm else _fallback_assessment(incident)
            blocked = blocked_commands(assessment.proposed_commands)
        if blocked:
            assessment = assessment.model_copy(
                update={
                    "recommended_actions": [
                        *assessment.recommended_actions,
                        "Human approval required for blocked high-risk commands",
                    ],
                    "proposed_commands": [
                        command
                        for command in assessment.proposed_commands
                        if command not in blocked
                    ],
                }
            )
        # Outbox semantics: these events are durable and can be delivered by a separate worker.
        self.ledger.append(
            run_id=run_id,
            agent="devops.notification",
            action="notification_queued",
            payload={"incident_id": incident.incident_id, "severity": assessment.severity.value},
        )
        event = self.ledger.append(
            run_id=run_id,
            agent="devops.ticket",
            action="ticket_queued",
            payload={"incident_id": incident.incident_id, "blocked_action_count": len(blocked)},
        )
        return IncidentResponse(
            run_id=run_id,
            incident_id=incident.incident_id,
            assessment=assessment,
            blocked_actions=blocked,
            notification_queued=True,
            ticket_queued=True,
            audit_signature=event.signature,
        )

    def _run_crew(self, incident: IncidentRequest) -> IncidentAssessment:
        configure_crewai_observability()
        result = build_incident_crew(
            incident, self.settings.crew_model, self.settings.devops_max_tool_calls
        ).kickoff()
        if result.pydantic is None:
            raise RuntimeError("CrewAI returned no validated IncidentAssessment")
        return IncidentAssessment.model_validate(result.pydantic)


def _fallback_assessment(incident: IncidentRequest) -> IncidentAssessment:
    return IncidentAssessment(
        root_cause="LLM analysis disabled; human diagnosis required",
        confidence=0,
        severity=Severity.HIGH,
        evidence=[incident.alert],
        recommended_actions=["Escalate to the on-call engineer"],
        proposed_commands=[],
        notification_summary=f"Incident {incident.incident_id} requires human diagnosis.",
    )
