import logging
from pathlib import Path
from typing import Self

from pydantic import Field, SecretStr, ValidationError, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError

# Placeholder until BC-D06 fixes the shared upload budget; 256 MiB keeps a ten-minute
# phone recording inside the limit while bounding local disk use.
DEFAULT_UPLOAD_MAX_BYTES = 256 * 1024 * 1024


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="OVRLY_", env_file=".env", extra="ignore")

    database_url: SecretStr
    embed_worker: bool = False
    api_port: int = Field(default=8000, gt=0, le=65535)
    database_timeout_seconds: float = Field(default=3, gt=0, le=30)
    worker_shutdown_seconds: float = Field(default=5, gt=0, le=30)
    job_lease_seconds: float = Field(default=30, gt=0, le=600)
    job_poll_seconds: float = Field(default=1, gt=0, le=60)
    upload_max_bytes: int = Field(default=DEFAULT_UPLOAD_MAX_BYTES, gt=0)
    upload_target_seconds: int = Field(default=900, gt=0, le=86400)
    max_shared_duration_seconds: int = Field(default=600, gt=0)
    storage_dir: Path = Path(".data/uploads")
    job_retry_transient_attempts: int = Field(default=5, ge=0, le=20)
    job_retry_rate_limited_attempts: int = Field(default=5, ge=0, le=20)
    job_retry_schema_repair_attempts: int = Field(default=2, ge=0, le=10)
    job_retry_unknown_outcome_attempts: int = Field(default=3, ge=0, le=10)
    job_retry_backoff_seconds: float = Field(default=1, gt=0, le=60)
    job_retry_max_backoff_seconds: float = Field(default=60, gt=0, le=3600)

    @model_validator(mode="after")
    def backoff_bounds(self) -> Self:
        if self.job_retry_max_backoff_seconds < self.job_retry_backoff_seconds:
            raise ValueError("job_retry_max_backoff_seconds must be at least the base backoff")
        return self

    @field_validator("database_url")
    @classmethod
    def postgres_url(cls, value: SecretStr) -> SecretStr:
        try:
            url = make_url(value.get_secret_value())
        except ArgumentError:
            raise ValueError("A PostgreSQL Psycopg URL is required") from None
        if url.drivername != "postgresql+psycopg" or not url.host or not url.database:
            raise ValueError("A PostgreSQL Psycopg URL with host and database is required")
        return value


def load_settings() -> Settings:
    try:
        return Settings()
    except ValidationError:
        logging.getLogger(__name__).error("Invalid backend environment configuration")
        raise RuntimeError(
            "Invalid backend configuration; check the documented OVRLY_ settings"
        ) from None
