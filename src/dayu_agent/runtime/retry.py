"""Bounded retry policy owned by the application runtime."""

import random
from collections.abc import Callable

from pydantic import BaseModel, ConfigDict, Field, model_validator


class RetryBudget(BaseModel):
    """Cap provider attempts, elapsed time, backoff, and jitter for one AgentRun."""

    model_config = ConfigDict(frozen=True)

    max_attempts: int = Field(default=3, ge=1, le=10)
    max_elapsed_seconds: float = Field(default=15.0, gt=0, le=600)
    base_delay: float = Field(default=0.25, ge=0, le=60)
    max_delay: float = Field(default=2.0, ge=0, le=120)
    jitter: bool = True

    @model_validator(mode="after")
    def validate_delay_range(self) -> "RetryBudget":
        """Require the backoff cap to be at least the initial delay."""

        if self.max_delay < self.base_delay:
            raise ValueError("max_delay must be greater than or equal to base_delay")
        return self

    def delay_after(
        self,
        failed_attempt: int,
        *,
        random_value: Callable[[], float] = random.random,
    ) -> float:
        """Return capped exponential backoff with bounded multiplicative jitter."""

        delay = min(self.max_delay, self.base_delay * (2 ** max(0, failed_attempt - 1)))
        if self.jitter and delay:
            delay *= 0.5 + random_value()
        return float(min(delay, self.max_delay))

    def permits_retry(self, *, failed_attempt: int, elapsed: float, delay: float) -> bool:
        """Allow another attempt only when both attempt and wall-clock budgets remain."""

        return failed_attempt < self.max_attempts and elapsed + delay < self.max_elapsed_seconds
