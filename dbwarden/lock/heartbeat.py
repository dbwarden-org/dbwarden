"""Background heartbeat task for migration locking v2.

The heartbeat runs on a separate connection and updates the
`last_heartbeat_at` timestamp in the status row. This allows
other processes to detect stale (STUCK) or dead workers.

On native-lock engines (PG, MySQL), heartbeat failure is an
observability event only; the migration continues. On fallback
engines (ClickHouse CH-0), heartbeat failure is fatal.
"""
from __future__ import annotations

import threading
import time
from typing import Any

from dbwarden.lock.table import update_heartbeat, read_status_row
from dbwarden.logging import get_component_logger

logger = get_component_logger("lock")


class HeartbeatTask:
    """Background heartbeat that updates the status row periodically.

    Runs on its own thread with a separate database connection.
    Does not interfere with the migration connection.

    Args:
        db_name: Database name for connection resolution.
        namespace: Lock namespace.
        execution_id: The execution ID to heartbeat for.
        interval_seconds: Seconds between heartbeats (default 15).
        ttl_seconds: Seconds after which a heartbeat is stale (default 45).
        fatal_grace: Number of consecutive heartbeat failures before aborting
            on fallback engines (default 1). Only applies to non-native-lock
            engines where heartbeat is a correctness input.
    """

    def __init__(
        self,
        db_name: str | None = None,
        namespace: str = "default",
        execution_id: str = "",
        interval_seconds: float = 15.0,
        ttl_seconds: int = 45,
        fatal_grace: int = 1,
    ):
        self.db_name = db_name
        self.namespace = namespace
        self.execution_id = execution_id
        self.interval_seconds = interval_seconds
        self.ttl_seconds = ttl_seconds
        self.fatal_grace = fatal_grace

        self._stopped = threading.Event()
        self._thread: threading.Thread | None = None
        self._degraded = False
        self._fatal = False
        self._consecutive_failures = 0

    @property
    def is_degraded(self) -> bool:
        """True if the last heartbeat failed (native engines only)."""
        return self._degraded

    @property
    def is_fatal(self) -> bool:
        """True if fatal grace was exhausted (fallback engines only)."""
        return self._fatal

    def start(self) -> None:
        """Start the heartbeat background thread."""
        if self._thread is not None and self._thread.is_alive():
            return

        self._stopped.clear()
        self._degraded = False
        self._fatal = False
        self._consecutive_failures = 0

        self._thread = threading.Thread(
            target=self._run,
            name=f"dbwarden-heartbeat-{self.namespace}",
            daemon=True,
        )
        self._thread.start()
        logger.debug(
            "Heartbeat started (interval=%ss, ttl=%ss)",
            self.interval_seconds,
            self.ttl_seconds,
        )

    def stop(self) -> None:
        """Stop the heartbeat background thread."""
        self._stopped.set()
        if self._thread is not None:
            self._thread.join(timeout=self.interval_seconds + 5)
            self._thread = None
        logger.debug("Heartbeat stopped")

    def _run(self) -> None:
        """Background loop that updates the heartbeat timestamp."""
        while not self._stopped.is_set():
            try:
                self._do_heartbeat()
                self._consecutive_failures = 0
            except Exception as exc:
                self._consecutive_failures += 1
                self._degraded = True

                if self._consecutive_failures >= self.fatal_grace:
                    self._fatal = True
                    logger.error(
                        "Heartbeat fatal: %d consecutive failures: %s",
                        self._consecutive_failures,
                        exc,
                    )
                else:
                    logger.warning(
                        "Heartbeat failed (attempt %d/%d): %s",
                        self._consecutive_failures,
                        self.fatal_grace,
                        exc,
                    )

            self._stopped.wait(timeout=self.interval_seconds)

    def _do_heartbeat(self) -> None:
        """Execute a single heartbeat update."""
        from dbwarden.config import get_database
        from dbwarden.connection.connection import get_db_connection

        config = get_database(self.db_name)
        db_type = config.database_type
        schema = getattr(config, "postgres_schema", "public") or "public"

        with get_db_connection(self.db_name) as conn:
            update_heartbeat(
                conn,
                namespace=self.namespace,
                execution_id=self.execution_id,
                db_type=db_type,
                schema=schema,
            )
