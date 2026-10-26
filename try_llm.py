"""Checks the OpenAI connection by reviewing a document that has an unclear clause.

Run from the project folder (OPENAI_API_KEY must be set, e.g. in .env):
    python try_llm.py
Makes one small API call. Uses LLM_PROVIDER=openai unless you set LLM_PROVIDER yourself.
"""
import os

os.environ.setdefault("LLM_PROVIDER", "openai")

from app.container import build_container  # noqa: E402  (after the env default is set)
from app.core.config import Settings  # noqa: E402
from app.repositories.document_repo import PageText, StoredDocument  # noqa: E402

TEXT = """Clause 1 — Purpose and confidentiality
The Receiving Party will keep everything the Disclosing Party shares secret and will not pass it to anyone else.
"""

settings = Settings.from_env()
print(f"Provider: {settings.llm_provider}   Model: {settings.openai_model}   Key set: {bool(settings.openai_api_key)}\n")

try:
    container = build_container(settings)
except ValueError as exc:
    raise SystemExit(f"Result: {exc}")

print("Health:", container.llm_status(), "\n")
container.documents.add(
    StoredDocument(document_id="llm_check", file_name="llm_check.txt",
                   extraction_status="success", pages=[PageText(page=1, text=TEXT)])
)
report = container.review_service.create_review("llm_check")

for r in report.rule_results:
    print(f"{r.rule_id}: {r.outcome:16} [{r.method}]  {r.reason}")
print(f"\nLLM calls: {report.metadata.llm_calls}")
for w in report.warnings:
    print(f"warning {w.code}: {w.message}")

codes = {w.code for w in report.warnings}
if "LLM_NOT_CONFIGURED" in codes:
    print("\nResult: the LLM is switched off (LLM_PROVIDER is not 'openai').")
elif "LLM_ERROR" in codes:
    print("\nResult: the OpenAI call failed. Check OPENAI_API_KEY, OPENAI_MODEL, your quota and internet access.")
elif report.metadata.llm_calls:
    print("\nResult: OpenAI is working.")
else:
    print("\nResult: no LLM call was needed for this text.")
