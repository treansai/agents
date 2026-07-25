from dataclasses import dataclass

from agenomic_agents.claims.models import ClaimDecision, ClaimRequest

REQUIRED_DOCUMENTS: dict[str, set[str]] = {
    "medical": {"invoice", "medical_report"},
    "property": {"invoice", "incident_report", "proof_of_ownership"},
    "travel": {"invoice", "booking_confirmation"},
    "vehicle": {"invoice", "incident_report", "registration"},
}


@dataclass(frozen=True)
class PolicyEvaluation:
    decision: ClaimDecision
    risk_score: float
    reasons: list[str]
    missing_documents: list[str]
    human_approval_required: bool


def evaluate_claim(claim: ClaimRequest, human_review_threshold: float = 0.65) -> PolicyEvaluation:
    """Deterministic policy gate. An LLM can explain but cannot override this result."""
    required = REQUIRED_DOCUMENTS.get(claim.category, {"invoice", "incident_report"})
    verified_kinds = {document.kind for document in claim.documents if document.verified}
    missing = sorted(required - verified_kinds)
    if missing:
        return PolicyEvaluation(
            decision=ClaimDecision.REQUEST_DOCUMENTS,
            risk_score=0.35,
            reasons=["Required verified documents are missing"],
            missing_documents=missing,
            human_approval_required=False,
        )

    score = 0.05
    reasons: list[str] = []
    signals = claim.signals
    factors = [
        (not signals.identity_match, 0.45, "Claimant identity does not match"),
        (signals.duplicate_claim, 0.55, "Potential duplicate claim"),
        (signals.amount_anomaly, 0.30, "Claim amount is anomalous"),
        (signals.provider_mismatch, 0.25, "Provider information is inconsistent"),
        (signals.prior_claims_12m >= 5, 0.15, "High recent claim frequency"),
        (claim.amount >= 50_000, 0.20, "High-value claim"),
    ]
    for active, weight, reason in factors:
        if active:
            score += weight
            reasons.append(reason)
    score = min(round(score, 4), 1.0)

    if signals.duplicate_claim or not signals.identity_match:
        decision = ClaimDecision.REJECT
        reasons.append("A hard fraud control was triggered")
        return PolicyEvaluation(decision, score, reasons, [], False)

    if score >= human_review_threshold or claim.amount >= 50_000:
        if claim.human_approval is None:
            return PolicyEvaluation(
                ClaimDecision.HUMAN_REVIEW,
                score,
                reasons or ["Risk threshold requires human review"],
                [],
                True,
            )
        if not claim.human_approval.approved:
            return PolicyEvaluation(
                ClaimDecision.REJECT,
                score,
                [*reasons, f"Human reviewer rejected: {claim.human_approval.reason}"],
                [],
                False,
            )
        reasons.append(f"Approved by human reviewer {claim.human_approval.reviewer_id}")

    return PolicyEvaluation(
        ClaimDecision.APPROVE,
        score,
        reasons or ["All required controls passed"],
        [],
        False,
    )
