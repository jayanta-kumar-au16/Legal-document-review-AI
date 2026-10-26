from app.schemas.playbook import Playbook
from app.schemas.review import Confidence, Finding, RuleResult

FINDING_OUTCOMES = {"deviation", "review_required"}


def _confidence(result: RuleResult) -> Confidence:
    if result.method == "llm":
        return "medium"
    if result.outcome == "review_required":
        return "low"
    # Deviation. A found clause with exact evidence is solid; a "missing clause"
    # claim rests on keywords, so it is less certain.
    return "high" if result.evidence is not None else "medium"


def build_findings(rule_results: list[RuleResult], playbook: Playbook) -> list[Finding]:
    """One finding per deviation / review_required result. Severity and advice come from the playbook."""
    guidance = {rule.rule_id: rule.guidance for rule in playbook.rules}
    findings: list[Finding] = []
    for result in rule_results:
        if result.outcome not in FINDING_OUTCOMES:
            continue
        findings.append(
            Finding(
                finding_id=f"F-{len(findings) + 1:03d}",
                rule_id=result.rule_id,
                clause_type=result.clause_type,
                severity=result.severity,
                description=f"{result.title}: {result.reason}",
                evidence=result.evidence,
                reason=result.reason,
                suggested_action=guidance[result.rule_id],
                confidence=_confidence(result),
                method=result.method,
            )
        )
    return findings
