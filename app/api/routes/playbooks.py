from fastapi import APIRouter, Depends, File, UploadFile
from starlette.concurrency import run_in_threadpool

from app.api.deps import get_playbook_service
from app.schemas.common import ApiResponse
from app.schemas.document import PlaybookListData, PlaybookSummary
from app.schemas.playbook import Playbook
from app.services.playbook_service import MAX_PLAYBOOK_BYTES, PlaybookService
from app.utils.response import success_response

router = APIRouter(prefix="/api/playbooks", tags=["playbooks"])


@router.get("", response_model=ApiResponse[PlaybookListData], summary="List playbooks")
def list_playbooks(service: PlaybookService = Depends(get_playbook_service)):
    """Built-in playbooks plus any uploaded in this session. `default_playbook_id` is used when a review names none."""
    return success_response(service.list(), "Playbooks fetched")


@router.post("", status_code=201, response_model=ApiResponse[PlaybookSummary], summary="Upload a playbook (JSON)")
async def upload_playbook(file: UploadFile = File(...), service: PlaybookService = Depends(get_playbook_service)):
    """Multipart upload, field name `file`, a .json file in the same format as the built-in playbook.

    Then pass the returned `playbook_id` to `POST /api/reviews`."""
    data = await file.read(MAX_PLAYBOOK_BYTES + 1)
    result = await run_in_threadpool(service.upload, file.filename or "", data)
    return success_response(result, "Playbook uploaded", 201)


@router.get("/{playbook_id}", response_model=ApiResponse[Playbook], summary="Fetch one playbook (also a template to edit)")
def get_playbook(playbook_id: str, service: PlaybookService = Depends(get_playbook_service)):
    return success_response(service.get(playbook_id), "Playbook fetched")
