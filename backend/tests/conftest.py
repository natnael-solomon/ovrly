import os
import uuid
from collections.abc import Iterator

import psycopg
import pytest
from psycopg import sql
from sqlalchemy.engine import make_url


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
    name = "ovrly_test_" + uuid.uuid4().hex
    connection_url = url.set(drivername="postgresql").render_as_string(hide_password=False)
    with psycopg.connect(connection_url, autocommit=True, connect_timeout=3) as admin:
        admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
        try:
            yield url.set(database=name).render_as_string(hide_password=False)
        finally:
            admin.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(name)))
