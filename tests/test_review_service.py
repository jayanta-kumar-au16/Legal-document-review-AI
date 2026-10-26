import json
import tempfile
import unittest
from pathlib import Path

from app.core.config import Settings
from app.container import build_container
from app.core.exceptions import AppError, ConflictError, NotFoundError, PlaybookConfigError, UnprocessableError
from app.dev_seed import SAMPLE_DOCUMENT_ID
from app.services.llm_client import LLMVerdict
from tests.helpers import AMBIGUOUS_NDA, GOOD_NDA, FakeResolver, add_document, make_container, outcomes


class SampleNdaTests(unittest.TestCase):
    def setUp(self):
        self.container = make_container()
        self.service = self.container.review_service
        self.report = self.service.create_review(SAMPLE_DOCUMENT_ID)

    def test_expected_outcomes_from_the_spec(self):
        self.assertEqual(
            outcomes(self.report),
            {"R1": "pass", "R2": "pass", "R3": "review_required", "R4": "deviation", "R5": "deviation"},
        )

    def test_no_llm_needed_for_the_sample(self):
        meta = self.report.metadata
        self.assertEqual(meta.llm_calls, 0)
        self.assertEqual(meta.rules_resolved_without_llm, 5)
        self.assertIsNone(meta.model)

    def test_findings_only_for_deviation_and_review_required(self):
        self.assertEqual([f.rule_id for f in self.report.findings], ["R3", "R4", "R5"])
        self.assertTrue(all(f.status == "pending" for f in self.report.findings))
        self.assertEqual(self.report.metadata.summary.findings, 3)

    def test_every_clause_type_has_a_status(self):
        statuses = {c.clause_type: c.status for c in self.report.clauses}
        self.assertEqual(len(statuses), 9)
        self.assertEqual(statuses["duration"], "found")
        self.assertEqual(statuses["termination"], "not_found")

    def test_evidence_is_verified_and_carries_page_and_section(self):
        by_rule = {r.rule_id: r for r in self.report.rule_results}
        self.assertTrue(all(r.evidence.verified for r in by_rule.values()))
        self.assertEqual((by_rule["R1"].evidence.source.page, by_rule["R1"].evidence.source.section), (1, "Clause 2"))
        self.assertEqual((by_rule["R4"].evidence.source.page, by_rule["R4"].evidence.source.section), (2, "Clause 4"))

    def test_severity_and_advice_come_from_the_playbook(self):
        r5 = next(f for f in self.report.findings if f.rule_id == "R5")
        self.assertEqual(r5.severity, "medium")
        self.assertIn("jurisdiction", r5.suggested_action)

    def test_playbook_version_is_recorded(self):
        self.assertEqual(self.report.metadata.playbook_id, "NDA-BASELINE")
        self.assertEqual(self.report.metadata.playbook_version, "1.0")

    def test_same_input_gives_same_outcomes(self):
        again = self.service.create_review(SAMPLE_DOCUMENT_ID)
        self.assertEqual(outcomes(again), outcomes(self.report))
        self.assertNotEqual(again.metadata.review_id, self.report.metadata.review_id)


