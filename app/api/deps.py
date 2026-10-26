from fastapi import Request

from app.services.document_service import DocumentService
from app.services.playbook_service import PlaybookService
from app.services.review_service import ReviewService


def get_review_service(request: Request) -> ReviewService:
    return request.app.state.container.review_service


def get_document_service(request: Request) -> DocumentService:
    return request.app.state.container.document_service


def get_playbook_service(request: Request) -> PlaybookService:
    return request.app.state.container.playbook_service
