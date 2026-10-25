#Local prototype: upload, validate, extract and retrieve PDF/DOCX text.
import io
import json
from typing import Literal
from fastapi import Query
from app.clause_service import process_clauses
import logging
import os
import re
import shutil
import tempfile
import time
import zipfile
from pathlib import Path
from uuid import UUID, uuid4
import subprocess
from fastapi.responses import FileResponse, JSONResponse
from fastapi import FastAPI, File, HTTPException, UploadFile, Request
from pypdf import PdfReader
from app.schemas import*
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException
logger = logging.getLogger(__name__)
DOCX_MIME = 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'


def fail(status, code, message):
    raise HTTPException(status, detail={'code': code, 'message': message})


def normalize(text):
    # Preserve paragraph boundaries and punctuation & handling missing words.
    return '\n'.join(re.sub(r'[ \t]+', ' ', line).strip()
                     for line in text.replace('\r\n', '\n').replace('\r', '\n').split('\n')).strip()

def extract(data, kind):
    segments, warnings = [], []
    page_count = extracted_pages = None
    if kind == 'pdf':
        if not data.startswith(b'%PDF-'):
            fail(422, 'invalid_document', 'The file is not a readable PDF.')
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted:
            fail(422, 'encrypted_pdf', 'Upload an unencrypted PDF.')
        page_count = len(reader.pages)
        if page_count > 100:
            fail(422, 'page_limit', 'Prototype supports PDFs with at most 100 pages.')
        extracted_pages = 0
        for number, page in enumerate(reader.pages, 1):
            text = normalize(page.extract_text() or '')
            if text:
                extracted_pages += 1
                segments.append(Segment(segment_id=f'PAGE-{number}', page=number, text=text))
            else:
                warnings.append(f'Page {number} has no extractable text; it may be blank or require OCR.')
    # else:
    #     with zipfile.ZipFile(io.BytesIO(data)) as archive:
    #         entries = archive.infolist()
    #         if (sum(e.file_size for e in entries) > 50 * 1024 * 1024
    #                 or len(entries) > 2000):
    #             fail(422, 'expanded_file_limit', 'DOCX archive exceeds the extraction limit.')
    #         if 'word/document.xml' not in archive.namelist():
    #             fail(422, 'invalid_document', 'The file is not a DOCX document.')
    #     document = Document(io.BytesIO(data))
    #     paragraph_number = table_number = 0
    #     # iter_inner_content preserves paragraph/table order in the document body.
    #     for block in document.iter_inner_content():
    #         if hasattr(block, 'rows'):
    #             table_number += 1
    #             for ri, row in enumerate(block.rows, 1):
    #                 seen = set()
    #                 for ci, cell in enumerate(row.cells, 1):
    #                     if cell._tc in seen:
    #                         continue  # avoid duplicate text from horizontally merged cells
    #                     seen.add(cell._tc)
    #                     text = normalize(cell.text)
    #                     if text:
    #                         segments.append(Segment(segment_id=f'T{table_number}-R{ri}-C{ci}',
    #                             table=table_number, row=ri, cell=ci, text=text))
    #         else:
    #             paragraph_number += 1
    #             text = normalize(block.text)
    #             if text:
    #                 segments.append(Segment(segment_id=f'P-{paragraph_number}',
    #                                         paragraph=paragraph_number, text=text))
    #     warnings.append('DOCX uses body paragraph/table references; page numbers are unavailable. Headers, footers, text boxes and nested tables are outside this parser scope.')
    if not segments:
        fail(422, 'no_extractable_text', 'No readable text found. Scanned documents require OCR, which is outside this prototype.')
    return segments, warnings, page_count, extracted_pages

