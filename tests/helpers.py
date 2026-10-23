import json
import tempfile
from pathlib import Path

from app.container import Container, build_container
from app.core.config import BASE_DIR, Settings
from app.repositories.document_repo import PageText, StoredDocument
from app.services.llm_client import LLMVerdict

PLAYBOOK_FILE = BASE_DIR / "config" / "playbooks" / "nda_baseline.json"


def make_container(resolver=None, min_months=None) -> Container:
    """Container with the sample NDA seeded. `min_months` overrides R3 in a temporary playbook copy."""
    if min_months is None:
        return build_container(Settings(), resolver)
    playbook = json.loads(PLAYBOOK_FILE.read_text(encoding="utf-8"))
    for rule in playbook["rules"]:
        if rule["rule_id"] == "R3":
            rule["params"]["min_months"] = min_months
    tmp = Path(tempfile.mkdtemp())
    (tmp / "playbook.json").write_text(json.dumps(playbook), encoding="utf-8")
    return build_container(Settings(playbook_dir=tmp), resolver)


def add_document(container: Container, document_id: str, text: str, status: str = "success") -> None:
    container.documents.add(
        StoredDocument(
            document_id=document_id,
            file_name=f"{document_id}.pdf",
            extraction_status=status,
            pages=[PageText(page=1, text=text)] if text is not None else [],
        )
    )


def outcomes(report) -> dict[str, str]:
    return {r.rule_id: r.outcome for r in report.rule_results}


class FakeResolver:
    configured = True
    model = "fake-model"
    prompt_version = "test-1"

    def __init__(self, verdict: LLMVerdict | None = None, error: Exception | None = None):
        self.verdict, self.error, self.calls = verdict, error, 0

    def resolve(self, rule, clause_text):
        self.calls += 1
        if self.error:
            raise self.error
        return self.verdict


AMBIGUOUS_NDA = """Clause 1 — Purpose and confidentiality
The parties will talk about things.
Clause 2 — Duration
This agreement shall continue for two years.
"""

GOOD_NDA = """Clause 1 — Purpose
The Receiving Party may use the information only to evaluate a proposed transaction.
Clause 2 — Confidentiality
The Receiving Party shall not disclose any confidential information.
Clause 3 — Duration
This agreement shall continue for three (3) years.
Clause 4 — Return or Destruction
Upon request the Receiving Party shall return or destroy all materials.
Clause 5 — Governing Law
This agreement shall be governed by the laws of the State of California.
"""
