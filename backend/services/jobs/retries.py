"""Retry classes, the exceptions stage handlers raise and the policy that schedules them.

A handler reports *why* a stage attempt could not finish by raising one of the typed
exceptions below. The worker never inspects messages (they may contain private details);
it consults :class:`RetryPolicy`, which either schedules the job through
``jobs.available_at`` or declares the class exhausted so the job fails with the class
recorded. Retries are counted per class in ``jobs.retry_counts`` so each cap is
independent of how often the job was re-leased after a crash.
"""

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from secrets import SystemRandom
from typing import TYPE_CHECKING, ClassVar

if TYPE_CHECKING:
    from services.settings import Settings


class RetryClass(StrEnum):
    TRANSIENT = "transient"
    RATE_LIMITED = "rate_limited"
    NON_RETRIABLE_INPUT = "non_retriable_input"
    INVALID_MODEL_SCHEMA = "invalid_model_schema"
    UNKNOWN_OUTCOME = "unknown_outcome"


class RetryableError(Exception):
    """Base of the typed outcomes a handler raises; ``retry_class`` selects the policy."""

    retry_class: ClassVar[RetryClass]


class Transient(RetryableError):
    """Provider 5xx, connection reset or similar: exponential backoff with jitter."""

    retry_class = RetryClass.TRANSIENT


class RateLimited(RetryableError):
    """Provider 429: wait for the provider's hint when given, otherwise back off."""

    retry_class = RetryClass.RATE_LIMITED

    def __init__(self, retry_after_seconds: float | None = None):
        super().__init__("rate limited")
        self.retry_after_seconds = retry_after_seconds


class ProviderCooldown(RateLimited):
    """A validated provider minimum, not an exponential-backoff ceiling."""


class NonRetriableInput(RetryableError):
    """The input can never succeed (unsupported media, rejected content): fail now."""

    retry_class = RetryClass.NON_RETRIABLE_INPUT


class InvalidModelSchema(RetryableError):
    """The model output failed schema validation: a bounded number of repair attempts."""

    retry_class = RetryClass.INVALID_MODEL_SCHEMA


class UnknownOutcome(RetryableError):
    """The provider call timed out after the request id was recorded.

    The next attempt must reconcile by ``ClaimedJob.provider_request_id`` instead of
    calling the provider again. The policy refuses to schedule this class when no request
    id was recorded, because re-running the stage would silently repeat the call.
    """

    retry_class = RetryClass.UNKNOWN_OUTCOME


@dataclass(frozen=True)
class RetryDecision:
    retry_class: RetryClass
    count: int
    delay_seconds: float | None

    @property
    def exhausted(self) -> bool:
        return self.delay_seconds is None


@dataclass(frozen=True)
class RetryPolicy:
    transient_attempts: int = 5
    rate_limited_attempts: int = 5
    schema_repair_attempts: int = 2
    unknown_outcome_attempts: int = 3
    backoff_seconds: float = 1
    max_backoff_seconds: float = 60
    # Injected so tests are deterministic; SystemRandom avoids the pseudo-random lint rule.
    jitter: Callable[[], float] = SystemRandom().random

    @classmethod
    def from_settings(cls, settings: "Settings") -> "RetryPolicy":
        return cls(
            transient_attempts=settings.job_retry_transient_attempts,
            rate_limited_attempts=settings.job_retry_rate_limited_attempts,
            schema_repair_attempts=settings.job_retry_schema_repair_attempts,
            unknown_outcome_attempts=settings.job_retry_unknown_outcome_attempts,
            backoff_seconds=settings.job_retry_backoff_seconds,
            max_backoff_seconds=settings.job_retry_max_backoff_seconds,
        )

    def cap(self, retry_class: RetryClass) -> int:
        match retry_class:
            case RetryClass.TRANSIENT:
                return self.transient_attempts
            case RetryClass.RATE_LIMITED:
                return self.rate_limited_attempts
            case RetryClass.INVALID_MODEL_SCHEMA:
                return self.schema_repair_attempts
            case RetryClass.UNKNOWN_OUTCOME:
                return self.unknown_outcome_attempts
            case RetryClass.NON_RETRIABLE_INPUT:
                return 0

    def backoff(self, count: int) -> float:
        """Full-jitter exponential backoff for the ``count``-th retry (1-based)."""
        ceiling: float = min(self.max_backoff_seconds, self.backoff_seconds * 2 ** (count - 1))
        return ceiling * self.jitter()

    def decide(
        self,
        error: RetryableError,
        retry_counts: Mapping[str, int],
        *,
        request_id_recorded: bool,
    ) -> RetryDecision:
        """Decide whether ``error`` schedules another attempt and after how long."""
        retry_class = error.retry_class
        count = retry_counts.get(retry_class.value, 0) + 1
        if count > self.cap(retry_class):
            return RetryDecision(retry_class, count, None)
        if retry_class is RetryClass.UNKNOWN_OUTCOME and not request_id_recorded:
            return RetryDecision(retry_class, count, None)
        if isinstance(error, RateLimited) and error.retry_after_seconds is not None:
            if isinstance(error, ProviderCooldown):
                return RetryDecision(retry_class, count, error.retry_after_seconds)
            hinted = min(max(error.retry_after_seconds, 0), self.max_backoff_seconds)
            return RetryDecision(retry_class, count, hinted)
        return RetryDecision(retry_class, count, self.backoff(count))
