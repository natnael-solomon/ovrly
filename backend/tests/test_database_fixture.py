from unittest.mock import MagicMock

import pytest
from conftest import database_url
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine

TEST_URL = "postgresql+psycopg://fixture@127.0.0.1:55432/ovrly"


@pytest.fixture
def isolated_connection(monkeypatch):
    for name in ("PGHOSTADDR", "PGSERVICE", "PGSERVICEFILE"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("OVRLY_TEST_DATABASE_URL", TEST_URL)
    connect = MagicMock()
    monkeypatch.setattr("conftest.psycopg.connect", connect)
    monkeypatch.setattr("conftest.migrate", MagicMock())
    return connect


@pytest.mark.parametrize(
    "query",
    [
        "dbname=ovrly",
        "db%6eame=ovrly",
        "database=ovrly",
        "host=remote.invalid",
        "hostaddr=192.0.2.1",
        "service=fixture-service",
        "servicefile=fixture.conf",
        "HOST=remote.invalid",
        "host=localhost&host=remote.invalid",
    ],
)
def test_query_routing_overrides_fail_before_connect(monkeypatch, isolated_connection, query):
    monkeypatch.setenv("OVRLY_TEST_DATABASE_URL", f"{TEST_URL}?{query}")
    with pytest.raises(pytest.fail.Exception, match="routing overrides"):
        next(database_url.__wrapped__())
    isolated_connection.assert_not_called()


@pytest.mark.parametrize("name", ["PGHOSTADDR", "PGSERVICE", "PGSERVICEFILE"])
def test_inherited_routing_overrides_fail_before_connect(monkeypatch, isolated_connection, name):
    monkeypatch.setenv(name, "private-fixture-value")
    with pytest.raises(pytest.fail.Exception, match="Unset PGHOSTADDR") as error:
        next(database_url.__wrapped__())
    assert "private-fixture-value" not in str(error.value)
    isolated_connection.assert_not_called()


@pytest.mark.parametrize("host", ["127.0.0.1", "localhost"])
@pytest.mark.parametrize("query", ["", "?sslmode=require&application_name=fixture"])
@pytest.mark.parametrize("failed_test", [False, True])
def test_valid_urls_use_owned_database_and_cleanup(
    monkeypatch, isolated_connection, host, query, failed_test
):
    configured = TEST_URL.replace("127.0.0.1", host) + query
    monkeypatch.setenv("OVRLY_TEST_DATABASE_URL", configured)
    generator = database_url.__wrapped__()
    supplied = make_url(next(generator))
    admin = isolated_connection.return_value.__enter__.return_value
    try:
        assert supplied.database.startswith("ovrly_test_")
        assert supplied.database != "ovrly"
        engine = create_async_engine(supplied)
        _, parameters = engine.dialect.create_connect_args(engine.url)
        assert parameters["dbname"] == supplied.database
        assert parameters["host"] == host
        assert parameters["port"] == 55432
        assert "hostaddr" not in parameters
        assert dict(supplied.query) == dict(make_url(configured).query)
        isolated_connection.assert_called_once_with(
            make_url(configured).set(drivername="postgresql").render_as_string(hide_password=False),
            autocommit=True,
            connect_timeout=3,
        )
        assert admin.execute.call_args_list[0].args[0].as_string() == (
            f'CREATE DATABASE "{supplied.database}"'
        )
        if failed_test:
            with pytest.raises(RuntimeError, match="fixture failure"):
                generator.throw(RuntimeError("fixture failure"))
    finally:
        generator.close()
    assert admin.execute.call_count == 2
    assert admin.execute.call_args_list[1].args[0].as_string() == (
        f'DROP DATABASE "{supplied.database}" WITH (FORCE)'
    )


def test_create_failure_never_drops_a_database(isolated_connection):
    admin = isolated_connection.return_value.__enter__.return_value
    admin.execute.side_effect = RuntimeError("creation failed")
    with pytest.raises(RuntimeError, match="creation failed"):
        next(database_url.__wrapped__())
    assert admin.execute.call_count == 1
