"""Application settings, read from the environment and a local .env file.

Every tunable lives here. Nothing else in the package calls os.getenv, so the
full configuration surface of the template is the field list below plus the
annotated .env.example beside it.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

ProviderName = Literal["anthropic", "gemini", "groq", "ollama"]


class Settings(BaseSettings):
    """Runtime configuration for the agent, database and API.

    Attributes are populated from environment variables of the same name in
    upper case, falling back to a .env file in the working directory.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- LLM provider ------------------------------------------------------
    llm_provider: ProviderName = "anthropic"

    anthropic_api_key: str = ""
    anthropic_model: str = "claude-opus-5"
    anthropic_effort: str = "medium"

    gemini_api_key: str = ""
    gemini_model: str = "gemini-2.5-flash"

    groq_api_key: str = ""
    groq_model: str = "llama-3.3-70b-versatile"

    ollama_base_url: str = "http://localhost:11434"
    ollama_model: str = "llama3.1"

    # Maximum number of assistant turns in one tool-use loop. Guards against a
    # model that keeps calling tools without ever producing an answer.
    max_tool_iterations: int = 6
    # Output ceiling per assistant turn. Generous enough that reasoning plus a
    # written answer both fit without truncation.
    max_output_tokens: int = 8192

    # --- Database ----------------------------------------------------------
    database_url: str = "sqlite+aiosqlite:///data/demo.db"
    directory_url: str = "sqlite+aiosqlite:///data/directory.db"

    # --- Query limits ------------------------------------------------------
    max_rows: int = Field(default=1000, gt=0)
    llm_max_rows: int = Field(default=100, gt=0)

    # --- API ---------------------------------------------------------------
    api_host: str = "127.0.0.1"
    api_port: int = 8000
    allowed_origins: str = ""
    api_key: str = ""
    log_level: str = "INFO"

    @field_validator("anthropic_effort")
    @classmethod
    def _validate_effort(cls, value: str) -> str:
        """Reject effort levels the API would refuse, allowing an empty value."""
        allowed = {"", "low", "medium", "high", "xhigh", "max"}
        normalised = value.strip().lower()
        if normalised not in allowed:
            raise ValueError(f"anthropic_effort must be one of {sorted(allowed)}")
        return normalised

    @property
    def cors_origins(self) -> list[str]:
        """Parse ALLOWED_ORIGINS, falling back to a permissive local default."""
        origins = [o.strip() for o in self.allowed_origins.split(",") if o.strip()]
        return origins or ["*"]


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide settings singleton."""
    return Settings()
