"""LLM gateway + OpenAI resolver, tested against a stand-in OpenAI server (no key, no network)."""
import json
import unittest

from app.container import build_container, build_resolver
from app.core.config import Settings
from app.dev_seed import SAMPLE_DOCUMENT_ID
from app.llm.gateway import LLMError, NoopGateway, OpenAIGateway, build_gateway
from app.services.llm_resolver import GatewayResolver, parse_verdict
from tests.fake_openai import FakeOpenAI, completion, verdict
from tests.helpers import AMBIGUOUS_NDA, add_document, make_container

KEY = "sk-test-key-123"
SCHEMA = {"type": "object", "properties": {"a": {"type": "string"}}, "required": ["a"], "additionalProperties": False}


class OpenAITestCase(unittest.TestCase):
    def setUp(self):
        self.fake = FakeOpenAI()
        self.addCleanup(self.fake.close)
        self.gateway = OpenAIGateway(KEY, "gpt-4o-mini", self.fake.url, timeout_seconds=5, max_retries=0, retry_delay_seconds=0)

    def review(self, text, gateway=None):
        gateway = gateway or self.gateway
        container = make_container(resolver=GatewayResolver(gateway))
        add_document(container, "doc", text)
        return container.review_service.create_review("doc")


class GatewayRequestTests(OpenAITestCase):
    def test_request_follows_the_chat_completions_api(self):
        self.fake.reply(completion('{"a": "x"}'))
        reply = self.gateway.chat_json("sys", "usr", SCHEMA, schema_name="thing", max_output_tokens=123)
        self.assertEqual(reply, '{"a": "x"}')
        request = self.fake.requests[0]
        body = request["body"]
        self.assertEqual((request["path"], request["auth"]), ("/v1/chat/completions", f"Bearer {KEY}"))
        self.assertEqual(body["model"], "gpt-4o-mini")
        self.assertEqual([m["role"] for m in body["messages"]], ["system", "user"])
        self.assertEqual(body["response_format"], {"type": "json_schema", "json_schema": {"name": "thing", "strict": True, "schema": SCHEMA}})
        self.assertEqual((body["temperature"], body["seed"], body["max_completion_tokens"]), (0, 42, 123))

    def test_models_with_fixed_sampling_get_no_temperature(self):
        gateway = OpenAIGateway(KEY, "gpt-5-mini", self.fake.url, max_retries=0)
        gateway.chat_json("s", "u", SCHEMA)
        self.assertNotIn("temperature", self.fake.requests[0]["body"])
        self.assertNotIn("seed", self.fake.requests[0]["body"])

    def test_the_key_is_never_shown(self):
        self.assertNotIn(KEY, repr(self.gateway))
        self.assertNotIn(KEY, repr(Settings(openai_api_key=KEY)))
        container = make_container(resolver=GatewayResolver(self.gateway))
        self.assertNotIn(KEY, json.dumps(container.llm_status()))

    def test_empty_key_is_refused(self):
        with self.assertRaises(ValueError):
            OpenAIGateway("")


class GatewayErrorTests(OpenAITestCase):
    def error_of(self, reply, gateway=None):
        self.fake.reply(reply)
        with self.assertRaises(LLMError) as ctx:
            (gateway or self.gateway).chat_json("s", "u", SCHEMA)
        return ctx.exception

    def test_http_errors_map_to_stable_codes(self):
        cases = {401: "LLM_AUTH_FAILED", 404: "LLM_MODEL_NOT_FOUND", 429: "LLM_RATE_LIMITED", 500: "LLM_SERVER_ERROR", 400: "LLM_REQUEST_FAILED"}
        for status, code in cases.items():
            with self.subTest(status=status):
                self.assertEqual(self.error_of((status, {"error": {"message": "secret document text"}})).code, code)

    def test_error_messages_do_not_echo_provider_bodies(self):
        error = self.error_of((400, {"error": {"message": "secret document text"}}))
        self.assertNotIn("secret", error.message)

    def test_truncated_refused_and_malformed_replies(self):
        self.assertEqual(self.error_of(completion("{}", finish_reason="length")).code, "LLM_OUTPUT_TRUNCATED")
        self.assertEqual(self.error_of(completion(None, refusal="no")).code, "LLM_REFUSED")
        self.assertEqual(self.error_of((200, {"unexpected": True})).code, "LLM_INVALID_RESPONSE")

    def test_not_reachable(self):
        down = OpenAIGateway(KEY, "gpt-4o-mini", "http://127.0.0.1:1/v1", timeout_seconds=2, max_retries=0)
        self.assertEqual(self.error_of((200, {}), down).code, "LLM_UNAVAILABLE")

    def test_retries_a_rate_limit_then_succeeds(self):
        gateway = OpenAIGateway(KEY, "gpt-4o-mini", self.fake.url, max_retries=2, retry_delay_seconds=0)
        self.fake.reply((429, {}), completion('{"a": "ok"}'))
        self.assertEqual(gateway.chat_json("s", "u", SCHEMA), '{"a": "ok"}')
        self.assertEqual(len(self.fake.requests), 2)

    def test_does_not_retry_a_bad_key(self):
        gateway = OpenAIGateway(KEY, "gpt-4o-mini", self.fake.url, max_retries=2, retry_delay_seconds=0)
        self.fake.reply((401, {}))
        with self.assertRaises(LLMError):
            gateway.chat_json("s", "u", SCHEMA)
        self.assertEqual(len(self.fake.requests), 1)

    def test_noop_gateway_refuses_clearly(self):
        with self.assertRaises(LLMError) as ctx:
            NoopGateway().chat_json("s", "u", SCHEMA)
        self.assertEqual((ctx.exception.code, ctx.exception.status), ("LLM_NOT_CONFIGURED", 503))


