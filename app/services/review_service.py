import time
from typing import Any

from app.core.exceptions import ConflictError, NotFoundError, UnprocessableError
from app.repositories.document_repo import DocumentRepository
from app.repositories.playbook_repo import PlaybookRepository
from app.repositories.review_repo import InMemoryReviewRepository
from app.schemas.playbook import Playbook
from app.schemas.review import (
    ClauseResult,
    Evidence,
    Finding,
    ReviewMetadata,
    ReviewReport,
    ReviewSummary,
    RuleResult,
    SourceRef,
)
from app.services.classifier import Classification, classify_segments
from app.services.findings import build_findings
from app.services.llm_client import ClauseResolver
from app.services.rule_engine import evaluate_rules
from app.services.segmenter import segment_document
from app.utils.clock import utc_now_iso
from app.utils.ids import new_review_id
from app.utils.text import excerpt_in_text, first_sentence

_EXCERPT_LIMIT = 1000


class ReviewService:
    def __init__(
        self,
        documents: DocumentRepository,
        playbooks: PlaybookRepository,
        reviews: InMemoryReviewRepository,
        resolver: ClauseResolver,
    ):
        self._documents = documents
        self._playbooks = playbooks
        self._reviews = reviews
        self._resolver = resolver

    # ---- POST /api/reviews -----------------------------------------------------
    def create_review(self, document_id: str, playbook_id: str | None = None) -> ReviewReport:
        started = time.perf_counter()

        document = self._documents.get(document_id)
        if document is None:
            raise NotFoundError("DOCUMENT_NOT_FOUND", f"Document '{document_id}' was not found")
        if document.extraction_status != "success":
            raise ConflictError("DOCUMENT_NOT_READY", "Text extraction did not succeed for this document")

        playbook = self._playbooks.get(playbook_id)
        if playbook is None:
            raise NotFoundError("PLAYBOOK_NOT_FOUND", f"Playbook '{playbook_id}' was not found")

        doc_text = "\n".join(page.text for page in document.pages)
        if not doc_text.strip():
            raise UnprocessableError("DOCUMENT_TEXT_EMPTY", "The document has no extracted text to review")

        segments = segment_document(document.pages)
        if not segments:
            raise UnprocessableError(
                "NO_CLAUSES_DETECTED",
                "No clause headings (for example 'Clause 1', 'Section 2' or '3. Duration') were detected",
            )

        classification = classify_segments(segments, playbook)
        engine = evaluate_rules(playbook, classification.matches, doc_text, self._resolver)
        findings = build_findings(engine.rule_results, playbook)

        report = ReviewReport(
            metadata=ReviewMetadata(
                review_id=new_review_id(),
                document_id=document.document_id,
                file_name=document.file_name,
                playbook_id=playbook.playbook_id,
                playbook_version=playbook.version,
                status="completed",
                created_at=utc_now_iso(),
                duration_ms=int((time.perf_counter() - started) * 1000),
                model=self._resolver.model if engine.llm_calls else None,
                prompt_version=self._resolver.prompt_version if engine.llm_calls else None,
                llm_calls=engine.llm_calls,
                rules_resolved_without_llm=sum(1 for r in engine.rule_results if r.method == "deterministic"),
                summary=_summarize(engine.rule_results, findings),
            ),
            clauses=_clause_results(playbook, classification, doc_text),
            rule_results=engine.rule_results,
            findings=findings,
            warnings=[*classification.warnings, *engine.warnings],
        )
        self._reviews.add(report)
        return report

    # ---- GET /api/reviews/{id} -------------------------------------------------
    def get_review(self, review_id: str) -> ReviewReport:
        report = self._reviews.get(review_id)
        if report is None:
            raise NotFoundError("REVIEW_NOT_FOUND", f"Review '{review_id}' was not found")
        return report

    # ---- PATCH /api/reviews/{id}/findings/{findingId} --------------------------
    def update_finding(self, review_id: str, finding_id: str, changes: dict[str, Any]) -> Finding:
        self.get_review(review_id)  # 404 REVIEW_NOT_FOUND if missing
        updated = self._reviews.update_finding(review_id, finding_id, changes)
        if updated is None:
            raise NotFoundError("FINDING_NOT_FOUND", f"Finding '{finding_id}' was not found in review '{review_id}'")
        return updated


def _summarize(results: list[RuleResult], findings: list[Finding]) -> ReviewSummary:
    def count(outcome: str) -> int:
        return sum(1 for r in results if r.outcome == outcome)

    return ReviewSummary(
        rules_checked=len(results),
        passed=count("pass"),
        deviations=count("deviation"),
        review_required=count("review_required"),
        not_applicable=count("not_applicable"),
        unable_to_evaluate=count("unable_to_evaluate"),
        findings=len(findings),
    )


def _clause_results(playbook: Playbook, classification: Classification, doc_text: str) -> list[ClauseResult]:
    """One entry for every clause type in the playbook catalog, including not_found ones."""
    results: list[ClauseResult] = []
    for definition in playbook.clause_catalog:
        match = classification.matches[definition.clause_type]
        if match.segment is None:
            results.append(ClauseResult(clause_type=definition.clause_type, status="not_found"))
            continue
        segment = match.segment
        excerpt = segment.body[:_EXCERPT_LIMIT]
        summary = (
            f"{match.candidates} clause(s) did not match any known clause type."
            if definition.clause_type == "other"
            else first_sentence(segment.body)
        )
        results.append(
            ClauseResult(
                clause_type=definition.clause_type,
                status=match.status,
                heading=f"{segment.label} - {segment.title}" if segment.title else segment.label,
                summary=summary,
                evidence=Evidence(
                    excerpt=excerpt,
                    source=SourceRef(page=segment.heading_page, section=segment.label),
                    verified=excerpt_in_text(excerpt, doc_text),
                ),
            )
        )
    return results
