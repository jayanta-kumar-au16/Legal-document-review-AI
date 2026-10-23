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


if __name__ == "__main__":
    unittest.main()
