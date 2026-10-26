# NDA Review API (prototype)

Three endpoints, built with FastAPI. Clause finding and rule checks use plain code; an LLM can be
plugged in later for ambiguous clauses only. AI-assisted support, not legal advice.

| Endpoint | Purpose |
|---|---|
| `POST /api/reviews` | Run a review for a document (+ optional playbook). Synchronous. |
| `GET /api/reviews/{id}` | Fetch the full report. |
| `PATCH /api/reviews/{id}/findings/{findingId}` | Save the reviewer's decision and comment. |

Not in this build (owned by others): upload, text preview, export.

## Run

```bash
pip install -r requirements.txt
uvicorn app.main:app --reload          # docs at http://127.0.0.1:8000/docs
python -m unittest discover -s tests -t . -v
```

Two fictional sample NDAs are seeded: `doc_sample_nda` (all clauses clear) and `doc_sample_unclear_nda`
(one unclear clause, for the LLM). Set `SEED_SAMPLE_DOCUMENT=false` to disable.

```bash
curl -X POST localhost:8000/api/reviews -H 'content-type: application/json' \
     -d '{"document_id": "doc_sample_nda"}'
curl localhost:8000/api/reviews/<review_id>
curl -X PATCH localhost:8000/api/reviews/<review_id>/findings/F-002 -H 'content-type: application/json' \
     -d '{"status": "accepted", "comment": "Ask for destruction wording"}'
```

## Response envelope (every endpoint, success and error)

```json
{ "statusCode": 201, "success": true,  "message": "Review completed",
  "data": { "review_id": "rev_ab12cd34ef56", "status": "completed", "playbook_version": "1.0" },
  "error": null }

{ "statusCode": 404, "success": false, "message": "Document 'x' was not found",
  "data": null, "error": { "code": "DOCUMENT_NOT_FOUND", "details": null } }
```

`data` follows the spec: create -> `review_id, status, playbook_version`; get -> `metadata, clauses,
rule_results, findings, warnings`; patch -> `finding_id, status, comment`. Validation errors return
`error.code = "VALIDATION_ERROR"` with `details: [{field, message}]`.

Error codes: `DOCUMENT_NOT_FOUND` 404, `PLAYBOOK_NOT_FOUND` 404, `REVIEW_NOT_FOUND` 404,
`FINDING_NOT_FOUND` 404, `DOCUMENT_NOT_READY` 409, `DOCUMENT_TEXT_EMPTY` 422,
`NO_CLAUSES_DETECTED` 422, `VALIDATION_ERROR` 422, `INTERNAL_ERROR` 500.

## Folders

```
app/
  main.py            create_app(), router, handlers
  container.py       wires repositories + service (one per app)
  api/               routes/reviews.py, deps.py            (FastAPI only lives here + main/handlers)
  core/              config, exceptions, handlers
  schemas/           common.py (envelope), review.py, playbook.py
  services/          segmenter -> classifier -> checks -> rule_engine -> findings -> review_service
                     llm_client.py (LLM seam), ollama_resolver.py (free local llama3.1)
  repositories/      document_repo, playbook_repo, review_repo (in-memory)
  utils/             response, text, numbers, ids, clock
config/playbooks/    nda_baseline.json   <- rules, keywords, thresholds
tests/
```

## Upload, text and clause extraction (merged from the extraction prototype)

| Endpoint | What it does |
|---|---|
| `POST /api/documents` | Multipart `file` (PDF or DOCX, 10 MB). Extracts the text and returns a `document_id`. |
| `GET /api/documents/{id}/text` | Page text and the section list (used by the UI preview). |
| `GET /api/documents/{id}/clauses?method=manual` | Lists clauses from `Clause N — Heading` lines. Plain code, no model. |
| `GET /api/documents/{id}/clauses?method=llm` | Asks local Ollama for clauses. Any clause that is not copied word for word from the document is rejected (`UNVERIFIED_CLAUSE`). |

Optional settings (`.env`):

- `DOCX_ENGINE=libreoffice` converts DOCX to PDF first so Word files get page numbers. It needs LibreOffice (`LIBREOFFICE_PATH`, or `soffice` on PATH). If it is missing or fails, the upload still works through python-docx and returns a warning.
- `DOCUMENT_STORAGE_DIR=data/documents` also saves each upload to disk (`original.*`, `extracted.json`, `clauses_<method>.json`) and reloads them at startup. Reviews are still kept in memory only.

