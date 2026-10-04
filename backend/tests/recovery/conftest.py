import pytest

from recovery.harness import Harness


@pytest.fixture
async def harness(database_url):
    harness = Harness(database_url)
    try:
        await harness.initialize()
        yield harness
        # Include cases ending cancelled/queued as well as those calling assert_invariants.
        for job_id in harness.job_ids:
            await harness.assert_no_cross_owner_read(job_id)
    finally:
        await harness.close()
