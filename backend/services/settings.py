import logging

from pydantic import Field, SecretStr, ValidationError, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="OVRLY_", env_file=".env", extra="ignore")

    database_url: SecretStr
    embed_worker: bool = False
    api_port: int = Field(default=8000, gt=0, le=65535)
    database_timeout_seconds: float = Field(default=3, gt=0, le=30)
    worker_shutdown_seconds: float = Field(default=5, gt=0, le=30)

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
