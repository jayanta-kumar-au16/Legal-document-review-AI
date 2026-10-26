"""LLM gateway: every model call in this app goes through here.

Nothing else imports an HTTP client for a model. To use another provider, add a class with the same
three members (`configured`, `chat_json`, `check`) and one line in `build_gateway`.

Safety rules kept in one place:
- The API key comes from the environment (OPENAI_API_KEY), is sent only in the Authorization header,
  and is never logged, returned by /health, or shown in error messages.
- Error messages carry a fixed code and the HTTP status only. Provider error bodies and prompts can echo
  document text, so they are never copied into logs or responses.
- Replies are requested as strict JSON (Structured Outputs). Callers still validate what comes back.
"""
import json
import logging
import time
import urllib.error
import urllib.request
from typing import Any, Protocol

logger = logging.getLogger("nda_review_api")

# Models that only accept their default sampling settings (no temperature / seed).
_FIXED_SAMPLING_PREFIXES = ("gpt-5", "o1", "o3", "o4")


class LLMError(RuntimeError):
    """A model call failed. `code` is stable and safe to show; `status` is the HTTP status to report."""

    def __init__(self, code: str, message: str, status: int = 502):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


class LLMGateway(Protocol):
    provider: str
    model: str | None
    configured: bool

    def chat_json(self, system: str, user: str, schema: dict[str, Any], *, schema_name: str = "response",
                  max_output_tokens: int = 2048) -> str:
        """Send one system + one user message; return the model's reply text (JSON matching `schema`)."""

    def check(self) -> dict:
        """Cheap health probe, never raises. Does not generate text, so it costs nothing."""


class NoopGateway:
    provider = "none"
    model = None
    configured = False

    def chat_json(self, system, user, schema, *, schema_name="response", max_output_tokens=2048) -> str:
        raise LLMError("LLM_NOT_CONFIGURED", "No LLM is configured. Set LLM_PROVIDER=openai and OPENAI_API_KEY.", 503)

    def check(self) -> dict:
        return {}


