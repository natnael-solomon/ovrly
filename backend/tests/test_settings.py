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
    ],
)
def test_invalid_options(field, value):
    with pytest.raises(ValidationError):
        Settings(database_url=URL, _env_file=None, **{field: value})


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
