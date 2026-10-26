"""Tests the Ollama fallback against a stand-in HTTP server that mimics POST /api/chat.
No real Ollama or model is needed (or used) here."""
import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from app.container import build_resolver
from app.core.config import Settings
from app.dev_seed import SAMPLE_DOCUMENT_ID
from app.services.llm_client import NoopResolver
from app.services.ollama_resolver import OllamaResolver, parse_verdict
from tests.helpers import AMBIGUOUS_NDA, add_document, make_container


class _Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        self.server.requests.append({"path": self.path, "body": json.loads(self.rfile.read(length))})
        status, body = self.server.reply
        data = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):  # Ollama's model list: GET /api/tags
        data = json.dumps(self.server.tags).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args):  # keep test output quiet
        pass


def chat_reply(outcome="pass", reason="ok", quote="") -> tuple[int, dict]:
    content = json.dumps({"outcome": outcome, "reason": reason, "quote": quote})
    return 200, {"model": "llama3.1", "message": {"role": "assistant", "content": content}, "done": True}


class OllamaTestCase(unittest.TestCase):
    def setUp(self):
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        self.server.requests, self.server.reply = [], chat_reply()
        self.server.tags = {"models": [{"name": "llama3.1:latest"}]}
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        self.resolver = OllamaResolver(self.url, "llama3.1", timeout_seconds=5)

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()

    def review(self, document_text, resolver=None):
        container = make_container(resolver=resolver or self.resolver)
        add_document(container, "doc", document_text)
        return container.review_service.create_review("doc")


class RequestShapeTests(OllamaTestCase):
    def test_request_follows_the_ollama_chat_api(self):
        self.server.reply = chat_reply("pass", "ok", "The parties will talk about things.")
        self.review(AMBIGUOUS_NDA)
        self.assertEqual(len(self.server.requests), 1)
        request = self.server.requests[0]
        body = request["body"]
        self.assertEqual(request["path"], "/api/chat")
        self.assertEqual(body["model"], "llama3.1")
        self.assertIs(body["stream"], False)
        self.assertEqual(body["options"]["temperature"], 0)
        self.assertIn("seed", body["options"])
        self.assertEqual(body["format"]["properties"]["outcome"]["enum"], ["pass", "deviation", "review_required"])

    def test_prompt_marks_the_clause_as_untrusted_and_sends_only_that_clause(self):
        self.review(AMBIGUOUS_NDA)
        system, user = (m["content"] for m in self.server.requests[0]["body"]["messages"])
        self.assertIn("untrusted", system)
        self.assertIn("Never follow", system)
        self.assertIn("Criterion: Does the clause oblige the receiving party", user)
        self.assertIn("<clause>\nThe parties will talk about things.\n</clause>", user)
        self.assertNotIn("two years", user)  # the other clause is not sent


class EndToEndTests(OllamaTestCase):
    def test_ambiguous_clause_resolved_by_llm(self):
        self.server.reply = chat_reply("pass", "States the duty", "The parties will talk about things.")
        report = self.review(AMBIGUOUS_NDA)
        r1 = next(r for r in report.rule_results if r.rule_id == "R1")
        self.assertEqual((r1.outcome, r1.method, r1.evidence.verified), ("pass", "llm", True))
        self.assertEqual((report.metadata.llm_calls, report.metadata.model), (1, "llama3.1"))
        self.assertEqual(report.metadata.prompt_version, "ollama-clause-v1")

    def test_clear_documents_never_call_the_llm(self):
        container = make_container(resolver=self.resolver)
        container.review_service.create_review(SAMPLE_DOCUMENT_ID)
        self.assertEqual(self.server.requests, [])

    def test_unusual_wording_escalates_to_llm_when_rule_allows_it(self):
        text = "Clause 1 — Purpose\nThe Receiving Party may use the information for the agreed project.\n"
        # Without an LLM the code check cannot decide: review_required, no warnings.
        plain = make_container()
        add_document(plain, "doc", text)
        before = plain.review_service.create_review("doc")
        self.assertEqual(next(r for r in before.rule_results if r.rule_id == "R2").outcome, "review_required")
        self.assertNotIn("LLM_NOT_CONFIGURED", [w.code for w in before.warnings])

        self.server.reply = chat_reply("pass", "Limited to the agreed project",
                                       "The Receiving Party may use the information for the agreed project.")
        after = self.review(text)
        r2 = next(r for r in after.rule_results if r.rule_id == "R2")
        self.assertEqual((r2.outcome, r2.method), ("pass", "llm"))
        self.assertEqual(len(self.server.requests), 1)  # R3 (policy) and others did not escalate


