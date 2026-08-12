from __future__ import annotations

import time
from typing import Self

from dbwarden.logging import DBWardenLogger


class PhaseTimer:
    """Time a named pipeline phase and report its wall-clock duration.

    Durations are logged at INFO level so operators always see phase timing
    during migrations. When ``perf`` is enabled, callers use it as a signal to
    emit deeper per-item breakdowns (e.g. per-SQL-statement timing).
    """

    def __init__(
        self,
        logger: DBWardenLogger,
        name: str,
        *,
        perf: bool = False,
    ) -> None:
        self.logger = logger
        self.name = name
        self.perf = perf
        self._start: float | None = None

    def __enter__(self) -> Self:
        self._start = time.perf_counter()
        return self

    def __exit__(
        self,
        exc_type: object,
        exc_value: object,
        traceback: object,
    ) -> None:
        if self._start is None:
            return
        if exc_type is not None:
            return
        elapsed_ms = (time.perf_counter() - self._start) * 1000.0
        self.logger.info(f"{self.name} completed in {elapsed_ms:.1f}ms")
