import logging
from datetime import date
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
    # Idle claim waits double from job_poll_seconds up to this cap; unset keeps a fixed poll.
    job_idle_poll_max_seconds: float | None = Field(default=None, gt=0, le=600)
    upload_max_bytes: int = Field(default=DEFAULT_UPLOAD_MAX_BYTES, gt=0)
    upload_target_seconds: int = Field(default=900, gt=0, le=86400)
    max_shared_duration_seconds: int = Field(default=600, gt=0)
    storage_dir: Path = Path(".data/uploads")
    artifacts_dir: Path = Path(".data/artifacts")
    ffprobe_path: str = Field(default="ffprobe", min_length=1)
    ffmpeg_path: str = Field(default="ffmpeg", min_length=1)
    media_probe_timeout_seconds: float = Field(default=10, gt=0, le=60)
    media_extract_timeout_seconds: float = Field(default=120, gt=0, le=600)
    media_cpu_seconds: int = Field(default=60, gt=0, le=300)
    media_output_max_bytes: int = Field(default=65536, gt=0, le=1048576)
    # Provisional decimal-byte cap, not evidence of a provider account's entitlement.
    asr_audio_max_bytes: int = Field(default=25000000, gt=44)
    # Main-flow stages default on (#127). Left on without their provider configuration they
    # report a typed unavailable reason at run time instead of skipping silently.
    asr_enabled: bool = True
    text_grace_seconds: int = Field(default=60, ge=0, le=3600)
    groq_api_key: SecretStr = SecretStr("")
    groq_model: str = Field(default="", max_length=128)
    groq_account_id: str = Field(default="", max_length=128)
    asr_limits_verified_on: date | None = None
    asr_requests_per_minute: int | None = Field(default=None, gt=0)
    asr_requests_per_day: int | None = Field(default=None, gt=0)
    asr_audio_seconds_per_hour: int | None = Field(default=None, gt=0)
    asr_audio_seconds_per_day: int | None = Field(default=None, gt=0)
    asr_minimum_billable_seconds: int | None = Field(default=None, gt=0)
    asr_response_max_bytes: int = Field(default=1048576, gt=0, le=4194304)
    asr_timeout_seconds: float = Field(default=120, gt=0, le=600)
    job_retry_transient_attempts: int = Field(default=5, ge=0, le=20)
    job_retry_rate_limited_attempts: int = Field(default=5, ge=0, le=20)
    job_retry_schema_repair_attempts: int = Field(default=2, ge=0, le=10)
    job_retry_unknown_outcome_attempts: int = Field(default=3, ge=0, le=10)
    job_retry_backoff_seconds: float = Field(default=1, gt=0, le=60)
    job_retry_max_backoff_seconds: float = Field(default=60, gt=0, le=3600)
    # Google Web client ID whose audience linked ID tokens must carry (BC-D07). Empty
    # disables account linking; it is configuration, not a secret.
    google_client_id: str = Field(default="", max_length=256)
    retention_enabled: bool = False
    retention_data_seconds: int = Field(default=86400, ge=60, le=2592000)
    retention_tombstone_seconds: int = Field(default=604800, ge=60, le=7776000)
    retention_poll_seconds: int = Field(default=60, ge=1, le=3600)
    retention_batch_size: int = Field(default=100, ge=1, le=1000)
    # DEVELOPMENT ONLY. Publishes a report built from the contract fixtures after intake so
    # clients have report data before the assessment pipeline (#27) exists. Never enable it
    # for real users; every such report is marked ``fixture: true``.
    stub_reports: bool = False
    # Evidence stages (BE-09, #27). The Scholarxiv key is server-only; without it the
    # retrieval, assessment and reanalysis jobs fail with ``EvidenceUnavailable``.
    scholarxiv_api_key: SecretStr | None = None
    scholarxiv_base_url: str = Field(default="https://www.scholarxiv.com", max_length=200)
    # Papers and Router calls share one account limit (1 200 requests per hour on Free);
    # the default leaves 200 requests of headroom for other users of the account.
    scholarxiv_requests_per_hour: int = Field(default=1000, ge=1, le=1200)
    # Federated search needs the Go plan or above; Free keys get 403 and fall back.
    scholarxiv_federated: bool = False
    evidence_query_route: str = Field(default="auto:cheap", min_length=1, max_length=100)
    evidence_relation_route: str = Field(default="auto:quality", min_length=1, max_length=100)
    evidence_max_claims: int = Field(default=20, ge=1, le=100)
    evidence_max_queries_per_claim: int = Field(default=3, ge=1, le=6)
    evidence_results_per_query: int = Field(default=10, ge=1, le=50)
    evidence_max_candidates_per_claim: int = Field(default=20, ge=1, le=100)
    evidence_max_passages_per_claim: int = Field(default=4, ge=1, le=10)
    evidence_max_full_text_per_claim: int = Field(default=2, ge=0, le=5)
    evidence_max_llm_calls_per_claim: int = Field(default=4, ge=2, le=10)
    evidence_provider_timeout_seconds: float = Field(default=20, gt=0, le=120)
    # Optional contact address for the Crossref polite pool; never required.
    crossref_mailto: str = Field(default="", max_length=200)
    # Proposed BC-D06 demo policy; opt-in until the product owner approves the limits.
    quotas_enabled: bool = False
    quota_daily_checks: int = Field(default=6, ge=1, le=1000)
    quota_active_checks: int = Field(default=2, ge=1, le=100)
    quota_daily_upload_bytes: int = Field(default=DEFAULT_UPLOAD_MAX_BYTES, gt=0)
    quota_claims_per_run: int = Field(default=5, ge=1, le=100)
    quota_provider_concurrency: int = Field(default=2, ge=1, le=10)
    quota_provider_reserve: int = Field(default=50, ge=1, le=1200)
    extraction_enabled: bool = True
    extraction_free_routes_verified: bool = False
    extraction_models: list[str] = Field(default_factory=list, max_length=20)
    extraction_max_tokens: int = Field(default=2048, ge=1, le=8192)
    reconciliation_max_tokens: int = Field(default=8192, ge=1, le=8192)
    extraction_recovery_attempts: int = Field(default=0, ge=0, le=10)
    groq_extraction_enabled: bool = False
    groq_free_route_verified: bool = False
    reconciliation_enabled: bool = True
    reconciliation_free_routes_verified: bool = False
    reconciliation_models: list[str] = Field(default_factory=list, max_length=20)
    # Per-input budget for real speech/text producers; defaults are the measured BE-08 candidate.
    extraction_budget_requests: int = Field(default=24, ge=1, le=10000)
    extraction_budget_tokens: int = Field(default=196500, ge=1, le=2147483647)
    extraction_reserved_requests: int = Field(default=3, ge=1, le=10000)
    extraction_reserved_tokens: int = Field(default=102500, ge=1, le=2147483647)
    extraction_batch_observations: int = Field(default=6, ge=1, le=64)
    extraction_overlap_observations: int = Field(default=1, ge=1, le=32)
    extraction_max_observations: int = Field(default=64, ge=1, le=4096)

    @property
    def asr_configured(self) -> bool:
        """Every verified account/model limit hosted speech needs before any call."""
        return (
            bool(self.groq_api_key.get_secret_value().strip())
            and bool(self.groq_model.strip())
            and bool(self.groq_account_id.strip())
            and self.asr_limits_verified_on is not None
            and self.asr_requests_per_minute is not None
            and self.asr_requests_per_day is not None
            and self.asr_audio_seconds_per_hour is not None
            and self.asr_audio_seconds_per_day is not None
            and self.asr_minimum_billable_seconds is not None
            and "asr_audio_max_bytes" in self.model_fields_set
        )

    @property
    def extraction_configured(self) -> bool:
        """Extraction is on with a verified free model pool and the server credential."""
        return (
            self.extraction_enabled
            and self.extraction_free_routes_verified
            and bool(self.extraction_models)
            and all(value.strip() and len(value) <= 200 for value in self.extraction_models)
            and self.scholarxiv_api_key is not None
            and bool(self.scholarxiv_api_key.get_secret_value().strip())
        )

    @property
    def reconciliation_configured(self) -> bool:
        """Reconciliation is on with its own verified quality pool on configured extraction."""
        return (
            self.reconciliation_enabled
            and self.extraction_configured
            and self.reconciliation_free_routes_verified
            and bool(self.reconciliation_models)
            and all(value.strip() and len(value) <= 200 for value in self.reconciliation_models)
        )

    @model_validator(mode="after")
    def extraction_gate(self) -> Self:
        # An explicit opt-in (argument or OVRLY_ variable) must be complete at startup; the
        # production default may lack its configuration and then fails visibly per job.
        explicit = set(self.model_fields_set)
        if (
            self.extraction_reserved_requests >= self.extraction_budget_requests
            or self.extraction_reserved_tokens >= self.extraction_budget_tokens
        ):
            raise ValueError("Extraction requires capacity outside the reconciliation reserve")
        if self.stub_reports:
            if self.extraction_enabled and "extraction_enabled" in explicit:
                raise ValueError("Real extraction cannot be combined with development stub reports")
            # The development stub replaces the default-on research stages.
            self.extraction_enabled = False
            self.reconciliation_enabled = self.reconciliation_enabled and (
                "reconciliation_enabled" in explicit
            )
        if (
            self.reconciliation_enabled
            and "reconciliation_enabled" in explicit
            and not self.reconciliation_configured
        ):
            raise ValueError("Reconciliation needs its own verified free quality model pool")
        if self.groq_extraction_enabled and (
            not self.extraction_enabled
            or not self.groq_free_route_verified
            or not self.groq_api_key.get_secret_value().strip()
        ):
            raise ValueError("Groq extraction needs independent route verification and credential")
        if (
            self.extraction_enabled
            and "extraction_enabled" in explicit
            and not self.extraction_configured
        ):
            raise ValueError("Extraction needs a verified free model pool and server credential")
        return self

    @model_validator(mode="after")
    def backoff_bounds(self) -> Self:
        if self.job_retry_max_backoff_seconds < self.job_retry_backoff_seconds:
            raise ValueError("job_retry_max_backoff_seconds must be at least the base backoff")
        idle_max = self.job_idle_poll_max_seconds
        if idle_max is not None and idle_max < self.job_poll_seconds:
            raise ValueError("job_idle_poll_max_seconds must be at least job_poll_seconds")
        if self.quotas_enabled and self.quota_provider_reserve >= self.scholarxiv_requests_per_hour:
            raise ValueError("quota_provider_reserve must be below scholarxiv_requests_per_hour")
        return self

    @model_validator(mode="after")
    def hosted_speech_configuration(self) -> Self:
        if self.asr_enabled and "asr_enabled" in self.model_fields_set and not self.asr_configured:
            raise ValueError("Hosted speech requires explicit verified account/model limits")
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