def convert_docx_to_pdf(data: bytes) -> bytes:
    #Convert DOCX bytes into a PDF using a separate LibreOffice profile

    # Validate the DOCX archive before sending it to the converter.
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            entries = archive.infolist()

            if (
                sum(entry.file_size for entry in entries)
                > 50 * 1024 * 1024
                or len(entries) > 2000
            ):
                fail(
                    422,
                    "expanded_file_limit",
                    "DOCX archive exceeds the extraction limit.",
                )

            if "word/document.xml" not in archive.namelist():
                fail(
                    422,
                    "invalid_document",
                    "The file is not a DOCX document.",
                )

    except zipfile.BadZipFile:
        fail(
            422,
            "invalid_document",
            "The DOCX file is corrupt or invalid.",
        )

    executable = (
        os.getenv("LIBREOFFICE_PATH")
        or shutil.which("soffice")
        or shutil.which("libreoffice")
    )

    if not executable:
        fail(
            503,
            "converter_unavailable",
            "LibreOffice is unavailable. Configure LIBREOFFICE_PATH.",
        )

    # Every conversion gets its own files and LibreOffice profile.
    with tempfile.TemporaryDirectory(prefix="nda-conversion-") as folder:
        working_dir = Path(folder).resolve()

        input_path = working_dir / "source.docx"
        output_dir = working_dir / "output"
        profile_dir = working_dir / "profile"

        output_dir.mkdir()
        input_path.write_bytes(data)

        command = [
            executable,
            f"-env:UserInstallation={profile_dir.as_uri()}",
            "--headless",
            "--nologo",
            "--nodefault",
            "--nofirststartwizard",
            "--convert-to",
            "pdf:writer_pdf_Export",
            "--outdir",
            str(output_dir),
            str(input_path),
        ]

        try:
            result = subprocess.run(
                command,
                capture_output=True,
                timeout=60,
                check=False,
                shell=False,
            )

        except subprocess.TimeoutExpired:
            fail(
                504,
                "conversion_timeout",
                "DOCX conversion exceeded 60 seconds.",
            )

        except OSError:
            fail(
                503,
                "converter_unavailable",
                "Could not start LibreOffice.",
            )

        pdf_path = output_dir / "source.pdf"

        # Check the output file as well as the process exit code.
        if result.returncode != 0 or not pdf_path.is_file():
            fail(
                422,
                "conversion_failed",
                "The DOCX could not be converted to PDF.",
            )

        if pdf_path.stat().st_size > 50 * 1024 * 1024:
            fail(
                422,
                "converted_file_too_large",
                "The converted PDF exceeds the extraction limit.",
            )

        pdf_data = pdf_path.read_bytes()

        if not pdf_data.startswith(b"%PDF-"):
            fail(
                422,
                "conversion_failed",
                "The converter did not produce a valid PDF.",
            )

        return pdf_data

def build_public_clauses(
    items: list[dict],
    method: str,
) -> list[Clause]:
    clauses = []

    for item in items:
        pages = item["source_pages"]
        number = item["number"]

        section = None
        if number is not None and str(number).strip():
            section = f"Clause {str(number).strip()}"

        clauses.append(
            Clause(
                clause_type=item["clause_type"],
                status="found",
                evidence=item["clause_content"],
                summary=(
                    item.get("summary")
                    if method == "llm"
                    else None
                ),
                source=ClauseSource(
                    page=min(pages) if pages else None,
                    section=section,
                ),
            )
        )

    return clauses

