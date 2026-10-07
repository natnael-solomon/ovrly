"""Failure diagnostics for the recovery suite: pending asyncio task stacks and job rows."""

import asyncio
import io

import psycopg
from psycopg import sql
from sqlalchemy.engine import make_url

# Scheduling and lease state only; payloads and stage data are left out of reports.
JOB_COLUMNS = (
    "id",
    "stage",
    "state",
    "attempts",
    "fencing_token",
    "generation",
    "cancel_requested",
    "lease_owner",
    "lease_expires_at",
    "available_at",
    "retry_class",
    "retry_counts",
    "failure",
    "provider_request_id IS NOT NULL AS request_recorded",
    "created_at",
    "updated_at",
)
MAX_STACK_FRAMES = 30
# Hosted speech attempts per model; no audio, transcripts or account identifiers.
LEDGER_COLUMNS = (
    "job_id",
    "model",
    "outcome",
    "retry_index",
    "retry_after",
    "audio_seconds",
    "created_at",
)


def task_stacks(loop: asyncio.AbstractEventLoop) -> str:
    """Every task still pending on ``loop``, with the await it is suspended in."""
    tasks = sorted(
        (task for task in asyncio.all_tasks(loop) if not task.done()),
        key=lambda task: task.get_name(),
    )
    if not tasks:
        return "No pending asyncio tasks.\n"
    output = io.StringIO()
    output.write(f"{len(tasks)} pending asyncio tasks:\n")
    for task in tasks:
        output.write(f"\n--- {task.get_name()} ---\n")
        task.print_stack(limit=MAX_STACK_FRAMES, file=output)
    return output.getvalue()


def job_rows(database_url: str) -> str:
    """The test database's job rows; with per-test databases these are only this test's."""
    url = make_url(database_url).set(drivername="postgresql")
    try:
        with psycopg.connect(
            url.render_as_string(hide_password=False),
            connect_timeout=3,
            options="-c statement_timeout=5000",
        ) as connection:
            now = connection.execute("SELECT now()").fetchone()
            columns = sql.SQL(", ").join(sql.SQL(column) for column in JOB_COLUMNS)
            rows = connection.execute(
                sql.SQL("SELECT {} FROM jobs ORDER BY created_at, id").format(columns)
            ).fetchall()
            ledger_columns = sql.SQL(", ").join(sql.SQL(column) for column in LEDGER_COLUMNS)
            ledger = connection.execute(
                sql.SQL("SELECT {} FROM asr_requests ORDER BY created_at, id").format(
                    ledger_columns
                )
            ).fetchall()
    except psycopg.Error as error:
        return f"Job rows unavailable: {type(error).__name__}\n"
    output = io.StringIO()
    output.write(f"Database time {now[0] if now else 'unknown'}; {len(rows)} job rows:\n")
    _write_rows(output, JOB_COLUMNS, rows)
    output.write(f"{len(ledger)} speech ledger rows:\n")
    _write_rows(output, LEDGER_COLUMNS, ledger)
    return output.getvalue()


def _write_rows(output: io.StringIO, columns: tuple[str, ...], rows: list[tuple]) -> None:
    names = [column.split(" AS ")[-1] for column in columns]
    for row in rows:
        output.write("  " + ", ".join(f"{n}={v}" for n, v in zip(names, row, strict=True)) + "\n")
