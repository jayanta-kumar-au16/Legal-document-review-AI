from fastapi import Request

from app.services.review_service import ReviewService


def get_review_service(request: Request) -> ReviewService:
    return request.app.state.container.review_service
