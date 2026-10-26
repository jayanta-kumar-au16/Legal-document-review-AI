from dataclasses import dataclass
from pathlib import Path

from app.core.config import Settings
from app.dev_seed import seed_sample_document
from app.repositories.disk_store import DiskDocumentStore
from app.repositories.document_repo import InMemoryDocumentRepository
from app.repositories.playbook_repo import PlaybookRepository
from app.repositories.review_repo import InMemoryReviewRepository
from app.services.document_service import DocumentService
from app.llm.gateway import LLMGateway, build_gateway
from app.services.llm_client import ClauseResolver
from app.services.llm_resolver import GatewayResolver
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
    gateway: LLMGateway

    def llm_status(self) -> dict:
        """What the app is configured to use, plus whether the provider is reachable and the key works."""
        status = {
            "provider": getattr(self.resolver, "provider", "custom"),
            "configured": bool(self.resolver.configured),
        }
        if self.resolver.configured:
            status["model"] = self.resolver.model
            status.update(self.gateway.check())
        return status


def build_resolver(settings: Settings, gateway: LLMGateway | None = None) -> ClauseResolver:
    return GatewayResolver(gateway or build_gateway(settings))


def build_container(settings: Settings, resolver: ClauseResolver | None = None, gateway: LLMGateway | None = None) -> Container:
    if settings.docx_engine not in ("python", "libreoffice"):
        raise ValueError(f"DOCX_ENGINE must be 'python' or 'libreoffice', got '{settings.docx_engine}'")
    documents = InMemoryDocumentRepository()
    playbooks = PlaybookRepository.load(settings.playbook_dir, settings.default_playbook_id)
    reviews = InMemoryReviewRepository()
    if settings.seed_sample_document:
        seed_sample_document(documents)
    gateway = gateway or build_gateway(settings)
    resolver = resolver or GatewayResolver(gateway)
    service = ReviewService(documents, playbooks, reviews, resolver)
    return Container(
        documents, playbooks, reviews, service, resolver,
        document_service=DocumentService(
            documents,
            store=DiskDocumentStore(Path(settings.document_storage_dir)) if settings.document_storage_dir else None,
            docx_engine=settings.docx_engine,
            gateway=gateway,
        ),
        playbook_service=PlaybookService(playbooks),
        gateway=gateway,
    )
