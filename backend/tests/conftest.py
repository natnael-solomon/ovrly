import os
import subprocess
import sys
import uuid
from collections.abc import Iterator

import psycopg
import pytest
from psycopg import sql
from sqlalchemy.engine import make_url

# The stage suites predate the default-on main flow (#127) and opt in stage by stage, so they
# start from the earlier opt-outs. test_main_flow.py clears these to run production defaults.
STAGE_OPT_OUTS = ("OVRLY_ASR_ENABLED", "OVRLY_EXTRACTION_ENABLED", "OVRLY_RECONCILIATION_ENABLED")
for _name in STAGE_OPT_OUTS:
    os.environ.setdefault(_name, "0")


@pytest.fixture(scope="session")
def database_url() -> Iterator[str]:
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
    name = "ovrly_test_" + uuid.uuid4().hex
    connection_url = url.set(drivername="postgresql").render_as_string(hide_password=False)
    with psycopg.connect(connection_url, autocommit=True, connect_timeout=3) as admin:
        admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
        try:
            test_url = url.set(database=name).render_as_string(hide_password=False)
            migrate(test_url)
            yield test_url
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
