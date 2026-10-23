"""Step 2: label each segment with a clause type using keyword scores. Plain code, no LLM."""
import re
from dataclasses import dataclass, field
from functools import lru_cache

from app.schemas.playbook import ClauseDefinition, Playbook
from app.schemas.review import ReviewWarning
from app.services.segmenter import Segment


@dataclass
class ClauseMatch:
    clause_type: str
    status: str  # found | not_found | ambiguous
    segment: Segment | None = None
    score: int = 0
    candidates: int = 0


@dataclass
class Classification:
    matches: dict[str, ClauseMatch]
    unmatched: list[Segment] = field(default_factory=list)
    warnings: list[ReviewWarning] = field(default_factory=list)


@lru_cache(maxsize=512)
def _keyword(keyword: str) -> re.Pattern[str]:
    # Matches at a word start so "confidential" also hits "Confidentiality".
    return re.compile(r"\b" + re.escape(keyword), re.IGNORECASE)


def _score(segment: Segment, definition: ClauseDefinition, title_weight: int, body_weight: int) -> int:
    score = sum(title_weight for kw in definition.title_keywords if _keyword(kw).search(segment.title))
    score += sum(body_weight for kw in definition.body_keywords if _keyword(kw).search(segment.body))
    return score


def classify_segments(segments: list[Segment], playbook: Playbook) -> Classification:
    cfg = playbook.classification
    definitions = [d for d in playbook.clause_catalog if d.clause_type != "other"]

    candidates: dict[str, list[tuple[str, int, Segment]]] = {d.clause_type: [] for d in definitions}
    unmatched: list[Segment] = []

    for segment in segments:
        scores = {d.clause_type: _score(segment, d, cfg.title_weight, cfg.body_weight) for d in definitions}
        ranked = sorted(scores.items(), key=lambda item: (-item[1], item[0]))
        best_type, best = ranked[0]
        runner_up = ranked[1][1] if len(ranked) > 1 else 0
        if best == 0:
            unmatched.append(segment)
            continue
        clear = best >= cfg.min_score and (best - runner_up) >= cfg.margin
        candidates[best_type].append(("found" if clear else "ambiguous", best, segment))

    result = Classification(matches={}, unmatched=unmatched)

    for definition in definitions:
        options = candidates[definition.clause_type]
        # Clear matches first, then highest score, then document order.
        options.sort(key=lambda o: (o[0] != "found", -o[1], o[2].index))
        if not options:
            result.matches[definition.clause_type] = ClauseMatch(definition.clause_type, "not_found")
            continue
        status, score, segment = options[0]
        result.matches[definition.clause_type] = ClauseMatch(
            definition.clause_type, status, segment, score, len(options)
        )
        found_count = sum(1 for o in options if o[0] == "found")
        if found_count > 1:
            result.warnings.append(
                ReviewWarning(
                    code="MULTIPLE_CLAUSES",
                    message=(
                        f"{found_count} clauses look like '{definition.clause_type}'; "
                        f"using {segment.label} (highest score). Check the others manually."
                    ),
                )
            )
        if status == "ambiguous":
            result.warnings.append(
                ReviewWarning(
                    code="AMBIGUOUS_CLAUSE",
                    message=f"{segment.label} may be a '{definition.clause_type}' clause but the match is weak.",
                )
            )

    if any(d.clause_type == "other" for d in playbook.clause_catalog):
        if unmatched:
            result.matches["other"] = ClauseMatch("other", "found", unmatched[0], 0, len(unmatched))
        else:
            result.matches["other"] = ClauseMatch("other", "not_found")
    return result
