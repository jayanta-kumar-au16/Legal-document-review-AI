"""Step 3: one small function per check_type. Pure code, no LLM, fully repeatable.

Each check receives the clause segment and the rule's `params` from the playbook JSON,
and returns an outcome, a reason, and the exact sentence it relied on.
"""
import re
from dataclasses import dataclass
from typing import Callable

from app.services.segmenter import Segment
from app.utils.numbers import find_durations
from app.utils.text import find_term, sentence_at


@dataclass
class CheckResult:
    outcome: str
    reason: str
    evidence: str | None = None
    offset: int = 0  # start of `evidence` inside segment.body


def _at(segment: Segment, position: int) -> tuple[str, int]:
    return sentence_at(segment.body, position)


def check_presence(segment: Segment, params: dict) -> CheckResult:
    """Clause must contain at least one of the required wording patterns (regexes)."""
    for pattern in params.get("required_patterns", []):
        match = re.search(pattern, segment.body, re.IGNORECASE)
        if match:
            sentence, offset = _at(segment, match.start())
            return CheckResult("pass", f"Required wording found: '{match.group(0)}'.", sentence, offset)
    sentence, offset = _at(segment, 0)
    return CheckResult(
        params.get("on_no_match", "review_required"),
        "The clause was found but none of the required wording was detected.",
        sentence,
        offset,
    )


def check_scope(segment: Segment, params: dict) -> CheckResult:
    """Use must be limited ('solely', 'only') to a stated purpose, and not broadly worded."""
    body = segment.body
    broad = find_term(body, params.get("broad_terms", []))
    if broad:
        sentence, offset = _at(segment, broad.start())
        return CheckResult("deviation", f"Broad use wording found: '{broad.group(0)}'.", sentence, offset)

    limiting = find_term(body, params.get("limiting_terms", []))
    purpose = find_term(body, params.get("purpose_terms", []))
    if limiting and purpose:
        sentence, offset = _at(segment, limiting.start())
        return CheckResult(
            "pass", f"Use is limited ('{limiting.group(0)}') to a stated purpose ('{purpose.group(0)}').", sentence, offset
        )
    anchor = limiting or purpose
    sentence, offset = _at(segment, anchor.start() if anchor else 0)
    missing = "a limiting word such as 'solely'" if not limiting else "a clearly stated purpose"
    return CheckResult("review_required", f"Could not confirm the use is limited: {missing} was not detected.", sentence, offset)


def check_presence_and_policy(segment: Segment, params: dict) -> CheckResult:
    """Read a duration, convert to months, compare with params.min_months (if configured)."""
    durations = find_durations(segment.body)
    if not durations:
        sentence, offset = _at(segment, 0)
        return CheckResult("review_required", "No fixed duration (years/months) could be read from the clause.", sentence, offset)

    distinct = sorted({d.months for d in durations})
    first = durations[0]
    sentence, offset = _at(segment, first.start)
    if len(distinct) > 1:
        values = ", ".join(f"{m} months" for m in distinct)
        return CheckResult("review_required", f"Several different durations were found ({values}).", sentence, offset)

    months = first.months
    minimum = params.get("min_months")
    if minimum is None:
        return CheckResult("review_required", f"Duration is {months} months; no minimum is configured in the playbook.", sentence, offset)
    if months >= minimum:
        return CheckResult("pass", f"Duration of {months} months meets the {minimum}-month minimum.", sentence, offset)
    return CheckResult("deviation", f"Duration of {months} months is below the {minimum}-month minimum.", sentence, offset)


def check_coverage(segment: Segment, params: dict) -> CheckResult:
    """Every group of terms must be addressed (each group is satisfied by any one of its terms)."""
    groups: list[list[str]] = params.get("required_groups", [])
    satisfied, missing = [], []
    for group in groups:
        match = find_term(segment.body, group)
        (satisfied if match else missing).append((group, match))

    if not missing:
        match = satisfied[0][1] if satisfied else None
        sentence, offset = _at(segment, match.start() if match else 0)
        return CheckResult("pass", "All required items are addressed.", sentence, offset)

    first_hit = next((m for _, m in satisfied if m), None)
    sentence, offset = _at(segment, first_hit.start() if first_hit else 0)
    names = "; ".join("/".join(group[:3]) for group, _ in missing)
    return CheckResult("deviation", f"Not addressed in the clause: {names}.", sentence, offset)


def check_specificity(segment: Segment, params: dict) -> CheckResult:
    """A named jurisdiction (from params.jurisdictions) must appear; 'applicable law' alone is not enough."""
    match = find_term(segment.body, params.get("jurisdictions", []))
    if match:
        sentence, offset = _at(segment, match.start())
        return CheckResult("pass", f"A specific jurisdiction is named: {match.group(0)}.", sentence, offset)
    sentence, offset = _at(segment, 0)
    return CheckResult("deviation", "No specific jurisdiction is named; the clause is generic.", sentence, offset)


CHECKS: dict[str, Callable[[Segment, dict], CheckResult]] = {
    "presence": check_presence,
    "scope": check_scope,
    "presence_and_policy": check_presence_and_policy,
    "coverage": check_coverage,
    "specificity": check_specificity,
}
