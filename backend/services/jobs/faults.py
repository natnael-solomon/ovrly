"""Fault-injection hooks used by the recovery harness; production installs no faults."""

from enum import StrEnum
from typing import Protocol

from services.jobs.queue import ClaimedJob


class Checkpoint(StrEnum):
    """Points in a job's execution where the worker consults the fault injector."""

    CLAIMED = "claimed"
    BEFORE_PUBLISH = "before_publish"
    AFTER_PUBLISH = "after_publish"


class SimulatedCrash(Exception):
    """Emulates the worker process dying: the worker performs no cleanup of its lease."""


class FaultInjector(Protocol):
    async def checkpoint(self, name: Checkpoint, job: ClaimedJob) -> None: ...


class NoFaults:
    async def checkpoint(self, name: Checkpoint, job: ClaimedJob) -> None:
        return None
