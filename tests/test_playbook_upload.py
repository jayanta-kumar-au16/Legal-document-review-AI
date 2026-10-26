import copy
import json
import unittest

from app.core.config import BASE_DIR
from app.core.exceptions import AppError, NotFoundError
from app.schemas.playbook import Playbook
from app.services.playbook_service import MAX_PLAYBOOK_BYTES
from tests.file_factory import EXPECTED_SAMPLE_OUTCOMES
from tests.helpers import make_container, outcomes

BUILT_IN = json.loads((BASE_DIR / "config" / "playbooks" / "nda_baseline.json").read_text(encoding="utf-8"))


def custom(**changes) -> dict:
    """The built-in playbook with a new id/version (the usual way to start a custom one)."""
    playbook = copy.deepcopy(BUILT_IN)
    playbook.update({"playbook_id": "MY-NDA-PLAYBOOK", "version": "1.0", "name": "My playbook"})
    playbook.update(changes)
    return playbook


def as_bytes(playbook) -> bytes:
    return json.dumps(playbook).encode("utf-8")


def rule(playbook: dict, rule_id: str) -> dict:
    return next(r for r in playbook["rules"] if r["rule_id"] == rule_id)


def error_of(service, name: str, data: bytes) -> AppError:
    with unittest.TestCase().assertRaises(AppError) as ctx:
        service.upload(name, data)
    return ctx.exception


class UploadAndUseTests(unittest.TestCase):
    def setUp(self):
        self.container = make_container()
        self.service = self.container.playbook_service

    def test_uploaded_playbook_is_listed_and_usable(self):
        summary = self.service.upload("my.json", as_bytes(custom()))
        self.assertEqual((summary.playbook_id, summary.version, summary.rule_count, summary.built_in), ("MY-NDA-PLAYBOOK", "1.0", 5, False))
        listing = self.service.list()
        self.assertEqual(listing.default_playbook_id, "NDA-BASELINE")
        self.assertEqual([(p.playbook_id, p.built_in, p.is_default) for p in listing.playbooks],
                         [("NDA-BASELINE", True, True), ("MY-NDA-PLAYBOOK", False, False)])
        report = self.container.review_service.create_review("doc_sample_nda", "MY-NDA-PLAYBOOK")
        self.assertEqual((report.metadata.playbook_id, report.metadata.playbook_version), ("MY-NDA-PLAYBOOK", "1.0"))
        self.assertEqual(outcomes(report), EXPECTED_SAMPLE_OUTCOMES)

    def test_uploaded_rules_change_the_result(self):
        playbook = custom()
        rule(playbook, "R3")["params"]["min_months"] = 24
        rule(playbook, "R5")["params"]["jurisdictions"] = ["applicable"]  # now "applicable law" counts as specific
        self.service.upload("my.json", as_bytes(playbook))
        report = self.container.review_service.create_review("doc_sample_nda", "MY-NDA-PLAYBOOK")
        self.assertEqual(outcomes(report), {"R1": "pass", "R2": "pass", "R3": "deviation", "R4": "deviation", "R5": "pass"})

    def test_no_playbook_means_the_built_in_one(self):
        report = self.container.review_service.create_review("doc_sample_nda")
        self.assertEqual((report.metadata.playbook_id, report.metadata.playbook_version), ("NDA-BASELINE", "1.0"))

    def test_the_downloaded_template_can_be_edited_and_uploaded_again(self):
        template = self.service.get("NDA-BASELINE").model_dump(mode="json")  # what GET /api/playbooks/NDA-BASELINE returns
        template["playbook_id"] = "FROM-TEMPLATE"
        self.assertEqual(self.service.upload("t.json", as_bytes(template)).playbook_id, "FROM-TEMPLATE")

    def test_utf8_bom_from_windows_editors_is_accepted(self):
        self.assertEqual(self.service.upload("my.json", b"\xef\xbb\xbf" + as_bytes(custom())).playbook_id, "MY-NDA-PLAYBOOK")

    def test_get_and_unknown(self):
        self.assertEqual(self.service.get("NDA-BASELINE").version, "1.0")
        with self.assertRaises(NotFoundError) as ctx:
            self.service.get("NOPE")
        self.assertEqual(ctx.exception.code, "PLAYBOOK_NOT_FOUND")

    def test_review_with_unknown_playbook_still_404s(self):
        with self.assertRaises(NotFoundError) as ctx:
            self.container.review_service.create_review("doc_sample_nda", "NOPE")
        self.assertEqual(ctx.exception.code, "PLAYBOOK_NOT_FOUND")


