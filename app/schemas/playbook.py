from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

Outcome = Literal["pass", "deviation", "review_required", "not_applicable", "unable_to_evaluate"]
Severity = Literal["low", "medium", "high"]
CheckType = Literal["presence", "scope", "presence_and_policy", "coverage", "specificity"]


class ClauseDefinition(BaseModel):
    clause_type: str
    title_keywords: list[str] = Field(default_factory=list)
    body_keywords: list[str] = Field(default_factory=list)


class ClassificationSettings(BaseModel):
    title_weight: int = 3
    body_weight: int = 1
    # A clause is "found" only when its best score reaches min_score AND beats
    # the runner-up by margin. Otherwise it is "ambiguous".
    min_score: int = 2
    margin: int = 2


class Rule(BaseModel):
    rule_id: str
    title: str
    clause_type: str
    check_type: CheckType
    params: dict[str, Any] = Field(default_factory=dict)
    severity: Severity
    on_missing_clause: Outcome = "deviation"
    guidance: str
    # Plain-language question put to the LLM (only used for ambiguous clauses, or when
    # llm_on_review_required is true and the code check could not decide).
    llm_criterion: str | None = None
    llm_on_review_required: bool = False


class Playbook(BaseModel):
    playbook_id: str
    version: str
    name: str
    classification: ClassificationSettings = Field(default_factory=ClassificationSettings)
    clause_catalog: list[ClauseDefinition]
    rules: list[Rule]

    @model_validator(mode="after")
    def _validate_references(self) -> "Playbook":
        clause_types = [c.clause_type for c in self.clause_catalog]
        if len(set(clause_types)) != len(clause_types):
            raise ValueError("clause_catalog contains duplicate clause_type values")
        rule_ids = [r.rule_id for r in self.rules]
        if len(set(rule_ids)) != len(rule_ids):
            raise ValueError("rules contain duplicate rule_id values")
        unknown = sorted({r.clause_type for r in self.rules} - set(clause_types))
        if unknown:
            raise ValueError(f"rules reference clause types missing from clause_catalog: {unknown}")
        return self
