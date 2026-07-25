from enum import StrEnum

from pydantic import BaseModel, Field, field_validator


class ClaimDecision(StrEnum):
    APPROVE = "approve"
    REJECT = "reject"
    REQUEST_DOCUMENTS = "request_documents"
    HUMAN_REVIEW = "human_review"


class ClaimDocument(BaseModel):
    document_id: str = Field(min_length=1, max_length=128)
    kind: str = Field(min_length=1, max_length=64)
    verified: bool = False
    checksum_sha256: str | None = Field(default=None, pattern=r"^[a-fA-F0-9]{64}$")


class ClaimSignals(BaseModel):
    identity_match: bool = True
    amount_anomaly: bool = False
    duplicate_claim: bool = False
    provider_mismatch: bool = False
    prior_claims_12m: int = Field(default=0, ge=0, le=1000)


class HumanApproval(BaseModel):
    reviewer_id: str = Field(min_length=3, max_length=128)
    approved: bool
    reason: str = Field(min_length=3, max_length=1000)


class ClaimRequest(BaseModel):
    claim_id: str = Field(min_length=3, max_length=128)
    claimant_id: str = Field(min_length=3, max_length=128)
    category: str = Field(min_length=2, max_length=64)
    amount: float = Field(gt=0, le=10_000_000)
    currency: str = Field(default="EUR", pattern=r"^[A-Z]{3}$")
    description: str = Field(min_length=10, max_length=10_000)
    documents: list[ClaimDocument] = Field(default_factory=list, max_length=50)
    signals: ClaimSignals = Field(default_factory=ClaimSignals)
    human_approval: HumanApproval | None = None

    @field_validator("category")
    @classmethod
    def normalize_category(cls, value: str) -> str:
        return value.strip().lower()


class ClaimReview(BaseModel):
    run_id: str
    agenomic_run_id: str
    agenomic_trace_id: str
    claim_id: str
    decision: ClaimDecision
    risk_score: float = Field(ge=0, le=1)
    reasons: list[str]
    missing_documents: list[str] = Field(default_factory=list)
    human_approval_required: bool
    model_analysis: str | None = None
    policy_version: str = "claims-policy-2026-01"
    audit_signature: str