class OpenAIGateway:
    provider = "openai"
    configured = True

    def __init__(self, api_key: str, model: str = "gpt-4o-mini", base_url: str = "https://api.openai.com/v1",
                 timeout_seconds: float = 60.0, max_retries: int = 2, retry_delay_seconds: float = 1.0):
        if not api_key:
            raise ValueError("OPENAI_API_KEY is empty")
        self.model = model
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout_seconds
        self._max_retries = max(0, max_retries)
        self._retry_delay = retry_delay_seconds

    def __repr__(self) -> str:  # never print the key
        return f"OpenAIGateway(model={self.model!r})"

    # ---- public ---------------------------------------------------------------------------

    def chat_json(self, system: str, user: str, schema: dict[str, Any], *, schema_name: str = "response",
                  max_output_tokens: int = 2048) -> str:
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "response_format": {"type": "json_schema", "json_schema": {"name": schema_name, "strict": True, "schema": schema}},
            "max_completion_tokens": max_output_tokens,
        }
        if not self.model.startswith(_FIXED_SAMPLING_PREFIXES):
            payload.update({"temperature": 0, "seed": 42})  # same input -> (nearly always) same answer

        body = self._request("POST", "/chat/completions", payload)
        try:
            choice = body["choices"][0]
            if choice.get("finish_reason") == "length":
                raise LLMError("LLM_OUTPUT_TRUNCATED", "The model response was cut short.", 502)
            message = choice["message"]
            if message.get("refusal"):
                raise LLMError("LLM_REFUSED", "The model declined to answer this request.", 502)
            content = message["content"]
        except (KeyError, IndexError, TypeError, AttributeError):
            raise LLMError("LLM_INVALID_RESPONSE", "The LLM service returned an unexpected response.", 502) from None
        if not isinstance(content, str):
            raise LLMError("LLM_INVALID_RESPONSE", "The LLM service returned an unexpected response.", 502)
        return content

    def check(self, timeout_seconds: float = 5.0) -> dict:
        """GET /models/{model}: tells us the key works and the model exists, without generating anything."""
        try:
            self._request("GET", f"/models/{self.model}", None, timeout=timeout_seconds, retries=0)
        except LLMError as exc:
            if exc.code == "LLM_AUTH_FAILED":
                return {"reachable": True, "key_valid": False, "model_available": False}
            if exc.code == "LLM_MODEL_NOT_FOUND":
                return {"reachable": True, "key_valid": True, "model_available": False}
            if exc.code in ("LLM_RATE_LIMITED", "LLM_REQUEST_FAILED"):
                return {"reachable": True, "key_valid": True, "model_available": True}  # busy, but the key got through
            return {"reachable": False, "key_valid": False, "model_available": False}
        return {"reachable": True, "key_valid": True, "model_available": True}

    # ---- internals ------------------------------------------------------------------------

    def _request(self, method: str, path: str, payload: dict | None, *, timeout: float | None = None,
                 retries: int | None = None) -> dict:
        retries = self._max_retries if retries is None else retries
        timeout = self._timeout if timeout is None else timeout
        data = json.dumps(payload).encode("utf-8") if payload is not None else None
        for attempt in range(retries + 1):
            request = urllib.request.Request(
                self._base_url + path, data=data, method=method,
                headers={"Authorization": f"Bearer {self._api_key}", "Content-Type": "application/json"},
            )
            try:
                with urllib.request.urlopen(request, timeout=timeout) as response:
                    return json.loads(response.read())
            except urllib.error.HTTPError as exc:
                error = _http_error(exc.code)
            except TimeoutError:
                error = LLMError("LLM_TIMEOUT", "The LLM service timed out.", 504)
            except urllib.error.URLError as exc:
                if isinstance(exc.reason, TimeoutError):
                    error = LLMError("LLM_TIMEOUT", "The LLM service timed out.", 504)
                else:
                    error = LLMError("LLM_UNAVAILABLE", "Cannot connect to the LLM service.", 503)
            except (OSError, json.JSONDecodeError):
                error = LLMError("LLM_INVALID_RESPONSE", "The LLM service returned an unexpected response.", 502)

            retryable = error.code in ("LLM_RATE_LIMITED", "LLM_TIMEOUT", "LLM_UNAVAILABLE", "LLM_SERVER_ERROR")
            if retryable and attempt < retries:
                logger.warning("LLM call failed (%s); retrying (%d/%d)", error.code, attempt + 1, retries)
                time.sleep(self._retry_delay * (2 ** attempt))
                continue
            raise error from None
        raise AssertionError("unreachable")  # pragma: no cover


def _http_error(status: int) -> LLMError:
    if status in (401, 403):
        return LLMError("LLM_AUTH_FAILED", "The LLM service rejected the API key (check OPENAI_API_KEY).", 502)
    if status == 404:
        return LLMError("LLM_MODEL_NOT_FOUND", "The LLM model was not found (check OPENAI_MODEL).", 502)
    if status == 429:
        return LLMError("LLM_RATE_LIMITED", "The LLM service is rate limiting requests or the quota is used up.", 503)
    if status >= 500:
        return LLMError("LLM_SERVER_ERROR", f"The LLM service had an internal error (HTTP {status}).", 502)
    return LLMError("LLM_REQUEST_FAILED", f"The LLM service rejected the request (HTTP {status}).", 502)


def build_gateway(settings) -> LLMGateway:
    provider = settings.llm_provider.strip().lower()
    if provider == "none":
        return NoopGateway()
    if provider == "openai":
        if not settings.openai_api_key:
            raise ValueError("LLM_PROVIDER=openai needs OPENAI_API_KEY (put it in .env or the environment)")
        return OpenAIGateway(
            api_key=settings.openai_api_key,
            model=settings.openai_model,
            base_url=settings.openai_base_url,
            timeout_seconds=settings.openai_timeout_seconds,
            max_retries=settings.openai_max_retries,
        )
    raise ValueError(f"LLM_PROVIDER must be 'none' or 'openai', got '{settings.llm_provider}'")
