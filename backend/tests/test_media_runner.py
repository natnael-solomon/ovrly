import asyncio
import os
import sys

import pytest

from services.media.runner import CommandLimits, run_command

# These stdlib-only tool fixtures must not start coverage's subprocess SQLite writer
# under deliberately tiny file/CPU limits. The parent still measures run_command.
PYTHON_TOOL = [sys.executable, "-S", "-c"]


async def test_command_arguments_are_not_interpreted_by_a_shell():
    literal = "$(echo unsafe); 'quoted' & spaces"
    result = await run_command(
        [*PYTHON_TOOL, "import sys; print(sys.argv[1])", literal],
        CommandLimits(timeout_seconds=5, cpu_seconds=2, output_bytes=1024, file_bytes=1024),
    )
    assert result.returncode == 0
    assert result.stdout == (literal + "\n").encode()


async def test_timeout_terminates_the_command(tmp_path):
    pid_file = tmp_path / "pid"
    script = f"import os,time; open({str(pid_file)!r}, 'w').write(str(os.getpid())); time.sleep(2)"
    with pytest.raises(TimeoutError):
        await run_command(
            [*PYTHON_TOOL, script],
            CommandLimits(timeout_seconds=0.2, cpu_seconds=2, output_bytes=1024, file_bytes=1024),
        )
    with pytest.raises(ProcessLookupError):
        os.kill(int(pid_file.read_text()), 0)


@pytest.mark.parametrize("stream", ["stdout", "stderr"])
async def test_captured_output_is_bounded(stream):
    with pytest.raises(ValueError, match="output"):
        await run_command(
            [*PYTHON_TOOL, f"import sys; sys.{stream}.write('x'*4096)"],
            CommandLimits(timeout_seconds=5, cpu_seconds=2, output_bytes=1024, file_bytes=1024),
        )


async def test_kernel_bounds_output_file_size(tmp_path):
    output = tmp_path / "output"
    result = await run_command(
        [*PYTHON_TOOL, f"open({str(output)!r}, 'wb').write(b'x'*8192)"],
        CommandLimits(timeout_seconds=5, cpu_seconds=2, output_bytes=4096, file_bytes=1024),
    )
    assert result.returncode != 0
    assert output.stat().st_size <= 1024


async def test_kernel_bounds_cpu_time():
    result = await run_command(
        [*PYTHON_TOOL, "while True: pass"],
        CommandLimits(timeout_seconds=4, cpu_seconds=1, output_bytes=1024, file_bytes=1024),
    )
    assert result.returncode < 0


async def test_cancellation_reaps_the_command(tmp_path):
    pid_file = tmp_path / "pid"
    script = f"import os,time; open({str(pid_file)!r}, 'w').write(str(os.getpid())); time.sleep(10)"
    task = asyncio.create_task(
        run_command(
            [*PYTHON_TOOL, script],
            CommandLimits(timeout_seconds=5, cpu_seconds=2, output_bytes=1024, file_bytes=1024),
        )
    )
    async with asyncio.timeout(2):
        while not pid_file.exists():  # noqa: ASYNC110 - polling an external process's file
            await asyncio.sleep(0.01)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    with pytest.raises(ProcessLookupError):
        os.kill(int(pid_file.read_text()), 0)