class PlaybookParameterTests(unittest.TestCase):
    def test_min_months_above_duration_gives_deviation(self):
        report = make_container(min_months=24).review_service.create_review(SAMPLE_DOCUMENT_ID)
        self.assertEqual(outcomes(report)["R3"], "deviation")

    def test_min_months_at_or_below_duration_gives_pass(self):
        report = make_container(min_months=12).review_service.create_review(SAMPLE_DOCUMENT_ID)
        self.assertEqual(outcomes(report)["R3"], "pass")

    def test_compliant_nda_passes_everything(self):
        container = make_container(min_months=24)
        add_document(container, "good", GOOD_NDA)
        report = container.review_service.create_review("good")
        self.assertEqual(set(outcomes(report).values()), {"pass"})
        self.assertEqual(report.findings, [])

    def test_missing_clause_uses_on_missing_clause_outcome(self):
        container = make_container()
        add_document(container, "partial", "Clause 1 — Purpose\nThe Receiving Party may use the data solely to evaluate a deal.\n")
        report = container.review_service.create_review("partial")
        result = outcomes(report)
        self.assertEqual(result["R2"], "pass")
        self.assertEqual(result["R1"], "deviation")  # confidentiality clause missing
        missing = next(r for r in report.rule_results if r.rule_id == "R1")
        self.assertIsNone(missing.evidence)
        finding = next(f for f in report.findings if f.rule_id == "R1")
        self.assertEqual(finding.confidence, "medium")

    def test_invalid_playbook_stops_startup_with_clear_error(self):
        tmp = Path(tempfile.mkdtemp())
        (tmp / "bad.json").write_text(json.dumps({"playbook_id": "X", "version": "1", "name": "x",
                                                  "clause_catalog": [], "rules": [
                                                      {"rule_id": "R1", "title": "t", "clause_type": "nope",
                                                       "check_type": "presence", "severity": "high", "guidance": "g"}]}))
        with self.assertRaises(PlaybookConfigError) as ctx:
            build_container(Settings(playbook_dir=tmp))
        self.assertIn("bad.json", str(ctx.exception))


class ErrorTests(unittest.TestCase):
    def setUp(self):
        self.container = make_container()
        self.service = self.container.review_service

    def assertAppError(self, exc_type, code, fn, *args):
        with self.assertRaises(exc_type) as ctx:
            fn(*args)
        self.assertEqual(ctx.exception.code, code)

    def test_unknown_document(self):
        self.assertAppError(NotFoundError, "DOCUMENT_NOT_FOUND", self.service.create_review, "missing")

    def test_unknown_playbook(self):
        self.assertAppError(NotFoundError, "PLAYBOOK_NOT_FOUND", self.service.create_review, SAMPLE_DOCUMENT_ID, "NOPE")

    def test_failed_extraction_is_not_reviewed(self):
        add_document(self.container, "bad", "", status="failed")
        self.assertAppError(ConflictError, "DOCUMENT_NOT_READY", self.service.create_review, "bad")

    def test_empty_text(self):
        add_document(self.container, "empty", "   \n  ")
        self.assertAppError(UnprocessableError, "DOCUMENT_TEXT_EMPTY", self.service.create_review, "empty")

    def test_no_headings_is_an_error_not_a_fake_review(self):
        add_document(self.container, "flat", "This agreement has no numbered clauses at all.\nJust paragraphs of text.")
        self.assertAppError(UnprocessableError, "NO_CLAUSES_DETECTED", self.service.create_review, "flat")

    def test_unknown_review(self):
        self.assertAppError(NotFoundError, "REVIEW_NOT_FOUND", self.service.get_review, "rev_missing")


