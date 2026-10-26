"""HTTP-level tests (envelope, status codes, validation). Skipped automatically when
FastAPI / httpx are not installed (they are in requirements.txt)."""
import unittest

try:
    from fastapi.testclient import TestClient

    from app.core.config import Settings
    from app.main import create_app

    HAVE_FASTAPI = True
except ImportError:  # pragma: no cover
    HAVE_FASTAPI = False

from tests.file_factory import (  # noqa: E402
    EXPECTED_SAMPLE_OUTCOMES, SAMPLE_PAGE_1, SAMPLE_PAGE_2, make_docx, make_pdf,
)

ENVELOPE_KEYS = {"statusCode", "success", "message", "data", "error"}


@unittest.skipUnless(HAVE_FASTAPI, "fastapi/httpx not installed")
class ReviewApiTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(create_app(Settings()), raise_server_exceptions=False)

    def create_review(self):
        return self.client.post("/api/reviews", json={"document_id": "doc_sample_nda"})

    def assert_envelope(self, response, status, success):
        body = response.json()
        self.assertEqual(response.status_code, status)
        self.assertEqual(set(body), ENVELOPE_KEYS)
        self.assertEqual(body["statusCode"], status)
        self.assertIs(body["success"], success)
        if success:
            self.assertIsNone(body["error"])
        else:
            self.assertIsNone(body["data"])
            self.assertIn("code", body["error"])
        return body

    def test_health_reports_llm_status(self):
        body = self.assert_envelope(self.client.get("/health"), 200, True)
        self.assertEqual(body["data"]["status"], "ok")
        self.assertEqual(body["data"]["llm"], {"provider": "none", "configured": False})

    def test_post_review(self):
        body = self.assert_envelope(self.create_review(), 201, True)
        self.assertEqual(set(body["data"]), {"review_id", "status", "playbook_version"})
        self.assertEqual(body["data"]["status"], "completed")

    def test_get_review(self):
        review_id = self.create_review().json()["data"]["review_id"]
        body = self.assert_envelope(self.client.get(f"/api/reviews/{review_id}"), 200, True)
        self.assertEqual(set(body["data"]), {"metadata", "clauses", "rule_results", "findings", "warnings"})
        self.assertEqual({r["rule_id"]: r["outcome"] for r in body["data"]["rule_results"]},
                         {"R1": "pass", "R2": "pass", "R3": "review_required", "R4": "deviation", "R5": "deviation"})

    def test_patch_finding(self):
        review_id = self.create_review().json()["data"]["review_id"]
        response = self.client.patch(f"/api/reviews/{review_id}/findings/F-001",
                                     json={"status": "accepted", "comment": "ok"})
        body = self.assert_envelope(response, 200, True)
        self.assertEqual(body["data"], {"finding_id": "F-001", "status": "accepted", "comment": "ok"})

    def test_errors_use_the_same_envelope(self):
        body = self.assert_envelope(self.client.post("/api/reviews", json={"document_id": "nope"}), 404, False)
        self.assertEqual(body["error"]["code"], "DOCUMENT_NOT_FOUND")
        body = self.assert_envelope(self.client.get("/api/reviews/rev_missing"), 404, False)
        self.assertEqual(body["error"]["code"], "REVIEW_NOT_FOUND")
        self.assert_envelope(self.client.get("/api/unknown"), 404, False)

    def test_validation_errors(self):
        body = self.assert_envelope(self.client.post("/api/reviews", json={}), 422, False)
        self.assertEqual(body["error"]["code"], "VALIDATION_ERROR")
        self.assertEqual(body["error"]["details"][0]["field"], "document_id")

        review_id = self.create_review().json()["data"]["review_id"]
        url = f"/api/reviews/{review_id}/findings/F-001"
        self.assert_envelope(self.client.patch(url, json={}), 422, False)
        self.assert_envelope(self.client.patch(url, json={"status": "maybe"}), 422, False)
        self.assert_envelope(self.client.patch(url, json={"status": None}), 422, False)
        self.assert_envelope(self.client.patch(url, json={"comment": "x" * 2001}), 422, False)

    def test_unknown_finding(self):
        review_id = self.create_review().json()["data"]["review_id"]
        response = self.client.patch(f"/api/reviews/{review_id}/findings/F-999", json={"status": "accepted"})
        body = self.assert_envelope(response, 404, False)
        self.assertEqual(body["error"]["code"], "FINDING_NOT_FOUND")


