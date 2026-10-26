from typing import Literal

from fastapi import APIRouter, Depends, File, Query, UploadFile
from starlette.concurrency import run_in_threadpool

from app.api.deps import get_document_service
from app.schemas.common import ApiResponse
from app.schemas.document import DocumentClausesData, DocumentTextData, DocumentUploadData
from app.services.document_service import DocumentService
from app.services.extractor import MAX_FILE_BYTES
from app.utils.response import success_response

router = APIRouter(prefix="/api/documents", tags=["documents"])


@router.post("", status_code=201, response_model=ApiResponse[DocumentUploadData], summary="Upload a PDF or DOCX")
async def upload_document(file: UploadFile = File(...), service: DocumentService = Depends(get_document_service)):
    """Multipart upload, field name `file`. Text-based PDF or DOCX only, 10 MB max.

    The text is extracted straight away; the file itself is not kept."""
    data = await file.read(MAX_FILE_BYTES + 1)  # bounded read: one byte over the limit is enough to reject
    result = await run_in_threadpool(service.upload, file.filename or "", data)
    return success_response(result, "Document uploaded and text extracted", 201)


@router.get("/{document_id}/text", response_model=ApiResponse[DocumentTextData], summary="Preview extracted text")
def get_document_text(document_id: str, service: DocumentService = Depends(get_document_service)):
    return success_response(service.get_text(document_id), "Document text fetched")


@router.get("/{document_id}/clauses", response_model=ApiResponse[DocumentClausesData], summary="List the clauses in a document")
def get_document_clauses(
    document_id: str,
    method: Literal["manual", "llm"] = Query("manual", description="manual = plain code (default); llm = OpenAI via the LLM gateway"),
    service: DocumentService = Depends(get_document_service),
):
    """`manual` finds 'Clause N — Heading' sections without any model. `llm` asks the LLM (OpenAI) and
    rejects any clause that is not copied word for word from the document."""
    result = service.get_clauses(document_id, method)
    message = "Clauses extracted with warnings" if result.warnings else "Clauses extracted"
    return success_response(result, message)
