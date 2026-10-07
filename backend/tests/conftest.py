import os
import subprocess
import sys
import uuid
from collections.abc import Iterator
from contextlib import contextmanager

import psycopg
import pytest
from psycopg import sql
from sqlalchemy.engine import URL, make_url


@pytest.fixture(scope="session")
def database_url() -> Iterator[str]:
    with owned_database(admin_database_url()) as test_url:
        migrate(test_url)
        yield test_url


@pytest.fixture(scope="session")
def database_template() -> Iterator[str]:
    """A migrated database that is only ever copied, never connected to by tests.

    PostgreSQL refuses to copy a template that has other sessions, so this is separate from
    the shared ``database_url`` database.
    """
    with owned_database(admin_database_url()) as template_url:
        migrate(template_url)
        yield str(make_url(template_url).database)


@pytest.fixture
def isolated_database_url(database_template: str) -> Iterator[str]:
    """A fresh migrated database for one test, copied from ``database_template``.

    Workers, global sweeps and quota rows in one test can never see another test's rows.
    """
    with owned_database(admin_database_url(), template=database_template) as test_url:
        yield test_url


def admin_database_url() -> URL:
    admin_url = os.environ.get("OVRLY_TEST_DATABASE_URL")
    if not admin_url:
        pytest.fail(
            "Set OVRLY_TEST_DATABASE_URL to a local PostgreSQL role allowed to create databases"
        )
    url = make_url(admin_url)
    if url.drivername != "postgresql+psycopg" or url.host not in {"127.0.0.1", "localhost"}:
        pytest.fail("Tests require an explicitly configured loopback PostgreSQL database")
    if {"dbname", "database", "host", "hostaddr", "service", "servicefile"} & {
        key.lower() for key in url.query
    }:
        pytest.fail("Test database URLs must not contain routing overrides in query parameters")
    if any(os.environ.get(key) for key in ("PGHOSTADDR", "PGSERVICE", "PGSERVICEFILE")):
        pytest.fail("Unset PGHOSTADDR, PGSERVICE and PGSERVICEFILE before database tests")
    return url


@contextmanager
def owned_database(url: URL, *, template: str | None = None) -> Iterator[str]:
    """Create a uniquely named database, optionally copied from ``template``, then drop it."""
    name = "ovrly_test_" + uuid.uuid4().hex
    create = sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name))
    if template is not None:
        create = sql.SQL("{} TEMPLATE {}").format(create, sql.Identifier(template))
    connection_url = url.set(drivername="postgresql").render_as_string(hide_password=False)
    with psycopg.connect(connection_url, autocommit=True, connect_timeout=3) as admin:
        admin.execute(create)
        try:
            yield url.set(database=name).render_as_string(hide_password=False)
        finally:
            admin.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(name)))


def migrate(url: str) -> None:
    result = subprocess.run(  # noqa: S603 - current interpreter and fixed Alembic arguments
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        env={**os.environ, "OVRLY_DATABASE_URL": url},
        capture_output=True,
        text=True,
        timeout=60,
    )
    if result.returncode != 0:
        pytest.fail("Applying migrations to the test database failed:\n" + result.stderr)
