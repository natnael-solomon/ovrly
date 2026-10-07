import asyncio

import pytest

from recovery.diagnostics import job_rows, task_stacks
from recovery.harness import Harness

LOOP = pytest.StashKey[asyncio.AbstractEventLoop]()
DATABASE_URL = pytest.StashKey[str]()


@pytest.fixture
def database_url(isolated_database_url):
    """Each recovery case gets its own database, so workers registered with the real stage
    names, global sweeps (reconciliation scheduling, due analysis, retention) and shared
    quota rows only ever see that case's rows."""
    return isolated_database_url


@pytest.fixture(autouse=True)
async def recovery_diagnostics(request):
    """Remember the case's event loop and database for the failure report below."""
    request.node.stash[LOOP] = asyncio.get_running_loop()
    if "database_url" in request.fixturenames:
        request.node.stash[DATABASE_URL] = request.getfixturevalue("database_url")


@pytest.hookimpl(wrapper=True)
def pytest_runtest_makereport(item, call):
    """On a failed case, including any wait timeout, report the asyncio tasks still pending
    (where each worker, stage and wait is suspended) and the case's job rows. This runs
    before fixture teardown, so worker tasks have not been stopped or cancelled yet."""
    report = yield
    if report.when == "call" and report.failed:
        sections = []
        if (loop := item.stash.get(LOOP, None)) is not None:
            sections.append(task_stacks(loop))
        if (url := item.stash.get(DATABASE_URL, None)) is not None:
            sections.append(job_rows(url))
        if sections:
            report.sections.append(("Captured recovery diagnostics", "\n".join(sections)))
    return report


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
