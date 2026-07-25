import pytest

from agenomic_agents.common.config import Settings
from agenomic_agents.common.ledger import SignedLedger
from agenomic_agents.devops.guards import ToolBudget, blocked_commands
from agenomic_agents.devops.models import IncidentRequest
from agenomic_agents.devops.service import IncidentService


def test_dangerous_commands_are_blocked() -> None:
    commands = ["kubectl get pods", "kubectl delete namespace production", "rm -rf /tmp/x"]
    assert blocked_commands(commands) == commands[1:]


def test_repeated_tool_loop_is_stopped() -> None:
    budget = ToolBudget(maximum=20, repeated_limit=2)
    budget.record("logs")
    budget.record("logs")
    with pytest.raises(RuntimeError, match="Loop detected"):
        budget.record("logs")


def test_no_llm_incident_path_still_queues_notification_and_ticket(
    settings: Settings, ledger: SignedLedger
) -> None:
    incident = IncidentRequest(
        incident_id="INC-1",
        service="api",
        alert="Elevated error rate detected",
        logs=["ERROR upstream timeout"],
    )
    response = IncidentService(settings, ledger).respond(incident, use_llm=False)
    assert response.notification_queued
    assert response.ticket_queued
    assert response.assessment.confidence == 0
    assert response.agenomic_run_id
    assert response.agenomic_trace_id
    assert [event.action for event in ledger.list_run(response.run_id)] == [
        "alert_received",
        "notification_queued",
        "ticket_queued",
    ]