class ConflictTests(unittest.TestCase):
    def setUp(self):
        self.service = make_container().playbook_service

    def test_built_in_id_is_reserved(self):
        error = error_of(self.service, "x.json", as_bytes(custom(playbook_id="NDA-BASELINE", version="9.9")))
        self.assertEqual((error.status_code, error.code), (409, "PLAYBOOK_ID_RESERVED"))
        self.assertEqual(self.service.get("NDA-BASELINE").version, "1.0")  # untouched

    def test_same_id_and_version_with_different_content_is_refused(self):
        self.service.upload("a.json", as_bytes(custom()))
        changed = custom()
        rule(changed, "R3")["params"]["min_months"] = 6
        error = error_of(self.service, "b.json", as_bytes(changed))
        self.assertEqual((error.status_code, error.code), (409, "PLAYBOOK_VERSION_CONFLICT"))

    def test_identical_reupload_and_new_version_are_fine(self):
        self.service.upload("a.json", as_bytes(custom()))
        self.service.upload("a.json", as_bytes(custom()))
        changed = custom(version="1.1")
        rule(changed, "R3")["params"]["min_months"] = 6
        self.service.upload("b.json", as_bytes(changed))
        self.assertEqual(self.service.get("MY-NDA-PLAYBOOK").version, "1.1")
        self.assertEqual(len(self.service.list().playbooks), 2)


class RejectionTests(unittest.TestCase):
    def setUp(self):
        self.service = make_container().playbook_service

    def assert_rejected(self, data: bytes, code="INVALID_PLAYBOOK", status=422, name="p.json", contains=""):
        error = error_of(self.service, name, data)
        self.assertEqual((error.status_code, error.code), (status, code))
        self.assertIn(contains, error.message)
        return error

    def test_file_level_problems(self):
        self.assert_rejected(as_bytes(custom()), "UNSUPPORTED_FILE_TYPE", 415, name="p.txt")
        self.assert_rejected(b"", "FILE_EMPTY")
        self.assert_rejected(b" " * (MAX_PLAYBOOK_BYTES + 1), "FILE_TOO_LARGE", 413)
        self.assert_rejected(b"{not json", contains="not valid JSON")
        self.assert_rejected(b"[1, 2]", contains="JSON object")
        self.assert_rejected(b"\xff\xfe\x00", contains="UTF-8")

    def test_schema_problems_name_the_field(self):
        error = self.assert_rejected(as_bytes({"playbook_id": "X"}), contains="not valid")
        fields = {d["field"] for d in error.details}
        self.assertTrue({"version", "name", "clause_catalog", "rules"} <= fields)

    def test_rule_problems(self):
        bad_check = custom()
        rule(bad_check, "R1")["check_type"] = "magic"
        self.assert_rejected(as_bytes(bad_check))

        unknown_clause = custom()
        rule(unknown_clause, "R1")["clause_type"] = "indemnity"
        self.assert_rejected(as_bytes(unknown_clause), contains="missing from clause_catalog")

        duplicate = custom()
        duplicate["rules"].append(copy.deepcopy(duplicate["rules"][0]))
        self.assert_rejected(as_bytes(duplicate), contains="duplicate rule_id")

        bad_severity = custom()
        rule(bad_severity, "R1")["severity"] = "critical"
        self.assert_rejected(as_bytes(bad_severity))

        bad_id = custom(playbook_id="has spaces/slash")
        self.assert_rejected(as_bytes(bad_id))

    def test_params_are_checked_per_check_type(self):
        cases = {
            "R1": [{"required_patterns": "shall protect"}, {"required_patterns": []}, {"required_patterns": ["(unclosed"]},
                   {"required_patterns": ["(a+)+$"]}, {"required_patterns": ["(\\s*)*x"]}, {"required_patterns": ["x" * 301]}],
            "R2": [{"limiting_terms": "solely"}, {"purpose_terms": [1, 2]}],
            "R3": [{"min_months": 0}, {"min_months": "12"}, {"min_months": True}, {"min_months": 5000}],
            "R4": [{}, {"required_groups": []}, {"required_groups": [[]]}, {"required_groups": ["return"]}],
            "R5": [{}, {"jurisdictions": []}, {"jurisdictions": "India"}],
        }
        for rule_id, variants in cases.items():
            for params in variants:
                playbook = custom()
                rule(playbook, rule_id)["params"] = params
                with self.subTest(rule=rule_id, params=params):
                    self.assert_rejected(as_bytes(playbook))

    def test_valid_variations_are_accepted(self):
        playbook = custom()
        rule(playbook, "R3")["params"] = {"min_months": 36}
        rule(playbook, "R1")["params"]["required_patterns"].append("\\bkeep\\s+confidential\\b")
        rule(playbook, "R2")["params"] = {"limiting_terms": ["solely"], "purpose_terms": ["evaluate"]}  # broad_terms optional
        self.assertEqual(self.service.upload("ok.json", as_bytes(playbook)).rule_count, 5)

    def test_catalog_needs_a_real_clause_type(self):
        only_other = custom(clause_catalog=[{"clause_type": "other"}], rules=[])
        self.assert_rejected(as_bytes(only_other))

    def test_the_built_in_playbook_passes_its_own_rules(self):
        Playbook.model_validate(BUILT_IN)


if __name__ == "__main__":
    unittest.main()
