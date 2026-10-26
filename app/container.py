from dataclasses import dataclass
from pathlib import Path

from app.core.config import Settings
from app.dev_seed import seed_sample_document
from app.repositories.disk_store import DiskDocumentStore
from app.repositories.document_repo import InMemoryDocumentRepository
from app.repositories.playbook_repo import PlaybookRepository
from app.repositories.review_repo import InMemoryReviewRepository
from app.services.document_service import DocumentService
from app.services.llm_client import ClauseResolver, NoopResolver
from app.services.ollama_resolver import OllamaResolver
from app.services.playbook_service import PlaybookService
from app.services.review_service import ReviewService


@dataclass
class Container:
    """Holds the shared objects. The upload endpoint (another developer) only needs `documents`."""

    documents: InMemoryDocumentRepository
    playbooks: PlaybookRepository
    reviews: InMemoryReviewRepository
    review_service: ReviewService
    resolver: ClauseResolver
    document_service: DocumentService
    playbook_service: PlaybookService

    def llm_status(self) -> dict:
        """What the app is configured to use, plus (for Ollama) whether it is reachable right now."""
        status = {
            "provider": getattr(self.resolver, "provider", "custom"),
            "configured": bool(self.resolver.configured),
        }
        if self.resolver.configured:
            status["model"] = self.resolver.model
            check = getattr(self.resolver, "check", None)
            if callable(check):
                status.update(check())
        return status


def build_resolver(settings: Settings) -> ClauseResolver:
    provider = settings.llm_provider.strip().lower()
    if provider == "none":
        return NoopResolver()
    if provider == "ollama":
        return OllamaResolver(
            base_url=settings.ollama_base_url,
            model=settings.ollama_model,
            timeout_seconds=settings.ollama_timeout_seconds,
            num_ctx=settings.ollama_num_ctx,
        )
    raise ValueError(f"LLM_PROVIDER must be 'none' or 'ollama', got '{settings.llm_provider}'")


def build_container(settings: Settings, resolver: ClauseResolver | None = None) -> Container:
    if settings.docx_engine not in ("python", "libreoffice"):
        raise ValueError(f"DOCX_ENGINE must be 'python' or 'libreoffice', got '{settings.docx_engine}'")
    documents = InMemoryDocumentRepository()
    playbooks = PlaybookRepository.load(settings.playbook_dir, settings.default_playbook_id)
    reviews = InMemoryReviewRepository()
    if settings.seed_sample_document:
        seed_sample_document(documents)
    resolver = resolver or build_resolver(settings)
    service = ReviewService(documents, playbooks, reviews, resolver)
    return Container(
        documents, playbooks, reviews, service, resolver,
        document_service=DocumentService(
            documents,
            store=DiskDocumentStore(Path(settings.document_storage_dir)) if settings.document_storage_dir else None,
            docx_engine=settings.docx_engine,
            ollama_base_url=settings.ollama_base_url,
            ollama_model=settings.ollama_model,
            ollama_timeout_seconds=settings.ollama_timeout_seconds,
        ),
        playbook_service=PlaybookService(playbooks),
    )