class HealthTests(OpenAITestCase):
    def test_check_ok(self):
        self.assertEqual(self.gateway.check(), {"reachable": True, "key_valid": True, "model_available": True})
        self.assertEqual(self.fake.requests[0]["path"], "/v1/models/gpt-4o-mini")

    def test_check_bad_key_and_missing_model(self):
        self.fake.models(401, {})
        self.assertEqual(self.gateway.check(), {"reachable": True, "key_valid": False, "model_available": False})
        self.fake.models(404, {})
        self.assertEqual(self.gateway.check(), {"reachable": True, "key_valid": True, "model_available": False})

    def test_check_unreachable(self):
        down = OpenAIGateway(KEY, "gpt-4o-mini", "http://127.0.0.1:1/v1", timeout_seconds=2)
        self.assertEqual(down.check(timeout_seconds=2), {"reachable": False, "key_valid": False, "model_available": False})

    def test_llm_status_for_health_endpoint(self):
        self.assertEqual(make_container().llm_status(), {"provider": "none", "configured": False})
        container = build_container(Settings(llm_provider="openai", openai_api_key=KEY, openai_base_url=self.fake.url))
        self.assertEqual(container.llm_status(), {"provider": "openai", "configured": True, "model": "gpt-4o-mini",
                                                  "reachable": True, "key_valid": True, "model_available": True})


class ReviewThroughGatewayTests(OpenAITestCase):
    def test_ambiguous_clause_resolved_by_llm(self):
        self.fake.reply(verdict("pass", "States the duty", "The parties will talk about things."))
        report = self.review(AMBIGUOUS_NDA)
        r1 = next(r for r in report.rule_results if r.rule_id == "R1")
        self.assertEqual((r1.outcome, r1.method, r1.evidence.verified), ("pass", "llm", True))
        self.assertEqual((report.metadata.llm_calls, report.metadata.model, report.metadata.prompt_version),
                         (1, "gpt-4o-mini", "gateway-clause-v1"))

    def test_only_the_one_clause_is_sent_and_marked_untrusted(self):
        self.review(AMBIGUOUS_NDA)
        system, user = (m["content"] for m in self.fake.requests[0]["body"]["messages"])
        self.assertIn("untrusted", system)
        self.assertIn("<clause>\nThe parties will talk about things.\n</clause>", user)
        self.assertNotIn("two years", user)

    def test_clear_documents_never_call_the_llm(self):
        make_container(resolver=GatewayResolver(self.gateway)).review_service.create_review(SAMPLE_DOCUMENT_ID)
        self.assertEqual(self.fake.requests, [])

    def test_failures_become_warnings_not_crashes(self):
        cases = [
            (verdict("pass", "ok", "A sentence that is not in the document."), "EVIDENCE_UNVERIFIED"),
            (completion("Sure! Looks fine."), "LLM_INVALID_OUTPUT"),
            ((500, {}), "LLM_ERROR"),
            ((401, {}), "LLM_ERROR"),
        ]
        for reply, code in cases:
            with self.subTest(code=code):
                self.fake.reply(reply)
                report = self.review(AMBIGUOUS_NDA)
                self.assertEqual(next(r for r in report.rule_results if r.rule_id == "R1").outcome, "review_required")
                self.assertIn(code, [w.code for w in report.warnings])

    def test_unclear_sample_only_r1_goes_to_the_llm(self):
        quote = "The Receiving Party will keep everything the Disclosing Party shares secret and will not pass it to anyone else."
        self.fake.reply(verdict("pass", "Secret and no disclosure", quote))
        report = make_container(resolver=GatewayResolver(self.gateway)).review_service.create_review("doc_sample_unclear_nda")
        self.assertEqual({r.rule_id: r.method for r in report.rule_results}["R1"], "llm")
        self.assertEqual((report.metadata.llm_calls, report.metadata.rules_resolved_without_llm), (1, 4))


class ParseAndConfigTests(unittest.TestCase):
    def test_parse_verdict(self):
        verdict_ = parse_verdict('{"outcome": " PASS ", "reason": "r", "quote": "q"}')
        self.assertEqual((verdict_.outcome, verdict_.reason, verdict_.quote), ("pass", "r", "q"))
        for bad in ("not json", '["pass"]', '{"outcome": "pass"}', '{"outcome": 1, "reason": "r", "quote": "q"}'):
            self.assertIsNone(parse_verdict(bad))

    def test_provider_selection(self):
        self.assertIsInstance(build_gateway(Settings()), NoopGateway)
        self.assertFalse(build_resolver(Settings()).configured)
        gateway = build_gateway(Settings(llm_provider="openai", openai_api_key=KEY))
        self.assertEqual((gateway.provider, gateway.model), ("openai", "gpt-4o-mini"))

    def test_openai_without_a_key_fails_at_startup_with_a_clear_message(self):
        with self.assertRaises(ValueError) as ctx:
            build_gateway(Settings(llm_provider="openai"))
        self.assertIn("OPENAI_API_KEY", str(ctx.exception))
        self.assertNotIn(KEY, str(ctx.exception))

    def test_unknown_provider(self):
        for name in ("ollama", "claude"):
            with self.assertRaises(ValueError):
                build_gateway(Settings(llm_provider=name))


if __name__ == "__main__":
    unittest.main()
