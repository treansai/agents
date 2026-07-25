from agenomic_agents.claims.models import ClaimDocument, ClaimRequest
from agenomic_agents.claims.service import ClaimsService
from agenomic_agents.common.agenomic import AgenomicRuntime
from agenomic_agents.common.config import Settings
from agenomic_agents.common.ledger import SignedLedger


async def test_agenomic_atep_trace_is_signed_and_correlated(
    settings: Settings, ledger: SignedLedger
) -> None:
    runtime = AgenomicRuntime(settings)
    claim = ClaimRequest(
        claim_id="CLM-ATEP",
        claimant_id="USR-PRIVATE",
        category="medical",
        amount=100,
        description="A private medical reimbursement description.",
        documents=[
            ClaimDocument(document_id="doc-1", kind="invoice", verified=True),
            ClaimDocument(document_id="doc-2", kind="medical_report", verified=True),
        ],
    )
    try:
        result = await ClaimsService(settings, ledger, runtime).review(claim, use_llm=False)
        traces = runtime.find_by_domain_run(result.run_id)
        verification = runtime.verify()
    finally:
        runtime.close()

    assert len(traces) == 1
    assert traces[0]["run_id"] == result.agenomic_run_id
    assert traces[0]["trace_id"] == result.agenomic_trace_id
    assert "USR-PRIVATE" not in str(traces[0])
    assert verification["claims"]["ok"] is True
    assert verification["claims"]["events_checked"] == 1
