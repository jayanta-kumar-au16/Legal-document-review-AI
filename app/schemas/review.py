from typing import Literal

from pydantic import BaseModel, Field, model_validator

from app.schemas.playbook import CheckType, Outcome, Severity

ClauseStatus = Literal["found", "not_found", "ambiguous"]
Method = Literal["deterministic", "llm"]
Confidence = Literal["high", "medium", "low"]
DecisionStatus = Literal["pending", "accepted", "dismissed"]


class SourceRef(BaseModel):
    page: int | None = None
    section: str | None = None


class Evidence(BaseModel):
    excerpt: str
    source: SourceRef
    verified: bool


class ClauseResult(BaseModel):
    clause_type: str
    status: ClauseStatus
    heading: str | None = None
    summary: str | None = None
    evidence: Evidence | None = None


class RuleResult(BaseModel):
    rule_id: str
    title: str
    clause_type: str
    check_type: CheckType
    outcome: Outcome
    severity: Severity
    reason: str
    method: Method
    evidence: Evidence | None = None


class Finding(BaseModel):
    finding_id: str
    rule_id: str
    clause_type: str
    severity: Severity
    description: str
    evidence: Evidence | None = None
    reason: str
    suggested_action: str
    confidence: Confidence
    method: Method
    status: DecisionStatus = "pending"
    comment: str | None = None
    updated_at: str | None = None


class ReviewWarning(BaseModel):
    code: str
    message: str
    rule_id: str | None = None


class ReviewSummary(BaseModel):
    rules_checked: int
    passed: int
    deviations: int
    review_required: int
    not_applicable: int
    unable_to_evaluate: int
    findings: int


class ReviewMetadata(BaseModel):
    review_id: str
    document_id: str
    file_name: str
    playbook_id: str
    playbook_version: str
    status: Literal["completed"]
    created_at: str
    duration_ms: int
    model: str | None = None
    prompt_version: str | None = None
    llm_calls: int = 0
    rules_resolved_without_llm: int
    summary: ReviewSummary


class ReviewReport(BaseModel):
    metadata: ReviewMetadata
    clauses: list[ClauseResult]
    rule_results: list[RuleResult]
    findings: list[Finding]
    warnings: list[ReviewWarning]


# ---- request / response payloads -------------------------------------------------


class ReviewCreateRequest(BaseModel):
    document_id: str = Field(min_length=1, max_length=100)
    # Omit to use the default playbook.
    playbook_id: str | None = Field(default=None, min_length=1, max_length=100)


class ReviewCreatedData(BaseModel):
    review_id: str
    status: str
    playbook_version: str


class FindingUpdateRequest(BaseModel):
    status: DecisionStatus | None = None
    comment: str | None = Field(default=None, max_length=2000)

    @model_validator(mode="after")
    def _require_a_change(self) -> "FindingUpdateRequest":
        provided = self.model_fields_set & {"status", "comment"}
        if not provided:
            raise ValueError("Provide at least one of 'status' or 'comment'")
        if "status" in provided and self.status is None:
            raise ValueError("'status' cannot be null")
        return self


class FindingUpdateData(BaseModel):
    finding_id: str
    status: DecisionStatus
    comment: str | None = None
