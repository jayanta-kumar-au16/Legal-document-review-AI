from typing import Any, Literal
from pydantic import BaseModel


class Segment(BaseModel):
    segment_id: str
    page: int | None = None
    text: str


class Metadata(BaseModel):
    document_id: str
    file_name: str
    source_type: str
    reference_document: str
    extraction_status: str
    page_count: int | None
    extracted_page_count: int | None
    segment_count: int
    warnings: list[str]


class ExtractedText(Metadata):
    segments: list[Segment]


class DocumentUploadResponse(BaseModel):
    status: Literal["success"] = "success"
    message: str
    data: Metadata
    request_id: str

class ClauseSource(BaseModel):
    page: int | None = None
    section: str | None = None

class Clause(BaseModel):
    clause_type: str
    status: Literal["found", "not_found"]
    evidence: str | None = None
    summary: str | None = None
    source: ClauseSource | None = None


class ClauseExtractionData(BaseModel):
    document_id: str
    file_name: str
    source_type: str
    reference_document: str
    clause_count: int
    clauses: list[Clause]
    warnings: list[str]


class ClauseExtractionResponse(BaseModel):
    status: Literal["success"] = "success"
    message: str
    data: ClauseExtractionData
    request_id: str


class ErrorDetail(BaseModel):
    code: str
    details: list[dict[str, Any]] | None = None


class ErrorResponse(BaseModel):
    status: Literal["error"] = "error"
    message: str
    data: None = None
    error: ErrorDetail
    request_id: str


