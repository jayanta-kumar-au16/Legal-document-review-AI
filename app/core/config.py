import os
from dataclasses import dataclass
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

    # Optional LLM fallback for ambiguous clauses. "none" (default) or "ollama".
    llm_provider: str = "none"
    ollama_base_url: str = "http://localhost:11434"
    ollama_model: str = "llama3.1"  # free, runs locally; 8B build is ~4.9 GB
    ollama_timeout_seconds: float = 120.0
    ollama_num_ctx: int = 4096

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
            ollama_base_url=os.getenv("OLLAMA_BASE_URL", "http://localhost:11434"),
            ollama_model=os.getenv("OLLAMA_MODEL", "llama3.1"),
            ollama_timeout_seconds=float(os.getenv("OLLAMA_TIMEOUT_SECONDS", "120")),
            ollama_num_ctx=int(os.getenv("OLLAMA_NUM_CTX", "4096")),
            env_file_loaded=str(env_path) if loaded else None,
        )