"""Step 4 seam: the LLM is only asked about one ambiguous clause at a time.

Nothing here calls a model yet. `NoopResolver` is the default, so ambiguous clauses go to
human review. To add a provider later, implement `ClauseResolver` and pass it to
`build_container(...)` - no other code changes.
"""
from dataclasses import dataclass
from typing import Protocol

from app.schemas.playbook import Rule


@dataclass
class LLMVerdict:
    outcome: str  # pass | deviation | review_required
    reason: str
    quote: str  # exact sentence from the clause; the engine verifies it


class ClauseResolver(Protocol):
    configured: bool
    model: str | None
    prompt_version: str | None

    def resolve(self, rule: Rule, clause_text: str) -> LLMVerdict | None: ...


class NoopResolver:
    configured = False
    model = None
    prompt_version = None

    def resolve(self, rule: Rule, clause_text: str) -> LLMVerdict | None:
        return None
