from pathlib import Path

import pytest
from pydantic import ValidationError

from services.settings import Settings, load_settings

URL = "postgresql+psycopg://ovrly:test-only@127.0.0.1:55432/ovrly"


def test_defaults_and_secret_repr():
    settings = Settings(database_url=URL, _env_file=None)
    assert not settings.embed_worker
    assert settings.api_port == 8000
    assert "test-only" not in repr(settings)


@pytest.mark.parametrize("value", ["", "sqlite:///local.db", "postgresql://localhost/db", "broken"])
def test_invalid_database_url(value):
    with pytest.raises(ValidationError):
        Settings(database_url=value, _env_file=None)


@pytest.mark.parametrize(
    "field,value",
    [
        ("database_timeout_seconds", 0),
        ("worker_shutdown_seconds", 31),
        ("api_port", 65536),
        ("embed_worker", "maybe"),
        ("upload_max_bytes", 0),
        ("upload_target_seconds", 86401),
        ("max_shared_duration_seconds", -1),
        ("job_lease_seconds", 601),
        ("job_poll_seconds", 0),
        ("job_retry_transient_attempts", -1),
        ("job_retry_rate_limited_attempts", 21),
        ("job_retry_schema_repair_attempts", 11),
        ("job_retry_unknown_outcome_attempts", 11),
        ("job_retry_backoff_seconds", 61),
        ("job_retry_max_backoff_seconds", 3601),
        ("retention_data_seconds", 59),
        ("retention_data_seconds", 2592001),
        ("retention_tombstone_seconds", 59),
        ("retention_tombstone_seconds", 7776001),
        ("retention_poll_seconds", 0),
        ("retention_poll_seconds", 3601),
        ("retention_batch_size", 0),
        ("retention_batch_size", 1001),
        ("retention_enabled", "maybe"),
        ("job_idle_poll_max_seconds", 0),
        ("job_idle_poll_max_seconds", 601),
    ],
)
def test_invalid_options(field, value):
    with pytest.raises(ValidationError):
        Settings(database_url=URL, _env_file=None, **{field: value})


def test_idle_poll_cap_defaults_off_and_cannot_undercut_the_poll():
    assert Settings(database_url=URL, _env_file=None).job_idle_poll_max_seconds is None
    settings = Settings(database_url=URL, _env_file=None, job_idle_poll_max_seconds=30)
    assert settings.job_idle_poll_max_seconds == 30
    with pytest.raises(ValidationError, match="at least job_poll_seconds"):
        Settings(database_url=URL, _env_file=None, job_poll_seconds=5, job_idle_poll_max_seconds=2)


def test_intake_limit_defaults():
    settings = Settings(database_url=URL, _env_file=None)
    assert settings.upload_max_bytes == 256 * 1024 * 1024
    assert settings.upload_target_seconds == 900
    assert settings.max_shared_duration_seconds == 600
    assert str(settings.storage_dir) == str(Path(".data/uploads"))
    assert settings.google_client_id == ""
    with pytest.raises(ValidationError):
        Settings(database_url=URL, _env_file=None, google_client_id="x" * 257)


def test_retry_defaults_and_backoff_ordering():
    settings = Settings(database_url=URL, _env_file=None)
    assert settings.job_retry_transient_attempts == 5
    assert settings.job_retry_rate_limited_attempts == 5
    assert settings.job_retry_schema_repair_attempts == 2
    assert settings.job_retry_unknown_outcome_attempts == 3
    assert settings.job_retry_backoff_seconds == 1
    assert settings.job_retry_max_backoff_seconds == 60
    with pytest.raises(ValidationError, match="at least the base backoff"):
        Settings(
            database_url=URL,
            _env_file=None,
            job_retry_backoff_seconds=10,
            job_retry_max_backoff_seconds=5,
        )


def test_missing_settings_error_is_safe(monkeypatch, tmp_path, caplog):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("OVRLY_DATABASE_URL", "secret-that-is-not-a-url")
    with pytest.raises(RuntimeError, match="Invalid backend configuration") as error:
        load_settings()
    assert "secret-that-is-not-a-url" not in str(error.value)
    assert "secret-that-is-not-a-url" not in caplog.text
    monkeypatch.delenv("OVRLY_DATABASE_URL")
    with pytest.raises(RuntimeError):
        load_settings()


def test_environment_enables_worker(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("OVRLY_DATABASE_URL", URL)
    monkeypatch.setenv("OVRLY_EMBED_WORKER", "1")
    assert load_settings().embed_worker


def test_retention_defaults_are_opt_in_and_bounds_are_inclusive():
    defaults = Settings(database_url=URL, _env_file=None)
    assert defaults.retention_enabled is False
    assert defaults.retention_data_seconds == 86400
    assert defaults.retention_tombstone_seconds == 604800
    for data, tombstone, poll, batch in ((60, 60, 1, 1), (2592000, 7776000, 3600, 1000)):
        settings = Settings(
            database_url=URL,
            _env_file=None,
            retention_data_seconds=data,
            retention_tombstone_seconds=tombstone,
            retention_poll_seconds=poll,
            retention_batch_size=batch,
        )
        assert settings.retention_data_seconds == data
        assert settings.retention_tombstone_seconds == tombstone
        assert settings.retention_poll_seconds == poll
        assert settings.retention_batch_size == batch
