from fastapi import APIRouter, Depends

from app.api.deps import get_review_service
from app.schemas.common import ApiResponse
from app.schemas.review import (
    FindingUpdateData,
    FindingUpdateRequest,
    ReviewCreatedData,
    ReviewCreateRequest,
    ReviewReport,
)
from app.services.review_service import ReviewService
from app.utils.response import success_response

router = APIRouter(prefix="/api/reviews", tags=["reviews"])


@router.post("", status_code=201, response_model=ApiResponse[ReviewCreatedData], summary="Run a review")
def create_review(body: ReviewCreateRequest, service: ReviewService = Depends(get_review_service)):
    """Runs the review synchronously for a document and a playbook (default playbook if omitted)."""
    report = service.create_review(body.document_id, body.playbook_id)
    meta = report.metadata
    data = ReviewCreatedData(
        review_id=meta.review_id, status=meta.status, playbook_version=meta.playbook_version
    )
    return success_response(data, "Review completed", 201)


@router.get("/{review_id}", response_model=ApiResponse[ReviewReport], summary="Fetch a review report")
def get_review(review_id: str, service: ReviewService = Depends(get_review_service)):
    return success_response(service.get_review(review_id), "Review fetched")


@router.patch(
    "/{review_id}/findings/{finding_id}",
    response_model=ApiResponse[FindingUpdateData],
    summary="Update a finding's human decision and comment",
)
def update_finding(
    review_id: str,
    finding_id: str,
    body: FindingUpdateRequest,
    service: ReviewService = Depends(get_review_service),
):
    # Only fields the client actually sent are changed; "comment": null (or "") clears the comment.
    changes: dict = {}
    if "status" in body.model_fields_set:
        changes["status"] = body.status
    if "comment" in body.model_fields_set:
        changes["comment"] = (body.comment or "").strip() or None
    finding = service.update_finding(review_id, finding_id, changes)
    data = FindingUpdateData(finding_id=finding.finding_id, status=finding.status, comment=finding.comment)
    return success_response(data, "Finding updated")
