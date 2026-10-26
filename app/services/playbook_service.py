import json

from pydantic import ValidationError

from app.core.exceptions import NotFoundError, PayloadTooLargeError, UnprocessableError, UnsupportedMediaError
from app.repositories.playbook_repo import PlaybookRepository
from app.schemas.document import PlaybookListData, PlaybookSummary
from app.schemas.playbook import Playbook
from app.services.extractor import clean_file_name

MAX_PLAYBOOK_BYTES = 256 * 1024
_MAX_ERRORS_SHOWN = 15


def _summary(playbook: Playbook, built_in: bool, default_id: str) -> PlaybookSummary:
    return PlaybookSummary(
        playbook_id=playbook.playbook_id,
        version=playbook.version,
        name=playbook.name,
        rule_count=len(playbook.rules),
        built_in=built_in,
        is_default=playbook.playbook_id == default_id,
    )


class PlaybookService:
    def __init__(self, playbooks: PlaybookRepository):
        self._playbooks = playbooks

    def upload(self, file_name: str, data: bytes) -> PlaybookSummary:
        """Parse and validate an uploaded playbook JSON file, then register it for reviews."""
        name = clean_file_name(file_name)
        if not name.lower().endswith(".json"):
            raise UnsupportedMediaError("UNSUPPORTED_FILE_TYPE", "A playbook must be a .json file.", {"file_name": name})
        if not data:
            raise UnprocessableError("FILE_EMPTY", "The playbook file is empty.")
        if len(data) > MAX_PLAYBOOK_BYTES:
            raise PayloadTooLargeError("FILE_TOO_LARGE", "The playbook file is larger than the 256 KB limit.")

        try:
            raw = json.loads(data.decode("utf-8-sig"))  # utf-8-sig: Windows editors often add a BOM
        except UnicodeDecodeError:
            raise UnprocessableError("INVALID_PLAYBOOK", "The playbook file must be UTF-8 text.") from None
        except json.JSONDecodeError as exc:
            raise UnprocessableError(
                "INVALID_PLAYBOOK", f"The playbook is not valid JSON (line {exc.lineno}, column {exc.colno}): {exc.msg}."
            ) from None
        if not isinstance(raw, dict):
            raise UnprocessableError("INVALID_PLAYBOOK", "The playbook must be a JSON object.")

        try:
            playbook = Playbook.model_validate(raw)
        except ValidationError as exc:
            errors = [
                {"field": ".".join(str(p) for p in err["loc"]) or "playbook", "message": err["msg"].removeprefix("Value error, ")}
                for err in exc.errors()
            ]
            raise UnprocessableError(
                "INVALID_PLAYBOOK",
                f"The playbook is not valid: {errors[0]['field']}: {errors[0]['message']}"
                + (f" (and {len(errors) - 1} more problem(s))" if len(errors) > 1 else ""),
                errors[:_MAX_ERRORS_SHOWN],
            ) from None

        self._playbooks.add_uploaded(playbook)
        return _summary(playbook, False, self._playbooks.default_id)

    def list(self) -> PlaybookListData:
        default_id = self._playbooks.default_id
        return PlaybookListData(
            default_playbook_id=default_id,
            playbooks=[_summary(p, built_in, default_id) for p, built_in in self._playbooks.list_all()],
        )

    def get(self, playbook_id: str) -> Playbook:
        playbook = self._playbooks.get(playbook_id)
        if playbook is None:
            raise NotFoundError("PLAYBOOK_NOT_FOUND", f"Playbook '{playbook_id}' was not found")
        return playbook
