# Legal Document Upload and Clause Extraction API

FastAPI prototype for uploading PDF/DOCX documents, extracting text, and organizing clauses using manual processing or a local LLM through Ollama.

Python 3.11+ is required. Manual processing does not require an LLM. LLM processing uses Ollama and does not require an OpenAI API key.

Use synthetic or approved public documents.

## Start on Windows PowerShell

Open a terminal in the project directory and run:

```powershell
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m uvicorn app.main:app --reload
```

Using the virtual environment interpreter directly avoids activation policy issues.

Open http://127.0.0.1:8000/docs to test through Swagger UI.

## Start on macOS or Linux

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m uvicorn app.main:app --reload
```

## Document conversion

DOCX uploads are converted to PDF using LibreOffice before text extraction. PDF uploads are processed directly.

LibreOffice must be available on PATH, or its executable path must be configured:

```powershell
$env:LIBREOFFICE_PATH = "C:\Program Files\LibreOffice\program\soffice.exe"
```

DOCX page references refer to the converted PDF.

## Ollama configuration

LLM extraction calls Ollama's `/api/chat` endpoint.

The configured defaults are:

```text
OLLAMA_BASE_URL=http://localhost:11434
OLLAMA_MODEL=qwen3:4b
OLLAMA_TIMEOUT_SECONDS=180
```

Ollama must be running, and the selected model must be available locally.

These values can be configured in `.env`:

```dotenv
OLLAMA_BASE_URL=http://localhost:11434
OLLAMA_MODEL=qwen3:4b
OLLAMA_TIMEOUT_SECONDS=180
```

The clause service loads environment settings using `python-dotenv`.

## APIs

### GET /health

Returns:

```json
{
  "status": "ok"
}
```

### POST /api/documents

Accepts a multipart upload using the field name `file`.

Validates the document, converts DOCX to PDF when needed, extracts text, and saves the document files.

Returns HTTP `201 Created`:

```json
{
  "status": "success",
  "message": "Document uploaded and text extracted successfully.",
  "data": {
    "document_id": "efbe71a7-6f96-4f24-9518-158aef92c37c",
    "file_name": "sample_nda.docx",
    "source_type": "docx",
    "reference_document": "converted_pdf",
    "extraction_status": "completed",
    "page_count": 1,
    "extracted_page_count": 1,
    "segment_count": 1,
    "warnings": []
  },
  "request_id": "req_example"
}
```

`reference_document` is `original_pdf` for PDF uploads and `converted_pdf` for DOCX uploads.

If some pages contain no extractable text, `extraction_status` is `completed_with_warnings`.

### GET /api/documents/{document_id}/text

Processes the stored text into clauses.

Query parameters:

| Parameter | Values | Default |
|---|---|---|
| `method` | `manual`, `llm` | `manual` |

Examples:

```text
GET /api/documents/{document_id}/text?method=manual
GET /api/documents/{document_id}/text?method=llm
```

Both methods return HTTP `200 OK` with the same public response structure:

```json
{
  "status": "success",
  "message": "Document clauses extracted successfully.",
  "data": {
    "document_id": "efbe71a7-6f96-4f24-9518-158aef92c37c",
    "file_name": "sample_nda.docx",
    "source_type": "docx",
    "reference_document": "converted_pdf",
    "clause_count": 1,
    "clauses": [
      {
        "clause_type": "confidentiality",
        "status": "found",
        "evidence": "The Receiving Party shall protect all confidential information and shall not disclose it to third parties without prior written consent.",
        "summary": "The receiving party must protect confidential information and obtain prior written consent before disclosure to third parties.",
        "source": {
          "page": 1,
          "section": "Clause 2"
        }
      }
    ],
    "warnings": []
  },
  "request_id": "req_example"
}
```

The example contains one clause. Actual results depend on the supplied document.

#### Manual processing

- Recognizes supported headings such as `Clause 2 — Confidentiality`.
- Derives `clause_type` from the heading.
- Collects the body text under each detected heading.
- Returns `summary: null`.
- Preserves text before the first recognized heading in internal `unclassified_blocks`.
- Returns warnings when text is unclassified or supported headings are not found.

Manual processing does not infer arbitrary heading formats or semantic categories.

#### LLM processing

- Uses the configured Ollama model to identify clauses.
- Supports content-based classification with dynamic clause types.
- Requests a separate factual summary for each clause.
- Verifies that each clause body matches a contiguous source excerpt after whitespace normalization.
- Enforces document order during evidence matching.
- Calculates source pages in Python from the matched evidence.

Evidence matching verifies copied text. It does not independently verify the accuracy of generated summaries or classifications.

#### Clause fields

| Field | Meaning |
|---|---|
| `clause_type` | Dynamic category; there is no fixed list of clause types. |
| `status` | Currently `found` for extracted clauses. |
| `evidence` | Extracted clause body. |
| `summary` | LLM-generated summary, or `null` for manual processing. |
| `source.page` | First evidence page; `null` when unavailable. |
| `source.section` | Clause reference built from the extracted number; `null` when no number exists. |

The schema permits `not_found`, but the current extraction logic does not check for missing required clause categories.

Both methods share the same response fields. Their clause boundaries, classifications, and warnings may differ.

Implementation details such as provider, model, prompt version, and classification basis are not included in the public clause response.

## Swagger testing

1. Open http://127.0.0.1:8000/docs.
2. Expand `POST /api/documents`.
3. Click **Try it out**.
4. Select a PDF or DOCX for the `file` field.
5. Execute the request.
6. Copy `data.document_id` from the response.
7. Expand `GET /api/documents/{document_id}/text`.
8. Enter the document ID and select `manual` or `llm`.
9. Execute the request.

## PowerShell examples

Upload a document:

```powershell
curl.exe -X POST "http://127.0.0.1:8000/api/documents" -F "file=@test-data/sample_nda.pdf"
```

Run manual extraction:

```powershell
curl.exe "http://127.0.0.1:8000/api/documents/DOCUMENT_ID/text?method=manual"
```

Run LLM extraction:

```powershell
curl.exe "http://127.0.0.1:8000/api/documents/DOCUMENT_ID/text?method=llm"
```

Save the public response locally:

```powershell
curl.exe "http://127.0.0.1:8000/api/documents/DOCUMENT_ID/text?method=manual" --output manual_response.json
```

Replace `DOCUMENT_ID` with the ID returned by the upload.

## Validation and errors

Default maximum upload size: 10 MiB (`10 * 1024 * 1024` bytes).

The application checks:

- Supported file extension.
- Declared MIME type.
- Non-empty content.
- PDF readability and encryption.
- PDF page limit.
- DOCX archive structure and expansion limits.
- Availability of extractable text.

Generic or absent MIME types are accepted, but document parsing must still succeed.

Errors use the following response structure:

```json
{
  "status": "error",
  "message": "Only PDF and DOCX files are supported.",
  "data": null,
  "error": {
    "code": "unsupported_format"
  },
  "request_id": "req_example"
}
```

Request validation errors also include `error.details`.

| HTTP status | Usage |
|---|---|
| `404` | Document not found. |
| `413` | Upload exceeds the configured size limit. |
| `415` | Unsupported extension or mismatched MIME type. |
| `422` | Invalid request, empty or unreadable document, or extraction-limit violation. |
| `500` | Storage failure, unreadable stored extraction, or unexpected server error. |
| `502` | Ollama request/output failure or clause evidence verification failure. |
| `503` | Ollama or the DOCX converter is unavailable. |
| `504` | Ollama request or DOCX conversion timeout. |

Responses include an `X-Request-ID` header for correlation with the JSON request ID and application logs.

## Storage

The default document directory is:

```text
data/documents/{document_id}/
```

A successful upload saves:

```text
original.pdf or original.docx
review.pdf
extracted.json
```

`extracted.json` contains metadata and raw text segments:

- `segment_id`
- `page`
- `text`

Calling the clause endpoint saves the selected method's internal result:

```text
clauses_manual.json
clauses_llm.json
```

These stored clause results retain internal fields and are not identical to the public response envelope. Manual results also preserve `unclassified_blocks`.

Each successful clause-processing call replaces that method's saved result using a temporary file. Raw extraction remains separate.

Uploads are published by renaming a staging directory after the files are written.

Generated document IDs determine storage paths. The client filename is retained as metadata.

Data persists across application restarts. There is no automatic expiry or deletion endpoint. Remove a document's local directory to delete its stored files.

The latest supplied `main.py` does not include the earlier `/text/download` endpoint. Saved JSON can be accessed directly in the document directory.

## Configuration

Set these variables before starting the application:

```powershell
$env:MAX_UPLOAD_BYTES = "10485760"
$env:DOCUMENT_STORAGE_DIR = "data/documents"
```

Ollama configuration is described above.

Logs include request/document identifiers and processing or error information. Upload completion logs include duration.

## Code layout

- `app/main.py`: validation, PDF extraction, DOCX conversion, response mapping, storage, middleware, exception handlers, API routes, and app factory.
- `app/schemas.py`: document, clause, success-response, and error-response schemas.
- `app/clause_service.py`: manual clause extraction, Ollama integration, internal model schemas, and evidence verification.

## Tests

The existing README provides these test commands:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m pytest -q
```

Run them from the project directory. Test results and coverage for the updated response schemas and Ollama changes have not been verified here.

## Prototype limitations

- No authentication or OCR.
- No semantic legal review.
- Manual extraction depends on supported heading patterns.
- LLM summaries and classifications may be inaccurate.
- Source matching does not establish complete clause coverage.
- PDF reading order depends on document layout.
- DOCX page references come from the converted PDF.
- A single `source.page` represents the first evidence page, not all pages of a multi-page clause.
- PDFs are limited to 100 pages.
- Expanded DOCX contents are limited to 50 MiB and 2,000 ZIP entries.
- DOCX conversion has a 60-second subprocess timeout.
- Multipart parsing occurs before the endpoint's upload-size check.
- Complex layouts and mixed text/image pages may not be fully extracted.

## Documentation references

- https://fastapi.tiangolo.com/tutorial/request-files/
- https://pypdf.readthedocs.io/en/stable/user/extract-text.html