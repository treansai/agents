from enum import StrEnum

from pydantic import BaseModel, Field


class Severity(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class MetricSample(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    value: float
    unit: str = Field(default="", max_length=32)


class IncidentRequest(BaseModel):
    incident_id: str = Field(min_length=3, max_length=128)
    service: str = Field(min_length=2, max_length=128)
    alert: str = Field(min_length=5, max_length=5_000)
    logs: list[str] = Field(default_factory=list, max_length=500)
    metrics: list[MetricSample] = Field(default_factory=list, max_length=200)
    environment: str = Field(default="production", max_length=64)


class IncidentAssessment(BaseModel):
    root_cause: str = Field(min_length=3, max_length=5_000)
    confidence: float = Field(ge=0, le=1)
    severity: Severity
    evidence: list[str] = Field(default_factory=list, max_length=30)
    recommended_actions: list[str] = Field(default_factory=list, max_length=20)
    proposed_commands: list[str] = Field(default_factory=list, max_length=20)
    notification_summary: str = Field(min_length=3, max_length=2_000)


class IncidentResponse(BaseModel):
    run_id: str
    agenomic_run_id: str
    agenomic_trace_id: str
    incident_id: str
    assessment: IncidentAssessment
    blocked_actions: list[str]
    notification_queued: bool
    ticket_queued: bool
    audit_signature: str
