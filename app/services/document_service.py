import uuid

from app.core.exceptions import NotFoundError, UnprocessableError
from app.llm.gateway import LLMGateway, NoopGateway
from app.repositories.disk_store import DiskDocumentStore
from app.repositories.document_repo import InMemoryDocumentRepository, StoredDocument
from app.schemas.document import (
    ClauseData,
    ClauseSource,
    DocumentClausesData,
    DocumentTextData,
    DocumentUploadData,
    PageTextData,
    SectionRef,
)
from app.services.clause_extractor import extract_clauses
from app.services.extractor import extract_document
from app.services.segmenter import segment_document

METHODS = ("manual", "llm")


class DocumentService:
    def __init__(
        self,
        documents: InMemoryDocumentRepository,
        store: DiskDocumentStore | None = None,
        docx_engine: str = "python",
        gateway: LLMGateway | None = None,
    ):
        self._documents = documents
        self._store = store
        self._docx_engine = docx_engine
        self._gateway = gateway or NoopGateway()
        if store:
            for document in store.load_all():
                documents.add(document)

    def upload(self, file_name: str, data: bytes) -> DocumentUploadData:
        """Validate, extract text, store. Invalid or unreadable files raise an AppError and store nothing."""
        extracted = extract_document(file_name, data, self._docx_engine)
        document = StoredDocument(
            document_id=f"doc_{uuid.uuid4().hex[:12]}",
            file_name=extracted.file_name,
            extraction_status="success",
            pages=extracted.pages,
            file_type=extracted.file_type,
            warnings=list(extracted.warnings),
        )
        if self._store:
            self._store.save(document, data)  # before the in-memory add: a failed save leaves nothing behind
        self._documents.add(document)
        return DocumentUploadData(
            document_id=document.document_id,
            file_name=document.file_name,
            extraction_status=document.extraction_status,
            file_type=extracted.file_type,
            page_count=extracted.page_count,
            section_count=len(segment_document(document.pages)),
            character_count=sum(len(p.text) for p in document.pages),
            warnings=document.warnings,
        )

    def _find(self, document_id: str) -> StoredDocument:
        document = self._documents.get(document_id)
        if document is None:
            raise NotFoundError("DOCUMENT_NOT_FOUND", f"Document '{document_id}' was not found")
        return document

    def get_text(self, document_id: str) -> DocumentTextData:
        document = self._find(document_id)
        return DocumentTextData(
            document_id=document.document_id,
            file_name=document.file_name,
            pages=[PageTextData(page=p.page, text=p.text) for p in document.pages],
            sections=[
                SectionRef(label=s.label, title=s.title, page=s.heading_page)
                for s in segment_document(document.pages)
            ],
        )

    def get_clauses(self, document_id: str, method: str = "manual") -> DocumentClausesData:
        """List the clauses in a document. manual = plain code; llm = through the LLM gateway (checked against the text)."""
        if method not in METHODS:
            raise UnprocessableError("INVALID_METHOD", "method must be 'manual' or 'llm'")
        document = self._find(document_id)
        result = extract_clauses(document.pages, method, gateway=self._gateway)

        clauses = []
        for item in result["clauses"]:
            number = str(item["number"]).strip() if item["number"] is not None else ""
            pages = item["source_pages"]
            clauses.append(
                ClauseData(
                    clause_type=item["clause_type"],
                    status="found",
                    evidence=item["clause_content"],
                    summary=item["summary"] if method == "llm" else None,
                    source=ClauseSource(page=min(pages) if pages else None, section=f"Clause {number}" if number else None),
                )
            )
        data = DocumentClausesData(
            document_id=document.document_id,
            file_name=document.file_name,
            file_type=document.file_type,
            method=method,
            clause_count=len(clauses),
            clauses=clauses,
            warnings=[*document.warnings, *result["warnings"]],
        )
        if self._store:
            self._store.save_clauses(document.document_id, method, data.model_dump(mode="json"))
        return data
