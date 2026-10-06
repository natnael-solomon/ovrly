"""Fault-injection hooks used by the recovery harness; production installs no faults."""

from enum import StrEnum
from typing import Protocol

from services.jobs.queue import ClaimedJob


class Checkpoint(StrEnum):
    """Points in a job's execution where the worker consults the fault injector.

    The first three are hit by the worker; the stage-level checkpoints are hit by stage
    handlers through ``JobContext.checkpoint`` once a stage calls a provider or stores an
    artifact. Speech uses these hooks with HTTP replay; legacy queue tests also use stubs.
    """

    CLAIMED = "claimed"
    BEFORE_PUBLISH = "before_publish"
    AFTER_PUBLISH = "after_publish"
    BEFORE_PROVIDER_CALL = "before_provider_call"
    AFTER_PROVIDER_CALL = "after_provider_call"
    AFTER_ARTIFACT_STORE = "after_artifact_store"


class SimulatedCrash(BaseException):
    """Emulates the worker process dying: the worker performs no cleanup of its lease.

    A ``BaseException`` so that a crash raised from a stage-level checkpoint inside a
    handler is not treated as a handler failure or retried; it ends the worker task.
    """


class FaultInjector(Protocol):
    async def checkpoint(self, name: Checkpoint, job: ClaimedJob) -> None: ...


class NoFaults:
    async def checkpoint(self, name: Checkpoint, job: ClaimedJob) -> None:
        return None
