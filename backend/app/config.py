"""Application settings, loaded from the environment / .env.

Credentials never live in code. ``.env`` is git-ignored; ``.env.example``
documents the shape.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(REPO_ROOT / ".env", REPO_ROOT / "backend" / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    database_url: str = Field(
        default="postgresql+psycopg://analytics:analytics@localhost:5434/ai_analytics",
        description="SQLAlchemy URL. Use postgresql+psycopg://… in production.",
    )
    claude_data_path: str = Field(default="auto")
    ccusage_bin: str = Field(default="ccusage")

    #: Claude Code deletes its own transcripts over time. Without a copy, the
    #: provenance chain breaks and the raw data the brief requires keeping is
    #: simply gone, so archiving is on by default.
    archive_transcripts: bool = Field(default=True)
    transcript_archive_path: str = Field(
        default=str(REPO_ROOT / "archive" / "transcripts")
    )

    #: When false the objective collection still runs end to end.
    ai_analysis_enabled: bool = Field(default=False)
    llm_provider: str = Field(default="anthropic")
    llm_api_key: str = Field(default="")
    llm_model: str = Field(default="claude-opus-5")
    #: Thinking depth / token spend for the analysis call.
    llm_effort: str = Field(default="medium")
    llm_max_tokens: int = Field(default=4096)
    #: Redaction runs before anything leaves the machine. On by default: the
    #: digest is derived from private local transcripts.
    llm_redact_enabled: bool = Field(default=True)

    #: A session whose transcript changed within this window is treated as
    #: possibly still running, and is re-read on every sync.
    active_session_threshold_minutes: int = Field(default=30)

    sql_echo: bool = Field(default=False)


@lru_cache
def get_settings() -> Settings:
    return Settings()
