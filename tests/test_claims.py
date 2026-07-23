import pytest

from agenomic_agents.claims.models import ClaimDecision, ClaimDocument, ClaimRequest, ClaimSignals
from agenomic_agents.claims.policy import evaluate_claim
from agenomic_agents.claims.service import ClaimsService
from agenomic_agents.common.config import Settings
from agenomic_agents.common.ledger import SignedLedger


def _claim(**updates):  # type: ignore[no-untyped-def]
    values = {
        "claim_id": "CLM-001",
        "claimant_id": "USR-001",
        "category": "medical",
        "amount": 1200,
        "description": "Emergency medical treatment reimbursement request.",
        "documents": [
            ClaimDocument(document_id="1", kind="invoice", verified=True),
            ClaimDocument(document_id="2", kind="medical_report", verified=True),
        ],
    }
    values.update(updates)
    return ClaimRequest(**values)


def test_normal_claim_is_approved() -> None:
    result = evaluate_claim(_claim())
    assert result.decision == ClaimDecision.APPROVE
    assert result.risk_score < 0.65


def test_missing_document_is_requested() -> None:
    result = evaluate_claim(_claim(documents=[]))
    assert result.decision == ClaimDecision.REQUEST_DOCUMENTS
    assert result.missing_documents == ["invoice", "medical_report"]


def test_duplicate_claim_is_rejected() -> None:
    result = evaluate_claim(_claim(signals=ClaimSignals(duplicate_claim=True)))
    assert result.decision == ClaimDecision.REJECT


def test_high_value_claim_cannot_auto_approve() -> None:
    result = evaluate_claim(_claim(amount=75_000))
    assert result.decision == ClaimDecision.HUMAN_REVIEW
    assert result.human_approval_required


@pytest.mark.asyncio
async def test_service_no_llm_writes_signed_audit_events(
    settings: Settings, ledger: SignedLedger
) -> None:
    response = await ClaimsService(settings, ledger).review(_claim(), use_llm=False)
    assert response.decision == ClaimDecision.APPROVE
    assert response.model_analysis is None
    assert response.audit_signature
    assert [event.action for event in ledger.list_run(response.run_id)] == [
        "claim_received",
        "decision_enforced",
    ]
