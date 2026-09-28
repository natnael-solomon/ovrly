import os
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/backend.sh"


@pytest.fixture
def helper_env(tmp_path):
    tools = tmp_path / "bin"
    tools.mkdir()
    stubs = {
        "df": """#!/bin/sh
if [ "${TEST_LOW_AFTER_SYNC:-0}" = 1 ] && grep -q 'uv sync' "$TEST_TOOL_LOG"; then
    TEST_FREE_KB=1000000
fi
printf 'Filesystem 1024-blocks Used Available Capacity Mounted\\n'
printf 'test 10000000 1000000 %s 10%% /\\n' "${TEST_FREE_KB:-5000000}"
""",
        "docker": """#!/bin/sh
printf 'docker %s\\n' "$*" >> "$TEST_TOOL_LOG"
if [ "${TEST_DOCKER_DOWN:-0}" = 1 ] && [ "$1" = info ]; then exit 1; fi
""",
        "uv": """#!/bin/sh
printf 'uv %s embedded=%s\\n' "$*" "${OVRLY_EMBED_WORKER:-unset}" >> "$TEST_TOOL_LOG"
case "$*" in
    *"print(load_settings().api_port)"*) echo 8000 ;;
    *"import socket"*) if [ "${TEST_PORT_BUSY:-0}" = 1 ]; then
        echo 'ERROR: API port unavailable' >&2; exit 1
    fi ;;
    *"alembic upgrade head"*) if [ "${TEST_MIGRATION_FAIL:-0}" = 1 ]; then
        echo 'ERROR: migration failed' >&2; exit 1
    fi ;;
esac
""",
    }
    for name, content in stubs.items():
        path = tools / name
        path.write_text(content)
        path.chmod(0o700)
    return {
        **os.environ,
        "PATH": str(tools) + os.pathsep + os.environ["PATH"],
        "TEST_TOOL_LOG": str(tmp_path / "calls"),
        "OVRLY_EMBED_WORKER": "0",
    }


def run_helper(env, tmp_path):
    return subprocess.run(
        ["sh", str(SCRIPT)],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
    )


def test_low_disk_refuses_downloads(helper_env, tmp_path):
    helper_env["TEST_FREE_KB"] = "2097151"
    result = run_helper(helper_env, tmp_path)
    assert result.returncode != 0
    assert "Less than 2 GiB free" in result.stderr
    calls = Path(helper_env["TEST_TOOL_LOG"]).read_text()
    assert "uv sync" not in calls
    assert "pull db" not in calls


def test_unavailable_docker_fails_before_install(helper_env, tmp_path):
    helper_env["TEST_DOCKER_DOWN"] = "1"
    result = run_helper(helper_env, tmp_path)
    assert result.returncode != 0
    assert "Docker is not reachable" in result.stderr
    assert "uv sync" not in Path(helper_env["TEST_TOOL_LOG"]).read_text()


def test_busy_api_port_does_not_start_database(helper_env, tmp_path):
    helper_env["TEST_PORT_BUSY"] = "1"
    result = run_helper(helper_env, tmp_path)
    assert result.returncode != 0
    assert "API port unavailable" in result.stderr
    assert "up -d" not in Path(helper_env["TEST_TOOL_LOG"]).read_text()


def test_helper_runs_from_other_directory_and_enables_worker(helper_env, tmp_path):
    result = run_helper(helper_env, tmp_path)
    assert result.returncode == 0, result.stderr
    calls = Path(helper_env["TEST_TOOL_LOG"]).read_text()
    assert "uv sync --frozen" in calls
    assert calls.index("up -d --wait db") < calls.index("alembic upgrade head")
    assert calls.index("alembic upgrade head") < calls.index("uvicorn")
    assert "--factory --host 127.0.0.1 --port 8000 embedded=1" in calls
    assert "down" not in calls


def test_disk_guard_rechecks_after_dependency_install(helper_env, tmp_path):
    helper_env["TEST_LOW_AFTER_SYNC"] = "1"
    result = run_helper(helper_env, tmp_path)
    assert result.returncode != 0
    assert "Less than 2 GiB free" in result.stderr
    calls = Path(helper_env["TEST_TOOL_LOG"]).read_text()
    assert "uv sync" in calls
    assert "pull db" not in calls


def test_failed_migration_never_starts_api_or_deletes_database(helper_env, tmp_path):
    helper_env["TEST_MIGRATION_FAIL"] = "1"
    result = run_helper(helper_env, tmp_path)
    assert result.returncode != 0
    assert "migration failed" in result.stderr
    calls = Path(helper_env["TEST_TOOL_LOG"]).read_text()
    assert "uvicorn" not in calls
    assert "down" not in calls