class FindingUpdateTests(unittest.TestCase):
    def setUp(self):
        self.service = make_container().review_service
        self.report = self.service.create_review(SAMPLE_DOCUMENT_ID)
        self.review_id = self.report.metadata.review_id

    def test_update_status_and_comment_persists(self):
        updated = self.service.update_finding(self.review_id, "F-002", {"status": "accepted", "comment": "Ask for destruction wording"})
        self.assertEqual((updated.status, updated.comment), ("accepted", "Ask for destruction wording"))
        stored = next(f for f in self.service.get_review(self.review_id).findings if f.finding_id == "F-002")
        self.assertEqual((stored.status, stored.comment), ("accepted", "Ask for destruction wording"))
        self.assertIsNotNone(stored.updated_at)

    def test_partial_update_keeps_other_field(self):
        self.service.update_finding(self.review_id, "F-001", {"comment": "needs threshold"})
        updated = self.service.update_finding(self.review_id, "F-001", {"status": "dismissed"})
        self.assertEqual((updated.status, updated.comment), ("dismissed", "needs threshold"))

    def test_comment_can_be_cleared(self):
        self.service.update_finding(self.review_id, "F-001", {"comment": "x"})
        updated = self.service.update_finding(self.review_id, "F-001", {"comment": None})
        self.assertIsNone(updated.comment)

    def test_other_findings_are_untouched(self):
        self.service.update_finding(self.review_id, "F-001", {"status": "accepted"})
        others = [f.status for f in self.service.get_review(self.review_id).findings if f.finding_id != "F-001"]
        self.assertEqual(others, ["pending", "pending"])

    def test_unknown_finding_and_review(self):
        with self.assertRaises(NotFoundError) as ctx:
            self.service.update_finding(self.review_id, "F-999", {"status": "accepted"})
        self.assertEqual(ctx.exception.code, "FINDING_NOT_FOUND")
        with self.assertRaises(NotFoundError) as ctx:
            self.service.update_finding("rev_missing", "F-001", {"status": "accepted"})
        self.assertEqual(ctx.exception.code, "REVIEW_NOT_FOUND")

    def test_returned_reports_are_copies(self):
        report = self.service.get_review(self.review_id)
        report.findings[0].status = "accepted"
        self.assertEqual(self.service.get_review(self.review_id).findings[0].status, "pending")


class AmbiguousClauseTests(unittest.TestCase):
    def run_with(self, resolver):
        container = make_container(resolver=resolver)
        add_document(container, "amb", AMBIGUOUS_NDA)
        return container.review_service.create_review("amb")

    def test_without_llm_ambiguous_goes_to_human_review(self):
        report = self.run_with(None)
        r1 = next(r for r in report.rule_results if r.rule_id == "R1")
        self.assertEqual(r1.outcome, "review_required")
        self.assertIn("LLM_NOT_CONFIGURED", [w.code for w in report.warnings])
        self.assertIn("AMBIGUOUS_CLAUSE", [w.code for w in report.warnings])
        self.assertEqual(report.metadata.llm_calls, 0)

    def test_llm_answer_with_real_quote_is_accepted(self):
        resolver = FakeResolver(LLMVerdict("pass", "Looks fine", "The parties will talk about things."))
        report = self.run_with(resolver)
        r1 = next(r for r in report.rule_results if r.rule_id == "R1")
        self.assertEqual((r1.outcome, r1.method), ("pass", "llm"))
        self.assertTrue(r1.evidence.verified)
        self.assertEqual(report.metadata.llm_calls, 1)
        self.assertEqual(resolver.calls, 1)  # only the one ambiguous rule reached the LLM
        self.assertEqual(report.metadata.model, "fake-model")
        self.assertLess(report.metadata.rules_resolved_without_llm, report.metadata.summary.rules_checked)

    def test_llm_quote_not_in_document_is_rejected(self):
        report = self.run_with(FakeResolver(LLMVerdict("pass", "ok", "This sentence was invented.")))
        r1 = next(r for r in report.rule_results if r.rule_id == "R1")
        self.assertEqual((r1.outcome, r1.method), ("review_required", "deterministic"))
        self.assertIn("EVIDENCE_UNVERIFIED", [w.code for w in report.warnings])

    def test_llm_invalid_outcome_is_rejected(self):
        report = self.run_with(FakeResolver(LLMVerdict("approved", "ok", "The parties will talk about things.")))
        self.assertIn("LLM_INVALID_OUTPUT", [w.code for w in report.warnings])

    def test_llm_failure_never_fails_silently(self):
        report = self.run_with(FakeResolver(error=TimeoutError("slow")))
        r1 = next(r for r in report.rule_results if r.rule_id == "R1")
        self.assertEqual(r1.outcome, "review_required")
        self.assertIn("LLM_ERROR", [w.code for w in report.warnings])


if __name__ == "__main__":
    unittest.main()
