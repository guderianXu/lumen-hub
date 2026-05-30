from __future__ import annotations

from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from threading import Lock


@dataclass(frozen=True)
class HardwareOperation:
    busy_message: str
    error_event: str
    operation: Callable[[], str]


class HardwareOperationQueue:
    """Serializes hardware writes so GUI actions cannot overlap on one device."""

    def __init__(self) -> None:
        self._pending: deque[HardwareOperation] = deque()
        self._active = False
        self._lock = Lock()

    @property
    def active(self) -> bool:
        with self._lock:
            return self._active

    @property
    def pending_count(self) -> int:
        with self._lock:
            return len(self._pending)

    def submit(self, operation: HardwareOperation) -> int:
        """Queue an operation.

        Returns 0 when the caller should start it immediately; otherwise returns
        the number of queued operations waiting behind the current one.
        """
        with self._lock:
            if not self._active:
                self._active = True
                return 0
            self._pending.append(operation)
            return len(self._pending)

    def complete_current(self) -> HardwareOperation | None:
        with self._lock:
            if self._pending:
                return self._pending.popleft()
            self._active = False
            return None

    def clear(self) -> None:
        with self._lock:
            self._pending.clear()