def create_app(storage_dir=None, max_upload_bytes=None):
    app = FastAPI(
        title="Legal Document Upload and Extraction API",
        version="1.0.0",
    )

    storage = Path(
        storage_dir
        or os.getenv("DOCUMENT_STORAGE_DIR", "data/documents")
    )

    limit = (
        max_upload_bytes
        if max_upload_bytes is not None
        else int(os.getenv("MAX_UPLOAD_BYTES", 10 * 1024 * 1024))
    )

    if limit <= 0:
        raise ValueError("MAX_UPLOAD_BYTES must be positive")

    # ---------------------------------------------------------
    # Request ID and unexpected error handling
    # ---------------------------------------------------------

    @app.middleware("http")
    async def request_context(request: Request, call_next):
        request.state.request_id = f"req_{uuid4().hex}"

        try:
            response = await call_next(request)

        except Exception:
            logger.exception(
                "request=%s error=unexpected_failure",
                request.state.request_id,
            )

            response = JSONResponse(
                status_code=500,
                content={
                    "status": "error",
                    "message": "An unexpected error occurred. Please retry.",
                    "data": None,
                    "error": {
                        "code": "internal_server_error",
                    },
                    "request_id": request.state.request_id,
                },
            )

        response.headers["X-Request-ID"] = request.state.request_id
        return response

    # ---------------------------------------------------------
    # Expected HTTP errors
    # ---------------------------------------------------------

    @app.exception_handler(StarletteHTTPException)
    async def http_exception_handler(
        request: Request,
        exc: StarletteHTTPException,
    ):
        if isinstance(exc.detail, dict):
            code = exc.detail.get("code", f"http_{exc.status_code}")
            message = exc.detail.get(
                "message",
                "The request could not be processed.",
            )
        else:
            code = f"http_{exc.status_code}"
            message = str(exc.detail)

        return JSONResponse(
            status_code=exc.status_code,
            headers=exc.headers,
            content={
                "status": "error",
                "message": message,
                "data": None,
                "error": {
                    "code": code,
                },
                "request_id": request.state.request_id,
            },
        )

    # ---------------------------------------------------------
    # Request validation errors
    # ---------------------------------------------------------

    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(
        request: Request,
        exc: RequestValidationError,
    ):
        return JSONResponse(
            status_code=422,
            content={
                "status": "error",
                "message": (
                    "Request validation failed. "
                    "Check the submitted fields."
                ),
                "data": None,
                "error": {
                    "code": "validation_error",
                    "details": [
                        {
                            "location": list(error["loc"]),
                            "message": error["msg"],
                            "type": error["type"],
                        }
                        for error in exc.errors()
                    ],
                },
                "request_id": request.state.request_id,
            },
        )

    # ---------------------------------------------------------
    # Health check
    # ---------------------------------------------------------

    @app.get("/health")
    def health():
        return {"status": "ok"}

    # ---------------------------------------------------------
    # Upload and extract document text
    # ---------------------------------------------------------

    @app.post(
        "/api/documents",
        response_model=DocumentUploadResponse,
        status_code=201,
    )
    def upload_document(
        request: Request,
        file: UploadFile = File(...),
    ):
        started = time.monotonic()
        document_id = str(uuid4())
        request_id = request.state.request_id
        staging = None

        try:
            name = (
                (file.filename or "")
                .replace("\\", "/")
                .split("/")[-1]
            )
            suffix = Path(name).suffix.lower()

            if suffix not in {".pdf", ".docx"}:
                fail(
                    415,
                    "unsupported_format",
                    "Only PDF and DOCX files are supported.",
                )

            expected = (
                "application/pdf"
                if suffix == ".pdf"
                else DOCX_MIME
            )

            mime = (
                (file.content_type or "")
                .split(";")[0]
                .strip()
                .lower()
            )

            if mime not in {
                expected,
                "application/octet-stream",
                "",
            }:
                fail(
                    415,
                    "mime_mismatch",
                    "The content type does not match the file extension.",
                )

            # Read at most the configured limit plus one byte.
            data = file.file.read(limit + 1)

            if len(data) > limit:
                fail(
                    413,
                    "file_too_large",
                    f"Maximum file size is {limit} bytes.",
                )

            if not data:
                fail(
                    422,
                    "empty_file",
                    "Upload a non-empty document.",
                )

            kind = suffix[1:]

            if kind == "docx":
                review_pdf = convert_docx_to_pdf(data)
                reference_document = "converted_pdf"
            else:
                review_pdf = data
                reference_document = "original_pdf"

            try:
                segments, warnings, pages, extracted_pages = extract(
                    review_pdf,
                    "pdf",
                )

            except HTTPException:
                raise

            except Exception as exc:
                logger.info(
                    "request=%s document=%s step=parse error=%s",
                    request_id,
                    document_id,
                    type(exc).__name__,
                )

                fail(
                    422,
                    "parse_error",
                    "The document could not be parsed for text extraction.",
                )

            result = ExtractedText(
                document_id=document_id,
                file_name=name,
                source_type=kind,
                reference_document=reference_document,
                extraction_status=(
                    "completed_with_warnings"
                    if warnings
                    else "completed"
                ),
                page_count=pages,
                extracted_page_count=extracted_pages,
                segment_count=len(segments),
                warnings=warnings,
                segments=segments,
            )

            # Validate the public response before publishing files.
            public_response = DocumentUploadResponse(
                status="success",
                message=(
                    "Document uploaded and text extracted successfully "
                    "with warnings."
                    if warnings
                    else "Document uploaded and text extracted successfully."
                ),
                data=Metadata(**result.model_dump()),
                request_id=request_id,
            )

            try:
                storage.mkdir(parents=True, exist_ok=True)

                staging = Path(
                    tempfile.mkdtemp(
                        prefix=".upload-",
                        dir=storage,
                    )
                )

                (staging / f"original{suffix}").write_bytes(data)
                (staging / "review.pdf").write_bytes(review_pdf)

                (staging / "extracted.json").write_text(
                    result.model_dump_json(indent=2),
                    encoding="utf-8",
                )

                # Publish only after all files are saved.
                staging.rename(storage / document_id)
                staging = None

            except OSError:
                logger.exception(
                    "request=%s document=%s "
                    "step=storage error=storage_failure",
                    request_id,
                    document_id,
                )

                fail(
                    500,
                    "storage_failure",
                    "Could not save the document. Please retry.",
                )

            logger.info(
                "request=%s document=%s step=complete duration_ms=%d",
                request_id,
                document_id,
                int((time.monotonic() - started) * 1000),
            )

            return public_response

        finally:
            try:
                file.file.close()
            except OSError:
                logger.warning(
                    "request=%s document=%s "
                    "step=cleanup error=file_close_failure",
                    request_id,
                    document_id,
                )

            if staging is not None:
                shutil.rmtree(staging, ignore_errors=True)

    # ---------------------------------------------------------
    # Extract clauses using manual or LLM processing
    # ---------------------------------------------------------
    @app.get(
        "/api/documents/{document_id}/text",
        response_model=ClauseExtractionResponse,
        status_code=200,
    )
    def get_text(
        request: Request,
        document_id: UUID,
        method: Literal["manual", "llm"] = Query(default="manual"),
    ):
        request_id = request.state.request_id
        path = storage / str(document_id) / "extracted.json"

        if not path.is_file():
            fail(
                404,
                "document_not_found",
                "Document not found.",
            )

        try:
            extracted = ExtractedText.model_validate_json(
                path.read_text(encoding="utf-8")
            )
        except (OSError, ValueError):
            fail(
                500,
                "stored_document_error",
                "Stored extraction could not be loaded.",
            )

        pages = [
            {
                "page": segment.page,
                "text": segment.text,
            }
            for segment in extracted.segments
        ]

        try:
            result = process_clauses(
                document_id=str(document_id),
                pages=pages,
                method=method,
            )
        except ValueError as exc:
            fail(
                422,
                "invalid_extraction_input",
                str(exc),
            )

        # Convert either method's result to the shared public schema.
        clauses = build_public_clauses(
            items=result["clauses"],
            method=method,
        )

        warnings = [
            *extracted.warnings,
            *result.get("warnings", []),
        ]

        public_response = ClauseExtractionResponse(
            status="success",
            message=(
                "Document clause extraction completed with warnings."
                if warnings
                else "Document clauses extracted successfully."
            ),
            data=ClauseExtractionData(
                document_id=str(document_id),
                file_name=extracted.file_name,
                source_type=extracted.source_type,
                reference_document=extracted.reference_document,
                clause_count=len(clauses),
                clauses=clauses,
                warnings=warnings,
            ),
            request_id=request_id,
        )

        # Preserve the internal extraction result separately.
        stored_result = {
            **result,
            "file_name": extracted.file_name,
            "source_type": extracted.source_type,
            "reference_document": extracted.reference_document,
            "warnings": warnings,
        }

        temporary_path = None

        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=path.parent,
                prefix=".clauses-",
                suffix=".json",
                delete=False,
            ) as output:
                temporary_path = Path(output.name)

                json.dump(
                    stored_result,
                    output,
                    ensure_ascii=False,
                    indent=2,
                )
                output.write("\n")

            temporary_path.replace(
                path.parent / f"clauses_{method}.json"
            )

        except OSError:
            logger.exception(
                "request=%s document=%s "
                "step=storage error=clause_storage_failure",
                request_id,
                document_id,
            )
            fail(
                500,
                "storage_failure",
                "Could not save the clause result. Please retry.",
            )

        finally:
            if temporary_path is not None:
                try:
                    temporary_path.unlink(missing_ok=True)
                except OSError:
                    logger.warning(
                        "request=%s document=%s "
                        "step=cleanup error=temporary_file_cleanup_failure",
                        request_id,
                        document_id,
                    )

        return public_response
   
   
    return app

app = create_app()

