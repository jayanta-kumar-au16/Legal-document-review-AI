"""Run every playbook rule against the classified clauses. One result per rule, always."""
import logging
from dataclasses import dataclass, field

from app.schemas.playbook import Playbook, Rule
from app.schemas.review import Evidence, ReviewWarning, RuleResult, SourceRef
from app.services.checks import CHECKS
from app.services.classifier import ClauseMatch
from app.services.llm_client import ClauseResolver
from app.services.segmenter import Segment
from app.utils.text import excerpt_in_text, sentence_at

logger = logging.getLogger("nda_review_api")

_LLM_OUTCOMES = {"pass", "deviation", "review_required"}


@dataclass
class EngineOutput:
    rule_results: list[RuleResult] = field(default_factory=list)
    warnings: list[ReviewWarning] = field(default_factory=list)
    llm_calls: int = 0


def _evidence(segment: Segment, text: str, offset: int, doc_text: str) -> Evidence:
    return Evidence(
        excerpt=text,
        source=SourceRef(page=segment.page_at(offset), section=segment.label),
        verified=excerpt_in_text(text, doc_text),
    )


def _result(rule: Rule, outcome: str, reason: str, method: str, evidence: Evidence | None) -> RuleResult:
    return RuleResult(
        rule_id=rule.rule_id,
        title=rule.title,
        clause_type=rule.clause_type,
        check_type=rule.check_type,
        outcome=outcome,
        severity=rule.severity,
        reason=reason,
        method=method,
        evidence=evidence,
    )


def _ask_llm(
    rule: Rule, segment: Segment, doc_text: str, resolver: ClauseResolver, out: EngineOutput
) -> RuleResult | None:
    """Ask the LLM about this one clause. Returns a verified result, or None after recording a warning."""

    def warn(code: str, message: str) -> None:
        out.warnings.append(ReviewWarning(code=code, message=f"{rule.rule_id}: {message}", rule_id=rule.rule_id))

    out.llm_calls += 1
    try:
        verdict = resolver.resolve(rule, segment.body)
    except Exception as exc:  # timeout, connection refused, provider error: never fail silently
        logger.error("LLM fallback failed for %s: %s", rule.rule_id, type(exc).__name__)
        warn("LLM_ERROR", f"LLM call failed ({type(exc).__name__}).")
        return None

    if verdict is None or verdict.outcome not in _LLM_OUTCOMES:
        warn("LLM_INVALID_OUTPUT", "LLM output was missing or not an allowed outcome.")
        return None
    if not excerpt_in_text(verdict.quote, segment.body):
        warn("EVIDENCE_UNVERIFIED", "LLM evidence could not be verified against the document.")
        return None

    position = max(segment.body.find(verdict.quote), 0)
    return _result(rule, verdict.outcome, verdict.reason, "llm", _evidence(segment, verdict.quote, position, doc_text))


def _needs_human(rule: Rule, segment: Segment, doc_text: str, reason: str) -> RuleResult:
    text, offset = sentence_at(segment.body, 0)
    return _result(rule, "review_required", reason, "deterministic", _evidence(segment, text, offset, doc_text))


def evaluate_rules(
    playbook: Playbook, matches: dict[str, ClauseMatch], doc_text: str, resolver: ClauseResolver
) -> EngineOutput:
    out = EngineOutput()
    for rule in playbook.rules:
        match = matches[rule.clause_type]

        if match.status == "not_found":
            result = _result(
                rule, rule.on_missing_clause, f"No '{rule.clause_type}' clause was found in the document.",
                "deterministic", None,
            )

        elif match.status == "ambiguous":
            llm_result = None
            if resolver.configured:
                llm_result = _ask_llm(rule, match.segment, doc_text, resolver, out)
            else:
                out.warnings.append(
                    ReviewWarning(
                        code="LLM_NOT_CONFIGURED",
                        message=f"{rule.rule_id}: clause is ambiguous and no LLM fallback is configured.",
                        rule_id=rule.rule_id,
                    )
                )
            if llm_result is not None:
                result = llm_result
            else:
                reason = (
                    "The clause match is ambiguous, so it was sent to human review."
                    if not resolver.configured
                    else "The LLM could not give a verified answer, so this rule needs human review."
                )
                result = _needs_human(rule, match.segment, doc_text, reason)

        else:
            check = CHECKS[rule.check_type](match.segment, rule.params)
            evidence = (
                _evidence(match.segment, check.evidence, check.offset, doc_text) if check.evidence else None
            )
            result = _result(rule, check.outcome, check.reason, "deterministic", evidence)
            # The code check could not decide (e.g. unusual wording): let the LLM read this one clause.
            if check.outcome == "review_required" and rule.llm_on_review_required and resolver.configured:
                result = _ask_llm(rule, match.segment, doc_text, resolver, out) or result

        if result.outcome == "unable_to_evaluate":
            out.warnings.append(
                ReviewWarning(code="RULE_UNABLE_TO_EVALUATE", message=f"{rule.rule_id} could not be evaluated.", rule_id=rule.rule_id)
            )
        if result.evidence is not None and not result.evidence.verified:
            out.warnings.append(
                ReviewWarning(code="EVIDENCE_UNVERIFIED", message=f"{rule.rule_id}: evidence text was not found in the document.", rule_id=rule.rule_id)
            )
        out.rule_results.append(result)
    return out
