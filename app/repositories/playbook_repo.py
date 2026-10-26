import json
import threading
from pathlib import Path

from pydantic import ValidationError

from app.core.exceptions import ConflictError, PlaybookConfigError
from app.schemas.playbook import Playbook


class PlaybookRepository:
    """Built-in playbooks come from JSON files on disk (and are read-only).
    Uploaded playbooks live in memory next to them and can never replace a built-in one."""

    def __init__(self, playbooks: dict[str, Playbook], default_id: str):
        if default_id not in playbooks:
            raise PlaybookConfigError(f"Default playbook '{default_id}' was not found")
        self._built_in = playbooks
        self._uploaded: dict[str, Playbook] = {}
        self._default_id = default_id
        self._lock = threading.Lock()

    @classmethod
    def load(cls, directory: Path, default_id: str) -> "PlaybookRepository":
        """Load and validate every *.json playbook. Any problem stops startup with a clear message."""
        files = sorted(directory.glob("*.json"))
        if not files:
            raise PlaybookConfigError(f"No playbook JSON files found in {directory}")
        playbooks: dict[str, Playbook] = {}
        for path in files:
            try:
                playbook = Playbook.model_validate(json.loads(path.read_text(encoding="utf-8-sig")))
            except (json.JSONDecodeError, ValidationError) as exc:
                raise PlaybookConfigError(f"Invalid playbook file {path.name}: {exc}") from exc
            if playbook.playbook_id in playbooks:
                raise PlaybookConfigError(f"Duplicate playbook_id '{playbook.playbook_id}' in {path.name}")
            playbooks[playbook.playbook_id] = playbook
        return cls(playbooks, default_id)

    @property
    def default_id(self) -> str:
        return self._default_id

    def get(self, playbook_id: str | None) -> Playbook | None:
        """The playbook with this id, or the default one when no id is given."""
        key = playbook_id or self._default_id
        with self._lock:
            return self._built_in.get(key) or self._uploaded.get(key)

    def is_built_in(self, playbook_id: str) -> bool:
        return playbook_id in self._built_in

    def list_all(self) -> list[tuple[Playbook, bool]]:
        """(playbook, built_in) pairs: built-in first, then uploaded."""
        with self._lock:
            return [(p, True) for p in self._built_in.values()] + [(p, False) for p in self._uploaded.values()]

    def add_uploaded(self, playbook: Playbook) -> Playbook:
        """Register an uploaded playbook.

        - The id of a built-in playbook is reserved.
        - Re-uploading identical content is fine.
        - Changed content must come with a new `version`, so a given id + version always means one rule set.
        """
        with self._lock:
            if playbook.playbook_id in self._built_in:
                raise ConflictError(
                    "PLAYBOOK_ID_RESERVED",
                    f"'{playbook.playbook_id}' is the id of a built-in playbook. "
                    "Change playbook_id in your file (for example 'MY-NDA-PLAYBOOK') and upload it again.",
                )
            existing = self._uploaded.get(playbook.playbook_id)
            if existing and existing.version == playbook.version and existing != playbook:
                raise ConflictError(
                    "PLAYBOOK_VERSION_CONFLICT",
                    f"A different playbook '{playbook.playbook_id}' version '{playbook.version}' was already uploaded. "
                    "Change 'version' in your file and upload it again.",
                )
            self._uploaded[playbook.playbook_id] = playbook
            return playbook
