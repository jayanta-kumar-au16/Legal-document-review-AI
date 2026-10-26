import threading
from typing import Literal, Protocol

from pydantic import BaseModel, Field


class PageText(BaseModel):
    """One extracted page. DOCX has no pages, so `page` may be None."""

    page: int | None = None
    text: str


class StoredDocument(BaseModel):
    document_id: str
    file_name: str
    extraction_status: Literal["success", "failed"]
    pages: list[PageText] = Field(default_factory=list)


class DocumentRepository(Protocol):
    def get(self, document_id: str) -> StoredDocument | None: ...


class InMemoryDocumentRepository:
    """Integration point with the upload endpoint (owned by another developer):
    after extracting text, the upload handler calls `container.documents.add(...)`.
    """

    def __init__(self) -> None:
        self._items: dict[str, StoredDocument] = {}
        self._lock = threading.Lock()

    def add(self, document: StoredDocument) -> None:
        with self._lock:
            self._items[document.document_id] = document

    def get(self, document_id: str) -> StoredDocument | None:
        with self._lock:
            return self._items.get(document_id)
