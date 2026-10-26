"""Checks the Ollama connection by reviewing a document that has an unclear clause.

Run from the project folder (Ollama must be running and the model pulled):
    python try_ollama.py
Uses LLM_PROVIDER=ollama unless you set LLM_PROVIDER yourself.
"""
import os

os.environ.setdefault("LLM_PROVIDER", "ollama")

from app.container import build_container  # noqa: E402  (after the env default is set)
from app.core.config import Settings  # noqa: E402
from app.repositories.document_repo import PageText, StoredDocument  # noqa: E402

TEXT = """Clause 1 — Purpose and confidentiality
The Receiving Party will keep everything the Disclosing Party shares secret and will not pass it to anyone else.
"""

settings = Settings.from_env()
print(f"Provider: {settings.llm_provider}   Model: {settings.ollama_model}   URL: {settings.ollama_base_url}\n")

container = build_container(settings)
container.documents.add(
    StoredDocument(document_id="ollama_check", file_name="ollama_check.txt",
                   extraction_status="success", pages=[PageText(page=1, text=TEXT)])
)
report = container.review_service.create_review("ollama_check")

for r in report.rule_results:
    print(f"{r.rule_id}: {r.outcome:16} [{r.method}]  {r.reason}")
print(f"\nLLM calls: {report.metadata.llm_calls}")
for w in report.warnings:
    print(f"warning {w.code}: {w.message}")

codes = {w.code for w in report.warnings}
if "LLM_NOT_CONFIGURED" in codes:
    print("\nResult: the LLM is switched off (LLM_PROVIDER is not 'ollama').")
elif "LLM_ERROR" in codes:
    print("\nResult: could not reach Ollama. Is it running, and did you run 'ollama pull llama3.1'?")
elif report.metadata.llm_calls:
    print("\nResult: connected. Ollama answered and its evidence was checked against the text.")