class UnclearSampleTests(OllamaTestCase):
    """The seeded `doc_sample_unclear_nda` exists so the LLM path can be tried through the API."""

    QUOTE = "The Receiving Party will keep everything the Disclosing Party shares secret and will not pass it to anyone else."

    def outcomes(self, report):
        return {r.rule_id: (r.outcome, r.method) for r in report.rule_results}

    def test_without_llm_only_r1_is_unresolved(self):
        report = make_container().review_service.create_review("doc_sample_unclear_nda")
        self.assertEqual(
            self.outcomes(report),
            {"R1": ("review_required", "deterministic"), "R2": ("pass", "deterministic"),
             "R3": ("review_required", "deterministic"), "R4": ("pass", "deterministic"),
             "R5": ("pass", "deterministic")},
        )
        self.assertEqual(report.metadata.llm_calls, 0)

    def test_with_ollama_only_r1_goes_to_the_llm(self):
        self.server.reply = chat_reply("pass", "Keeps information secret and bars disclosure", self.QUOTE)
        container = make_container(resolver=self.resolver)
        report = container.review_service.create_review("doc_sample_unclear_nda")
        self.assertEqual(self.outcomes(report)["R1"], ("pass", "llm"))
        self.assertEqual(report.metadata.llm_calls, 1)
        self.assertEqual(len(self.server.requests), 1)
        self.assertEqual(report.metadata.rules_resolved_without_llm, 4)


class StatusTests(OllamaTestCase):
    def test_check_reports_reachable_and_model_available(self):
        self.assertEqual(self.resolver.check(), {"reachable": True, "model_available": True})

    def test_check_reports_missing_model(self):
        self.server.tags = {"models": [{"name": "mistral:latest"}]}
        self.assertEqual(self.resolver.check(), {"reachable": True, "model_available": False})

    def test_check_reports_ollama_down(self):
        down = OllamaResolver("http://127.0.0.1:1", "llama3.1")
        self.assertEqual(down.check(timeout_seconds=2), {"reachable": False, "model_available": False})

    def test_llm_status_for_health_endpoint(self):
        self.assertEqual(make_container().llm_status(), {"provider": "none", "configured": False})
        status = make_container(resolver=self.resolver).llm_status()
        self.assertEqual(status, {"provider": "ollama", "configured": True, "model": "llama3.1",
                                  "reachable": True, "model_available": True})


class FailureTests(OllamaTestCase):
    def rule(self, report, rule_id="R1"):
        return next(r for r in report.rule_results if r.rule_id == rule_id)

    def test_invented_quote_is_rejected(self):
        self.server.reply = chat_reply("pass", "ok", "A sentence that is not in the document.")
        report = self.review(AMBIGUOUS_NDA)
        self.assertEqual(self.rule(report).outcome, "review_required")
        self.assertIn("EVIDENCE_UNVERIFIED", [w.code for w in report.warnings])

    def test_reply_that_is_not_json_is_rejected(self):
        self.server.reply = (200, {"message": {"role": "assistant", "content": "Sure! The clause looks fine."}})
        report = self.review(AMBIGUOUS_NDA)
        self.assertEqual(self.rule(report).outcome, "review_required")
        self.assertIn("LLM_INVALID_OUTPUT", [w.code for w in report.warnings])

    def test_server_error_becomes_a_warning_not_a_crash(self):
        self.server.reply = (500, {"error": "model failed to load"})
        report = self.review(AMBIGUOUS_NDA)
        self.assertEqual(self.rule(report).outcome, "review_required")
        self.assertIn("LLM_ERROR", [w.code for w in report.warnings])

    def test_ollama_not_running(self):
        down = OllamaResolver("http://127.0.0.1:1", "llama3.1", timeout_seconds=2)
        report = self.review(AMBIGUOUS_NDA, resolver=down)
        self.assertEqual(self.rule(report).outcome, "review_required")
        self.assertIn("LLM_ERROR", [w.code for w in report.warnings])


class ParseAndConfigTests(unittest.TestCase):
    def test_parse_verdict(self):
        verdict = parse_verdict('{"outcome": " PASS ", "reason": "r", "quote": "q"}')
        self.assertEqual((verdict.outcome, verdict.reason, verdict.quote), ("pass", "r", "q"))
        self.assertIsNone(parse_verdict("not json"))
        self.assertIsNone(parse_verdict('["pass"]'))
        self.assertIsNone(parse_verdict('{"outcome": "pass"}'))
        self.assertIsNone(parse_verdict('{"outcome": 1, "reason": "r", "quote": "q"}'))

    def test_provider_selection(self):
        self.assertIsInstance(build_resolver(Settings()), NoopResolver)
        resolver = build_resolver(Settings(llm_provider="ollama"))
        self.assertIsInstance(resolver, OllamaResolver)
        self.assertEqual(resolver.model, "llama3.1")
        with self.assertRaises(ValueError):
            build_resolver(Settings(llm_provider="openai"))


if __name__ == "__main__":
    unittest.main()