@unittest.skipUnless(HAVE_FASTAPI, "fastapi/httpx not installed")
class UploadApiTests(unittest.TestCase):
    """Upload -> preview -> (optional playbook) -> review, over HTTP."""

    def setUp(self):
        self.client = TestClient(create_app(Settings()), raise_server_exceptions=False)

    def upload(self, name="nda.pdf", data=None, content_type="application/pdf"):
        data = make_pdf([SAMPLE_PAGE_1, SAMPLE_PAGE_2]) if data is None else data
        return self.client.post("/api/documents", files={"file": (name, data, content_type)})

    def test_upload_pdf_then_review_with_the_built_in_playbook(self):
        response = self.upload()
        body = response.json()
        self.assertEqual((response.status_code, body["success"]), (201, True))
        data = body["data"]
        self.assertEqual((data["extraction_status"], data["file_name"], data["page_count"], data["section_count"]),
                         ("success", "nda.pdf", 2, 5))

        created = self.client.post("/api/reviews", json={"document_id": data["document_id"]}).json()["data"]
        report = self.client.get(f"/api/reviews/{created['review_id']}").json()["data"]
        self.assertEqual({r["rule_id"]: r["outcome"] for r in report["rule_results"]}, EXPECTED_SAMPLE_OUTCOMES)
        self.assertEqual(report["metadata"]["playbook_id"], "NDA-BASELINE")

    def test_upload_docx(self):
        docx = make_docx((SAMPLE_PAGE_1 + "\n" + SAMPLE_PAGE_2).split("\n"))
        response = self.upload("nda.docx", docx, "application/vnd.openxmlformats-officedocument.wordprocessingml.document")
        self.assertEqual(response.status_code, 201)
        self.assertIsNone(response.json()["data"]["page_count"])

    def test_text_preview(self):
        document_id = self.upload().json()["data"]["document_id"]
        body = self.client.get(f"/api/documents/{document_id}/text").json()
        self.assertEqual([p["page"] for p in body["data"]["pages"]], [1, 2])
        self.assertEqual(body["data"]["sections"][0], {"label": "Clause 1", "title": "Purpose", "page": 1})
        missing = self.client.get("/api/documents/doc_missing/text")
        self.assertEqual((missing.status_code, missing.json()["error"]["code"]), (404, "DOCUMENT_NOT_FOUND"))

    def test_clauses_endpoint(self):
        document_id = self.upload().json()["data"]["document_id"]
        response = self.client.get(f"/api/documents/{document_id}/clauses")
        body = response.json()
        self.assertEqual((response.status_code, body["data"]["method"], body["data"]["clause_count"]), (200, "manual", 5))
        self.assertEqual(set(body["data"]["clauses"][0]), {"clause_type", "status", "evidence", "summary", "source"})
        self.assertIn("x-request-id", response.headers)
        bad = self.client.get(f"/api/documents/{document_id}/clauses?method=magic")
        self.assertEqual((bad.status_code, bad.json()["error"]["code"]), (422, "VALIDATION_ERROR"))
        missing = self.client.get("/api/documents/doc_missing/clauses")
        self.assertEqual((missing.status_code, missing.json()["error"]["code"]), (404, "DOCUMENT_NOT_FOUND"))

    def test_rejected_uploads_use_the_error_envelope(self):
        cases = [
            (self.upload("notes.txt", b"hello there, plain text"), 415, "UNSUPPORTED_FILE_TYPE"),
            (self.upload("empty.pdf", b""), 422, "FILE_EMPTY"),
            (self.upload("bad.pdf", b"%PDF-1.4 broken"), 422, "FILE_UNREADABLE"),
            (self.upload("blank.pdf", make_pdf([" "])), 422, "NO_EXTRACTABLE_TEXT"),
        ]
        for response, status, code in cases:
            body = response.json()
            self.assertEqual((response.status_code, body["success"], body["error"]["code"]), (status, False, code))
            self.assertEqual(set(body), ENVELOPE_KEYS)

    def test_missing_file_field(self):
        response = self.client.post("/api/documents")
        body = response.json()
        self.assertEqual((response.status_code, body["error"]["code"]), (422, "VALIDATION_ERROR"))
        self.assertEqual(body["error"]["details"][0]["field"], "file")

    def test_upload_playbook_and_review_with_it(self):
        import json

        from tests.test_playbook_upload import custom, rule

        playbook = custom()
        rule(playbook, "R3")["params"]["min_months"] = 24
        uploaded = self.client.post("/api/playbooks", files={"file": ("mine.json", json.dumps(playbook).encode(), "application/json")})
        self.assertEqual(uploaded.status_code, 201)
        self.assertEqual(uploaded.json()["data"]["playbook_id"], "MY-NDA-PLAYBOOK")

        listing = self.client.get("/api/playbooks").json()["data"]
        self.assertEqual(listing["default_playbook_id"], "NDA-BASELINE")
        self.assertEqual([p["playbook_id"] for p in listing["playbooks"]], ["NDA-BASELINE", "MY-NDA-PLAYBOOK"])

        created = self.client.post("/api/reviews", json={"document_id": "doc_sample_nda", "playbook_id": "MY-NDA-PLAYBOOK"})
        report = self.client.get(f"/api/reviews/{created.json()['data']['review_id']}").json()["data"]
        self.assertEqual(report["metadata"]["playbook_id"], "MY-NDA-PLAYBOOK")
        self.assertEqual({r["rule_id"]: r["outcome"] for r in report["rule_results"]}["R3"], "deviation")

    def test_playbook_template_download_and_errors(self):
        template = self.client.get("/api/playbooks/NDA-BASELINE").json()["data"]
        self.assertEqual((template["playbook_id"], len(template["rules"])), ("NDA-BASELINE", 5))

        import json

        reserved = self.client.post("/api/playbooks", files={"file": ("t.json", json.dumps(template).encode(), "application/json")})
        self.assertEqual((reserved.status_code, reserved.json()["error"]["code"]), (409, "PLAYBOOK_ID_RESERVED"))
        broken = self.client.post("/api/playbooks", files={"file": ("t.json", b"{oops", "application/json")})
        self.assertEqual((broken.status_code, broken.json()["error"]["code"]), (422, "INVALID_PLAYBOOK"))
        self.assertEqual(self.client.get("/api/playbooks/NOPE").status_code, 404)


if __name__ == "__main__":
    unittest.main()