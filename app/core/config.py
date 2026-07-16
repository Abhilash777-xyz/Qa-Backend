"""
app/core/config.py
──────────────────
Central application configuration loaded from environment variables (or a
.env file).  Uses pydantic-settings so every value is type-checked and
validated at startup.  Import the singleton ``settings`` wherever config is
needed — never read os.environ directly in application code.
"""

from pathlib import Path
from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application-wide settings resolved from environment / .env file."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ── Application ────────────────────────────────────────────────────────
    app_env: str = "development"
    log_level: str = "INFO"

    # ── Database ───────────────────────────────────────────────────────────
    database_url: str = "sqlite:///./qa_backend.db"

    # ── File paths ─────────────────────────────────────────────────────────
    data_dir: Path = Path("data")
    llm_output_dir: Path = Path("llm_outputs")

    # ── OpenRouter ─────────────────────────────────────────────────────────
    openrouter_api_key: str = ""
    openrouter_model: str = "google/gemini-flash-1.5"
    openrouter_base_url: str = "https://openrouter.ai/api/v1"

    # ── LLM behaviour ──────────────────────────────────────────────────────
    llm_max_retries: int = 1
    llm_timeout_seconds: int = 60
    prompt_version: str = "v1"

    @field_validator("data_dir", "llm_output_dir", mode="before")
    @classmethod
    def _to_path(cls, v: object) -> Path:
        return Path(str(v))

    @property
    def is_development(self) -> bool:
        return self.app_env.lower() == "development"

    @property
    def llm_configured(self) -> bool:
        """Return True only when a real API key is present."""
        return bool(self.openrouter_api_key) and not self.openrouter_api_key.startswith(
            "sk-or-v1-xxx"
        )


# Module-level singleton — import this everywhere.
settings = Settings()

# Ensure runtime directories exist.
settings.data_dir.mkdir(parents=True, exist_ok=True)
settings.llm_output_dir.mkdir(parents=True, exist_ok=True)