Every response carries an `X-Request-ID` header.

## Hooking up the upload endpoint

The upload developer only has to store the extracted text:

```python
container.documents.add(StoredDocument(
    document_id=..., file_name=..., extraction_status="success",   # or "failed"
    pages=[PageText(page=1, text="..."), ...]))                    # page=None for DOCX
```

## Changing policy without code

Edit `config/playbooks/nda_baseline.json`: keywords per clause type, `min_months` for R3, the
jurisdiction list for R5, severity, guidance, and what happens when a clause is missing
(`on_missing_clause`). The file is validated at startup; a bad file stops the app with a clear message.
Bump `version` when you change it; the version is saved in every review.

## Local LLM fallback (free): Ollama + llama3.1

Off by default. Both Ollama (MIT) and Llama 3.1 (Llama 3.1 Community License, royalty-free) are free to use.
The default `llama3.1` tag is the 8B build, about 4.9 GB.

```bash
ollama pull llama3.1                    # once; needs a recent Ollama (JSON-schema "format" support)
LLM_PROVIDER=ollama uvicorn app.main:app --reload
```

Instead of setting terminal variables, you can put them in a `.env` file in the project folder
(copy `.env.example` to `.env`). Real environment variables win over the file. On startup the uvicorn
console prints `LLM provider = ...` and which `.env` was found, so you can see at once whether it is on.

| Env var | Default |
|---|---|
| `LLM_PROVIDER` | `none` (or `ollama`) |
| `OLLAMA_BASE_URL` | `http://localhost:11434` |
| `OLLAMA_MODEL` | `llama3.1` |
| `OLLAMA_TIMEOUT_SECONDS` | `120` |
| `OLLAMA_NUM_CTX` | `4096` |

When it is used: only for (1) an ambiguous clause, and (2) a rule with `"llm_on_review_required": true`
(R1, R2) when the code check could not decide. Each call sends one clause plus one question
(`llm_criterion` in the playbook), with temperature 0 and a fixed seed. The answer must be JSON and must
quote a sentence that exists in the clause; otherwise it is rejected and the rule goes to human review
with a warning (`LLM_ERROR`, `LLM_INVALID_OUTPUT`, `EVIDENCE_UNVERIFIED`). The sample NDA makes 0 LLM calls.
`metadata.llm_calls`, `model` and `prompt_version` are recorded in every review.

### How to tell Ollama is working

1. `GET /health` -> `data.llm` shows the provider, the model, and for Ollama whether it is reachable and
   whether the model is downloaded:
   `{"provider": "ollama", "configured": true, "model": "llama3.1", "reachable": true, "model_available": true}`.
   `reachable: false` means Ollama is not running; `model_available: false` means run `ollama pull llama3.1`.
   `provider: "none"` means `LLM_PROVIDER` was not set before the server started.
2. Review the seeded document with one unclear clause: `POST /api/reviews` with
   `{"document_id": "doc_sample_unclear_nda"}`, then `GET` the report. When Ollama is used, R1 shows
   `method: "llm"`, `metadata.llm_calls` is 1 and `metadata.model` is `llama3.1`.
   Without Ollama the same document gives R1 `review_required` plus a `LLM_NOT_CONFIGURED` warning.
   (`doc_sample_nda` never needs the LLM, so `llm_calls` stays 0 there.)
3. `python try_ollama.py` does the same check without the HTTP server.
4. While a review runs, `ollama ps` lists the loaded model.

An 8B model is weaker than hosted models: expect misses and wrong verdicts on tricky clauses, and
some answers rejected for imperfect quotes. Measure it on the golden set before relying on it.
Responses can take several seconds per clause on CPU.

## Behaviour worth knowing

- One result per rule, always. A missing clause follows `on_missing_clause`; it is never a silent pass.
- Without the LLM, an ambiguous clause becomes `review_required` plus a warning. Another provider can be
  added by implementing `ClauseResolver` in `services/llm_client.py`.
- A document with no detectable clause headings returns `NO_CLAUSES_DETECTED` instead of a misleading report.
- Storage is in memory (lost on restart). Swap the repositories for SQLite/JSON when needed.
- Clause summaries are extractive (first sentence); the LLM is not used for summaries.
- `presence_and_policy` currently reads durations (`min_months`); other check types are generic.
