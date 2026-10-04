import pytest

from recovery.harness import Harness


@pytest.fixture
async def harness(database_url):
    harness = Harness(database_url)
    try:
        yield harness
    finally:
        await harness.close()
