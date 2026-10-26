import json
from pathlib import Path

from pydantic import ValidationError

from app.core.exceptions import PlaybookConfigError
from app.schemas.playbook import Playbook


class PlaybookRepository:
    def __init__(self, playbooks: dict[str, Playbook], default_id: str):
        if default_id not in playbooks:
            raise PlaybookConfigError(f"Default playbook '{default_id}' was not found")
        self._playbooks = playbooks
        self._default_id = default_id

    @classmethod
    def load(cls, directory: Path, default_id: str) -> "PlaybookRepository":
        """Load and validate every *.json playbook. Any problem stops startup with a clear message."""
        files = sorted(directory.glob("*.json"))
        if not files:
            raise PlaybookConfigError(f"No playbook JSON files found in {directory}")
        playbooks: dict[str, Playbook] = {}
        for path in files:
            try:
                playbook = Playbook.model_validate(json.loads(path.read_text(encoding="utf-8")))
            except (json.JSONDecodeError, ValidationError) as exc:
                raise PlaybookConfigError(f"Invalid playbook file {path.name}: {exc}") from exc
            if playbook.playbook_id in playbooks:
                raise PlaybookConfigError(f"Duplicate playbook_id '{playbook.playbook_id}' in {path.name}")
            playbooks[playbook.playbook_id] = playbook
        return cls(playbooks, default_id)

    def get(self, playbook_id: str | None) -> Playbook | None:
        return self._playbooks.get(playbook_id or self._default_id)
