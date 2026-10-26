import re
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

Outcome = Literal["pass", "deviation", "review_required", "not_applicable", "unable_to_evaluate"]
Severity = Literal["low", "medium", "high"]
CheckType = Literal["presence", "scope", "presence_and_policy", "coverage", "specificity"]

# Playbooks can be uploaded, so every list and string is bounded.
ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$"
MAX_KEYWORDS = 300
MAX_KEYWORD_LEN = 120
MAX_REGEX_LEN = 300

# Best-effort guard against catastrophic-backtracking regexes such as (a+)+ or (\s*)*.
_NESTED_QUANTIFIER = re.compile(r"\((?:[^()\\]|\\.)*[+*](?:[^()\\]|\\.)*\)\s*(?:[+*]|\{\d)")


def _keyword_list(name: str, value: Any, *, required: bool) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(v, str) and v.strip() for v in value):
        raise ValueError(f"params.{name} must be a list of non-empty strings")
    if required and not value:
        raise ValueError(f"params.{name} must not be empty")
    if len(value) > MAX_KEYWORDS or any(len(v) > MAX_KEYWORD_LEN for v in value):
        raise ValueError(f"params.{name} is too large (max {MAX_KEYWORDS} items of {MAX_KEYWORD_LEN} characters)")
    return value


def validate_rule_params(check_type: str, params: dict[str, Any]) -> None:
    """Fail at load time, with a clear message, instead of crashing during a review."""
    if check_type == "presence":
        patterns = _keyword_list("required_patterns", params.get("required_patterns"), required=True)
        for pattern in patterns:
            if len(pattern) > MAX_REGEX_LEN:
                raise ValueError(f"params.required_patterns: a pattern is longer than {MAX_REGEX_LEN} characters")
            if _NESTED_QUANTIFIER.search(pattern):
                raise ValueError(f"params.required_patterns: pattern '{pattern}' has nested repetition, which is not allowed")
            try:
                re.compile(pattern, re.IGNORECASE)
            except re.error as exc:
                raise ValueError(f"params.required_patterns: '{pattern}' is not a valid regular expression ({exc})") from None
        if "on_no_match" in params and params["on_no_match"] not in ("deviation", "review_required"):
            raise ValueError("params.on_no_match must be 'deviation' or 'review_required'")
    elif check_type == "scope":
        for name in ("limiting_terms", "purpose_terms", "broad_terms"):
            if name in params:
                _keyword_list(name, params[name], required=False)
    elif check_type == "presence_and_policy":
        minimum = params.get("min_months")
        if minimum is not None and (isinstance(minimum, bool) or not isinstance(minimum, int) or not 1 <= minimum <= 1200):
            raise ValueError("params.min_months must be null or a whole number of months between 1 and 1200")
    elif check_type == "coverage":
        groups = params.get("required_groups")
        if not isinstance(groups, list) or not groups:
            raise ValueError("params.required_groups must be a non-empty list of term lists")
        for group in groups:
            _keyword_list("required_groups[]", group, required=True)
    elif check_type == "specificity":
        _keyword_list("jurisdictions", params.get("jurisdictions"), required=True)


class ClauseDefinition(BaseModel):
    clause_type: str = Field(min_length=1, max_length=64, pattern=r"^[a-z][a-z0-9_]*$")
    title_keywords: list[str] = Field(default_factory=list, max_length=MAX_KEYWORDS)
    body_keywords: list[str] = Field(default_factory=list, max_length=MAX_KEYWORDS)

    @model_validator(mode="after")
    def _keywords_are_sane(self) -> "ClauseDefinition":
        for keyword in [*self.title_keywords, *self.body_keywords]:
            if not keyword.strip() or len(keyword) > MAX_KEYWORD_LEN:
                raise ValueError(f"keywords must be 1-{MAX_KEYWORD_LEN} characters (clause '{self.clause_type}')")
        return self


class ClassificationSettings(BaseModel):
    title_weight: int = Field(default=3, ge=0, le=100)
    body_weight: int = Field(default=1, ge=0, le=100)
    # A clause is "found" only when its best score reaches min_score AND beats
    # the runner-up by margin. Otherwise it is "ambiguous".
    min_score: int = Field(default=2, ge=0, le=1000)
    margin: int = Field(default=2, ge=0, le=1000)


class Rule(BaseModel):
    rule_id: str = Field(pattern=ID_PATTERN)
    title: str = Field(min_length=1, max_length=200)
    clause_type: str
    check_type: CheckType
    params: dict[str, Any] = Field(default_factory=dict)
    severity: Severity
    on_missing_clause: Outcome = "deviation"
    guidance: str = Field(min_length=1, max_length=1000)
    # Plain-language question put to the LLM (only used for ambiguous clauses, or when
    # llm_on_review_required is true and the code check could not decide).
    llm_criterion: str | None = Field(default=None, max_length=500)
    llm_on_review_required: bool = False

    @model_validator(mode="after")
    def _params_match_check_type(self) -> "Rule":
        try:
            validate_rule_params(self.check_type, self.params)
        except ValueError as exc:
            raise ValueError(f"rule {self.rule_id}: {exc}") from None
        return self


class Playbook(BaseModel):
    playbook_id: str = Field(pattern=ID_PATTERN)
    version: str = Field(min_length=1, max_length=32)
    name: str = Field(min_length=1, max_length=200)
    classification: ClassificationSettings = Field(default_factory=ClassificationSettings)
    clause_catalog: list[ClauseDefinition] = Field(min_length=1, max_length=40)
    rules: list[Rule] = Field(min_length=1, max_length=60)

    @model_validator(mode="after")
    def _validate_references(self) -> "Playbook":
        clause_types = [c.clause_type for c in self.clause_catalog]
        if len(set(clause_types)) != len(clause_types):
            raise ValueError("clause_catalog contains duplicate clause_type values")
        if not any(t != "other" for t in clause_types):
            raise ValueError("clause_catalog needs at least one clause type other than 'other'")
        rule_ids = [r.rule_id for r in self.rules]
        if len(set(rule_ids)) != len(rule_ids):
            raise ValueError("rules contain duplicate rule_id values")
        unknown = sorted({r.clause_type for r in self.rules} - set(clause_types))
        if unknown:
            raise ValueError(f"rules reference clause types missing from clause_catalog: {unknown}")
        return self
