from pydantic import BaseModel, Field, HttpUrl, model_validator


class SourceDocument(BaseModel):
    source_id: str = Field(min_length=1, max_length=128)
    title: str = Field(min_length=1, max_length=500)
    url: HttpUrl | None = None
    content: str = Field(min_length=20, max_length=50_000)
    published_at: str | None = Field(default=None, max_length=64)


class ResearchRequest(BaseModel):
    report_id: str = Field(min_length=3, max_length=128)
    topic: str = Field(min_length=5, max_length=2_000)
    jurisdiction: str = Field(default="EU", min_length=2, max_length=128)
    policy_requirements: list[str] = Field(default_factory=list, max_length=50)
    sources: list[SourceDocument] = Field(min_length=1, max_length=50)

    @model_validator(mode="after")
    def unique_source_ids(self) -> "ResearchRequest":
        ids = [source.source_id for source in self.sources]
        if len(ids) != len(set(ids)):
            raise ValueError("source_id values must be unique")
        return self


class Finding(BaseModel):
    statement: str = Field(min_length=3, max_length=3_000)
    source_ids: list[str] = Field(min_length=1, max_length=20)


class ResearchFindings(BaseModel):
    findings: list[Finding] = Field(default_factory=list, max_length=50)
    unanswered_questions: list[str] = Field(default_factory=list, max_length=20)


class SourceCheck(BaseModel):
    source_id: str
    usable: bool
    concerns: list[str] = Field(default_factory=list)


class VerificationResult(BaseModel):
    checks: list[SourceCheck]


class AnalysisResult(BaseModel):
    executive_summary: str = Field(min_length=10, max_length=10_000)
    claims: list[Finding] = Field(default_factory=list, max_length=50)
    limitations: list[str] = Field(default_factory=list, max_length=30)


class ComplianceResult(BaseModel):
    allowed: bool
    risky_claims: list[str] = Field(default_factory=list, max_length=30)
    required_changes: list[str] = Field(default_factory=list, max_length=30)
    rationale: str = Field(min_length=3, max_length=5_000)


class EvidenceItem(BaseModel):
    claim: str
    source_ids: list[str]


class EvidencePackage(BaseModel):
    items: list[EvidenceItem] = Field(default_factory=list)
    coverage: float = Field(ge=0, le=1)


class FinalReport(BaseModel):
    title: str = Field(min_length=3, max_length=500)
    markdown: str = Field(min_length=10, max_length=50_000)
    cited_source_ids: list[str] = Field(default_factory=list)
    blocked: bool = False
    compliance_notes: list[str] = Field(default_factory=list)


class ResearchResponse(BaseModel):
    run_id: str
    agenomic_run_id: str
    agenomic_trace_id: str
    report_id: str
    report: FinalReport
    evidence: EvidencePackage
    audit_signature: str
