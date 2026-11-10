"""Runtime configuration. Every value can be overridden through environment variables."""

from functools import lru_cache

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

DEV_SECRET = "dev-insecure-change-me-not-for-production-use"  # noqa: S105 - rejected at startup in production


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="TRACEMIND_", extra="ignore")

    env: str = "development"
    database_url: str = "sqlite:///./tracemind.db"
    secret_key: str = Field(default=DEV_SECRET, min_length=32)
    access_token_ttl_minutes: int = 60 * 12
    cookie_secure: bool = False
    cors_origins: list[str] = ["http://localhost:3000"]

    # Public demo: enables /auth/demo and seeds the synthetic demo project on startup.
    demo_mode: bool = True
    seed_on_startup: bool = True

    # Uploads
    max_upload_bytes: int = 25 * 1024 * 1024
    max_rows_per_upload: int = 250_000
    storage_backend: str = "local"  # local | s3
    storage_local_dir: str = "./data/uploads"
    s3_bucket: str | None = None
    s3_endpoint_url: str | None = None
    s3_region: str | None = None

    # Analysis
    analysis_rate_limit_per_minute: int = 6
    investigate_rate_limit_per_minute: int = 20
    analysis_workers: int = 2
    request_timeout_seconds: float = 30.0

    # LLM provider: deterministic (no external calls) | anthropic
    llm_provider: str = "deterministic"
    llm_model: str = "claude-opus-5-5"
    llm_effort: str = "medium"
    llm_api_key: str | None = None
    llm_base_url: str | None = None
    llm_timeout_seconds: float = 90.0
    llm_max_output_tokens: int = 16000

    log_level: str = "INFO"

    @field_validator("database_url")
    @classmethod
    def _psycopg_driver(cls, url: str) -> str:
        # Hosting providers hand out postgres:// or postgresql:// URLs; SQLAlchemy needs the driver named.
        for prefix in ("postgres://", "postgresql://"):
            if url.startswith(prefix):
                return "postgresql+psycopg://" + url[len(prefix):]
        return url

    @property
    def is_sqlite(self) -> bool:
        return self.database_url.startswith("sqlite")


@lru_cache
def get_settings() -> Settings:
    return Settings()
