import os
from dataclasses import dataclass
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[2]


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    app_name: str = "NDA Review API"
    playbook_dir: Path = BASE_DIR / "config" / "playbooks"
    default_playbook_id: str = "NDA-BASELINE"
    # Loads the fictional sample NDA at startup so the review endpoints can be
    # tried before the upload endpoint exists. Turn off in real environments.
    seed_sample_document: bool = True

    # Optional LLM fallback for ambiguous clauses. "none" (default) or "ollama".
    llm_provider: str = "none"
    ollama_base_url: str = "http://localhost:11434"
    ollama_model: str = "llama3.1"  # free, runs locally; 8B build is ~4.9 GB
    ollama_timeout_seconds: float = 120.0
    ollama_num_ctx: int = 4096

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            playbook_dir=Path(os.getenv("PLAYBOOK_DIR", str(BASE_DIR / "config" / "playbooks"))),
            default_playbook_id=os.getenv("DEFAULT_PLAYBOOK_ID", "NDA-BASELINE"),
            seed_sample_document=_env_bool("SEED_SAMPLE_DOCUMENT", True),
            llm_provider=os.getenv("LLM_PROVIDER", "none").strip().lower(),
            ollama_base_url=os.getenv("OLLAMA_BASE_URL", "http://localhost:11434"),
            ollama_model=os.getenv("OLLAMA_MODEL", "llama3.1"),
            ollama_timeout_seconds=float(os.getenv("OLLAMA_TIMEOUT_SECONDS", "120")),
            ollama_num_ctx=int(os.getenv("OLLAMA_NUM_CTX", "4096")),
        )
