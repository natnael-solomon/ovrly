"""Execute argument-based media commands on Linux/WSL."""

import asyncio
import os
import shutil
import signal
from contextlib import suppress
from dataclasses import dataclass


@dataclass(frozen=True)
class CommandLimits:
    timeout_seconds: float
    cpu_seconds: int
    output_bytes: int
    file_bytes: int


@dataclass(frozen=True)
class CommandResult:
    returncode: int
    stdout: bytes
    stderr: bytes


class MediaToolUnavailable(RuntimeError):
    """A required executable is missing from the worker environment."""


class MediaOutputTooLarge(ValueError):
    """A tool exceeded its bounded output budget."""


async def run_command(argv: list[str], limits: CommandLimits) -> CommandResult:
    executable = shutil.which(argv[0])
    limiter = shutil.which("prlimit")
    if executable is None or limiter is None:
        raise MediaToolUnavailable("Required media executable is unavailable")
    if min(limits.timeout_seconds, limits.cpu_seconds, limits.output_bytes, limits.file_bytes) <= 0:
        raise ValueError("Command limits must be positive")
    process = await asyncio.create_subprocess_exec(
        limiter,
        f"--cpu={limits.cpu_seconds}:{limits.cpu_seconds}",
        f"--fsize={limits.file_bytes}:{limits.file_bytes}",
        "--",
        executable,
        *argv[1:],
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        start_new_session=True,
    )

    async def read(stream: asyncio.StreamReader | None) -> bytes:
        if stream is None:
            raise RuntimeError("Media command pipe was not created")
        result = bytearray()
        while chunk := await stream.read(min(65536, limits.output_bytes + 1)):
            result.extend(chunk)
            if len(result) > limits.output_bytes:
                raise MediaOutputTooLarge("Media command output limit exceeded")
        return bytes(result)

    stdout = asyncio.create_task(read(process.stdout))
    stderr = asyncio.create_task(read(process.stderr))
    waiting = asyncio.create_task(process.wait())
    try:
        async with asyncio.timeout(limits.timeout_seconds):
            out, err, code = await asyncio.gather(stdout, stderr, waiting)
        return CommandResult(code, out, err)
    finally:
        # Kill the group even if its leader exited while a child still owns a pipe.
        with suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGKILL)
        for task in (stdout, stderr, waiting):
            task.cancel()
        await asyncio.gather(stdout, stderr, waiting, return_exceptions=True)
        await process.communicate()
