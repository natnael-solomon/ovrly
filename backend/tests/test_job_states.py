import itertools

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from services.jobs.states import (
    ACTIVE,
    TERMINAL,
    IllegalTransition,
    JobEvent,
    JobState,
    JobStatus,
    apply,
    check_invariants,
    is_legal,
)

events = st.lists(st.sampled_from(list(JobEvent)), max_size=40)


@settings(max_examples=500)
@given(events)
def test_random_transition_sequences_never_reach_an_invalid_state(sequence):
    status = JobStatus(JobState.QUEUED)
    check_invariants(status)
    for event in sequence:
        before = status
        try:
            status = apply(status, event)
        except IllegalTransition as rejected:
            assert rejected.status == before
            assert status == before, "A rejected transition must not change the status"
            continue
        check_invariants(status)
        assert isinstance(status.state, JobState)
        if before.state is JobState.DELETED:
            raise AssertionError("Deleted jobs are tombstones and never transition")
        if before.state in TERMINAL:
            assert status.state is JobState.DELETED, "Terminal states only move to deleted"
        if status.state is JobState.PUBLISHED:
            assert before.state is JobState.RUNNING
            assert not before.cancel_requested
        if before.cancel_requested and status.state in ACTIVE:
            assert status.cancel_requested, "A cancellation request is never forgotten"
            assert status.state is before.state, "A requested cancel cannot progress a job"


@pytest.mark.parametrize(
    ("state", "flag", "event"),
    list(itertools.product(JobState, (False, True), JobEvent)),
)
def test_every_transition_matches_the_table(state, flag, event):
    """Exhaustive oracle over all states, cancellation flags and events."""
    status = JobStatus(state, flag)
    legal = is_legal(status, event)
    if state is JobState.DELETED:
        assert not legal
    elif event is JobEvent.DELETE:
        assert legal
    elif state in TERMINAL:
        assert not legal
    elif event is JobEvent.PUBLISH:
        assert legal == (state is JobState.RUNNING and not flag)
    elif event is JobEvent.START:
        assert legal == (state is JobState.LEASED and not flag)
    elif event is JobEvent.CLAIM:
        assert legal == (state is JobState.QUEUED)
    else:
        assert legal == (
            state in ACTIVE
            and (
                event in (JobEvent.REQUEST_CANCEL, JobEvent.CANCEL) or state is not JobState.QUEUED
            )
        )


def test_requested_versus_effective_cancellation():
    queued = JobStatus(JobState.QUEUED)
    assert apply(queued, JobEvent.REQUEST_CANCEL) == JobStatus(JobState.CANCELLED, True)
    running = apply(apply(queued, JobEvent.CLAIM), JobEvent.START)
    requested = apply(running, JobEvent.REQUEST_CANCEL)
    assert requested == JobStatus(JobState.RUNNING, True)
    assert not is_legal(requested, JobEvent.PUBLISH)
    assert apply(requested, JobEvent.CANCEL) == JobStatus(JobState.CANCELLED, True)
    assert apply(requested, JobEvent.EXPIRE) == JobStatus(JobState.CANCELLED, True)
    assert apply(running, JobEvent.EXPIRE) == JobStatus(JobState.QUEUED)
    assert apply(running, JobEvent.RETRY) == JobStatus(JobState.QUEUED)
    assert apply(requested, JobEvent.RETRY) == JobStatus(JobState.CANCELLED, True)
    assert not is_legal(queued, JobEvent.RETRY)
