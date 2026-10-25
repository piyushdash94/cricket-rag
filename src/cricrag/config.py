"""Settings. Values come from the shell, then ./.env, then cricbot's backend/.env (LLM keys only)."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


def _read_env_file(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    if not path.is_file():
        return out
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        out[key.strip()] = value.strip().strip('"').strip("'")
    return out


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    cricbot_root: Path | None = None
    cricbot_db: Path | None = None
    cricbot_api: str = "http://127.0.0.1:8000"
    fielding_events: Path | None = None

    data_dir: Path = Path("data")
    embed_model: str = "BAAI/bge-small-en-v1.5"
    rerank_model: str = "Xenova/ms-marco-MiniLM-L-6-v2"

    groq_api_key: str | None = Field(default=None, repr=False)
    groq_model: str = "openai/gpt-oss-120b,openai/gpt-oss-20b"
    llm_cache: bool = True
    groq_intent_model: str = (
        "openai/gpt-oss-20b,qwen/qwen3.8-27b,openai/gpt-oss-120b"  # small form-filling call
    )
    router: str = "intent"  # intent | rules
    groq_max_wait: float = 20.0  # seconds to wait on a 429 before trying the next model (raise for eval runs)

    @property
    def db_path(self) -> Path:
        if self.cricbot_db:
            return self.cricbot_db
        if self.cricbot_root:
            return self.cricbot_root / "data" / "cricbot.duckdb"
        raise RuntimeError("Set CRICBOT_ROOT or CRICBOT_DB (see .env.example)")

    @property
    def lance_dir(self) -> Path:
        return self.data_dir / "lance"

    @property
    def model_dir(self) -> Path:
        """Plain model folders (data/models/<org>__<name>). Used instead of fastembed's temp-dir cache."""
        return self.data_dir / "models"

    @property
    def events_db(self) -> Path:
        return self.data_dir / "events.duckdb"

    @property
    def cache_dir(self) -> Path:
        return self.data_dir / "cache"

    @property
    def groq_intent_models(self) -> list[str]:
        return [m.strip() for m in self.groq_intent_model.split(",") if m.strip()]

    @property
    def groq_models(self) -> list[str]:
        return [m.strip() for m in self.groq_model.split(",") if m.strip()]


@lru_cache(maxsize=1)
def settings() -> Settings:
    s = Settings()
    if s.cricbot_root and (not s.groq_api_key):
        borrowed = _read_env_file(s.cricbot_root / "backend" / ".env")
        if borrowed.get("GROQ_API_KEY"):
            s.groq_api_key = borrowed["GROQ_API_KEY"]
            if borrowed.get("GROQ_MODEL") and s.groq_model == Settings.model_fields["groq_model"].default:
                s.groq_model = borrowed["GROQ_MODEL"]
    return s
