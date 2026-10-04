"""Pure job state machine shared by the queue SQL and the property-based tests.

A job status is the persisted ``state`` plus the ``cancel_requested`` flag, which
distinguishes a requested cancellation (a worker still owns the lease and must
observe the flag) from an effective one (the ``CANCELLED`` state). Every SQL
transition in :mod:`services.jobs.queue` corresponds to one :class:`JobEvent`.
"""

from dataclasses import dataclass, replace
from enum import StrEnum
from typing import Final


class JobState(StrEnum):
    QUEUED = "queued"
    LEASED = "leased"
    RUNNING = "running"
    PUBLISHED = "published"
    CANCELLED = "cancelled"
    DELETED = "deleted"
    FAILED = "failed"


class JobEvent(StrEnum):
    CLAIM = "claim"
    START = "start"
    PUBLISH = "publish"
    FAIL = "fail"
    RELEASE = "release"
    RETRY = "retry"
    EXPIRE = "expire"
    REQUEST_CANCEL = "request_cancel"
    CANCEL = "cancel"
    DELETE = "delete"


@dataclass(frozen=True)
class JobStatus:
    state: JobState
    cancel_requested: bool = False


class IllegalTransition(Exception):
    def __init__(self, status: JobStatus, event: JobEvent):
        super().__init__(f"{event.value} is not allowed from {status.state.value}")
        self.status = status
        self.event = event


TERMINAL: Final = frozenset(
    {JobState.PUBLISHED, JobState.CANCELLED, JobState.DELETED, JobState.FAILED}
)
LEASED_STATES: Final = frozenset({JobState.LEASED, JobState.RUNNING})
ACTIVE: Final = frozenset({JobState.QUEUED, *LEASED_STATES})


def apply(status: JobStatus, event: JobEvent) -> JobStatus:
    """Return the status after ``event`` or raise :class:`IllegalTransition`."""
    state = status.state
    match event:
        case JobEvent.CLAIM if state is JobState.QUEUED:
            return replace(status, state=JobState.LEASED)
        case JobEvent.START if state is JobState.LEASED and not status.cancel_requested:
            return replace(status, state=JobState.RUNNING)
        case JobEvent.PUBLISH if state is JobState.RUNNING and not status.cancel_requested:
            return replace(status, state=JobState.PUBLISHED)
        case JobEvent.FAIL if state in LEASED_STATES:
            return replace(status, state=JobState.FAILED)
        case JobEvent.RELEASE | JobEvent.RETRY | JobEvent.EXPIRE if state in LEASED_STATES:
            if status.cancel_requested:
                return replace(status, state=JobState.CANCELLED)
            return replace(status, state=JobState.QUEUED)
        case JobEvent.REQUEST_CANCEL if state is JobState.QUEUED:
            return JobStatus(JobState.CANCELLED, cancel_requested=True)
        case JobEvent.REQUEST_CANCEL if state in LEASED_STATES:
            return replace(status, cancel_requested=True)
        case JobEvent.CANCEL if state in ACTIVE:
            return JobStatus(JobState.CANCELLED, cancel_requested=True)
        case JobEvent.DELETE if state is not JobState.DELETED:
            return replace(status, state=JobState.DELETED)
    raise IllegalTransition(status, event)


def is_legal(status: JobStatus, event: JobEvent) -> bool:
    try:
        apply(status, event)
    except IllegalTransition:
        return False
    return True


def check_invariants(status: JobStatus) -> None:
    """Raise ``AssertionError`` when a status violates the documented invariants."""
    if status.state is JobState.PUBLISHED and status.cancel_requested:
        raise AssertionError("A published job cannot carry a cancellation request")
    if status.state is JobState.QUEUED and status.cancel_requested:
        raise AssertionError("A queued cancellation request must be effective immediately")
    if status.state is JobState.CANCELLED and not status.cancel_requested:
        raise AssertionError("Effective cancellation must record the request")
