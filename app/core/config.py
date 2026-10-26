import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import MutableMapping

BASE_DIR = Path(__file__).resolve().parents[2]


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def load_env_file(path: Path, environ: MutableMapping[str, str] | None = None) -> bool:
    """Read KEY=VALUE lines from a .env file into the environment.

    Variables that are already set win over the file. Returns True if the file was read.
    Handles comments, blank lines, `export KEY=...`, quotes, and the BOM that Windows
    PowerShell adds to UTF-8 files.
    """
    environ = os.environ if environ is None else environ
    if not path.is_file():
        return False
    for raw in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):].lstrip()
        key, separator, value = line.partition("=")
        if not separator:
            continue
        key, value = key.strip(), value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        elif " #" in value:
            value = value.split(" #", 1)[0].rstrip()
        if key:
            environ.setdefault(key, value)
    return True


@dataclass(frozen=True)
class Settings:
    app_name: str = "NDA Review API"
    playbook_dir: Path = BASE_DIR / "config" / "playbooks"
    default_playbook_id: str = "NDA-BASELINE"
    # Loads the fictional sample NDAs at startup so the review endpoints can be
    # tried before the upload endpoint exists. Turn off in real environments.
    seed_sample_document: bool = True

    # LLM provider for ambiguous clauses and clause extraction: "none" (default) or "openai".
    llm_provider: str = "none"
    openai_api_key: str = field(default="", repr=False)  # never printed
    openai_model: str = "gpt-4o-mini"
    openai_base_url: str = "https://api.openai.com/v1"
    openai_timeout_seconds: float = 60.0
    openai_max_retries: int = 2

    # Uploaded documents: "" keeps them in memory only. A folder name also saves them on disk
    # and reloads them at startup.
    document_storage_dir: str = ""
    # How DOCX is read: "python" (python-docx, no page numbers) or "libreoffice" (needs LibreOffice;
    # converts to PDF first so pages exist; falls back to python-docx if it is not available).
    docx_engine: str = "python"

    # Path of the .env file that was read, or None. Shown in the startup log.
    env_file_loaded: str | None = None

    @classmethod
    def from_env(cls) -> "Settings":
        # Real environment variables win; .env in the project folder fills in the rest.
        env_path = Path(os.getenv("ENV_FILE", str(BASE_DIR / ".env")))
        loaded = load_env_file(env_path)
        return cls(
            playbook_dir=Path(os.getenv("PLAYBOOK_DIR", str(BASE_DIR / "config" / "playbooks"))),
            default_playbook_id=os.getenv("DEFAULT_PLAYBOOK_ID", "NDA-BASELINE"),
            seed_sample_document=_env_bool("SEED_SAMPLE_DOCUMENT", True),
            llm_provider=os.getenv("LLM_PROVIDER", "none").strip().lower(),
            openai_api_key=os.getenv("OPENAI_API_KEY", "").strip(),
            openai_model=os.getenv("OPENAI_MODEL", "gpt-4o-mini").strip(),
            openai_base_url=os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1").strip(),
            openai_timeout_seconds=float(os.getenv("OPENAI_TIMEOUT_SECONDS", "60")),
            openai_max_retries=int(os.getenv("OPENAI_MAX_RETRIES", "2")),
            document_storage_dir=os.getenv("DOCUMENT_STORAGE_DIR", "").strip(),
            docx_engine=os.getenv("DOCX_ENGINE", "python").strip().lower(),
            env_file_loaded=str(env_path) if loaded else None,
        )
