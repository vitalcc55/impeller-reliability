from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from time import monotonic

from impeller_reliability.persistence.project_errors import ProjectOperationError


@dataclass(frozen=True, slots=True)
class RequestDeadline:
    expires_at: float
    _clock: Callable[[], float] = field(default=monotonic, repr=False, compare=False)

    @classmethod
    def start(cls, timeout_ms: int, *, clock: Callable[[], float] = monotonic) -> RequestDeadline:
        return cls(expires_at=clock() + timeout_ms / 1000, _clock=clock)

    def check(self, stage: str) -> None:
        if self._clock() < self.expires_at:
            return
        raise ProjectOperationError(
            "timeout",
            "Операция не завершена в установленный срок. Состояние проекта не изменено.",
            details={"stage": stage},
            retryable=True,
        )
