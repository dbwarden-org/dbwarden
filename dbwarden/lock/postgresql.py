"""PostgreSQL lock strategy (Grade A).

Uses session-level advisory locks on the migration connection.
The lock is a property of the connection; worker death triggers
connection teardown, which releases the lock automatically.

Key properties:
- pg_try_advisory_lock with bounded retry (not blocking)
- Primary check: pg_is_in_recovery() must return false
- Pooler detection: pg_backend_pid consistency check
- Holder discovery via pg_locks + pg_stat_activity
"""
from __future__ import annotations

import hashlib
import time
from typing import Any

from sqlalchemy import text

from dbwarden.lock.table import (
    ensure_lock_table,
    read_status_row,
    update_state,
    upsert_status_row,
)
from dbwarden.lock.strategy import (
    AcquireResult,
    HolderInfo,
    LockStrategy,
    StatusRow,
    _derive_lock_key,
)
from dbwarden.logging import get_component_logger

logger = get_component_logger("lock")


class PostgreSQLStrategy:
    """PostgreSQL advisory lock strategy.

    Uses pg_try_advisory_lock (non-blocking) with a bounded retry loop.
    The lock is released automatically when the connection closes.
    """

    def __init__(
        self,
        acquire_wait_timeout: float = 0.0,
        acquire_backoff_base: float = 1.0,
        acquire_backoff_max: float = 30.0,
    ):
        self.acquire_wait_timeout = acquire_wait_timeout
        self.acquire_backoff_base = acquire_backoff_base
        self.acquire_backoff_max = acquire_backoff_max

    def ensure_table(self, connection: Any, schema: str = "public") -> None:
        ensure_lock_table(connection, "postgresql", schema)

    def acquire(
        self,
        connection: Any,
        status_row: StatusRow,
        schema: str = "public",
    ) -> AcquireResult:
        # Step 1: Primary check
        if not self._check_primary(connection):
            return AcquireResult(
                success=False,
                status_row=status_row,
                holder_description="Target is a read replica (pg_is_in_recovery() returned true)",
                error="Cannot acquire lock on a read replica",
            )

        # Step 2: Pooler detection
        if self._detect_transaction_pooling(connection):
            return AcquireResult(
                success=False,
                status_row=status_row,
                holder_description="Transaction-pooling proxy detected (PgBouncer in transaction mode)",
                error="Session-level advisory locks are meaningless under transaction pooling",
            )

        # Step 3: Acquire advisory lock with bounded retry
        lock_key = _derive_lock_key(status_row.namespace, "primary")
        deadline = time.monotonic() + self.acquire_wait_timeout
        attempt = 0

        while True:
            attempt += 1
            result = connection.execute(
                text("SELECT pg_try_advisory_lock(CAST(:lock_key AS bigint))"),
                {"lock_key": lock_key},
            )
            acquired = result.scalar()
            if acquired:
                break

            # Lock held by someone else
            holder = self._describe_advisory_holder(connection, lock_key)
            logger.info(
                "Advisory lock contention (attempt %d): %s", attempt, holder
            )

            if time.monotonic() >= deadline:
                return AcquireResult(
                    success=False,
                    status_row=status_row,
                    holder_description=holder or "Unknown holder",
                    error=f"Could not acquire advisory lock within {self.acquire_wait_timeout}s",
                )

            backoff = min(
                self.acquire_backoff_base * (2 ** min(attempt - 1, 5)),
                self.acquire_backoff_max,
            )
            # Add 20% jitter
            import random
            backoff *= 0.8 + random.random() * 0.4
            time.sleep(backoff)

        # Step 4: Write status row
        # Get the backend PID for the status row
        backend_pid_result = connection.execute(text("SELECT pg_backend_pid()"))
        backend_pid = backend_pid_result.scalar()
        status_row.db_connection_id = str(backend_pid) if backend_pid else None

        upsert_status_row(
            connection,
            namespace=status_row.namespace,
            execution_id=status_row.execution_id,
            owner_id=status_row.owner_id,
            migration_version=status_row.migration_version,
            migration_checksum=status_row.migration_checksum,
            fencing_token=status_row.fencing_token,
            state="RUNNING",
            db_type="postgresql",
            schema=schema,
        )

        return AcquireResult(success=True, status_row=status_row)

    def release(
        self,
        connection: Any,
        namespace: str = "default",
        schema: str = "public",
    ) -> bool:
        lock_key = _derive_lock_key(namespace, "primary")
        try:
            connection.execute(
                text("SELECT pg_advisory_unlock(:lock_key)"),
                {"lock_key": lock_key},
            )
        except Exception as exc:
            logger.warning("Failed to release advisory lock: %s", exc)

        # Update status to COMPLETE
        try:
            update_state(
                connection,
                namespace=namespace,
                state="COMPLETE",
                db_type="postgresql",
                schema=schema,
            )
        except Exception as exc:
            logger.warning("Failed to update status after release: %s", exc)

        return True

    def describe_holder(
        self,
        connection: Any,
        namespace: str = "default",
        schema: str = "public",
    ) -> HolderInfo | None:
        lock_key = _derive_lock_key(namespace, "primary")

        # Check if advisory lock is held
        lock_held = connection.execute(
            text(
                "SELECT pid FROM pg_locks "
                "WHERE locktype = 'advisory' "
                "AND (classid::bigint << 32 | objid::bigint) = :lock_key"
            ),
            {"lock_key": lock_key},
        ).mappings().first()

        if lock_held is None:
            return None

        # Get holder details from pg_stat_activity
        row = connection.execute(
            text(
                "SELECT a.pid, a.client_addr, a.application_name, "
                "a.backend_start, a.state "
                "FROM pg_stat_activity a "
                "WHERE a.pid = :pid"
            ),
            {"pid": lock_held["pid"]},
        ).mappings().first()

        if row is None:
            return None

        # Also read the status row for execution/migration info
        status = read_status_row(
            connection, namespace=namespace, db_type="postgresql", schema=schema
        )

        return HolderInfo(
            execution_id=status.get("execution_id", "") if status else "",
            owner_id=status.get("owner_id", "") if status else "",
            host=str(row.get("client_addr", "")),
            pid=row.get("pid"),
            migration_version=status.get("migration_version") if status else None,
            state=status.get("state", "UNKNOWN") if status else "UNKNOWN",
            acquired_at=status.get("acquired_at") if status else None,
            last_heartbeat_at=status.get("last_heartbeat_at") if status else None,
            is_alive=True,  # If pg_locks shows it, it's alive
        )

    def is_alive(
        self,
        connection: Any,
        namespace: str = "default",
        schema: str = "public",
    ) -> bool:
        lock_key = _derive_lock_key(namespace, "primary")
        result = connection.execute(
            text(
                "SELECT pid FROM pg_locks "
                "WHERE locktype = 'advisory' "
                "AND (classid::bigint << 32 | objid::bigint) = CAST(:lock_key AS bigint)"
            ),
            {"lock_key": lock_key},
        ).first()
        return result is not None

    def _check_primary(self, connection: Any) -> bool:
        """Verify this is a primary, not a read replica."""
        try:
            result = connection.execute(text("SELECT pg_is_in_recovery()"))
            return result.scalar() is False
        except Exception:
            # If the function doesn't exist, assume primary
            return True

    def _detect_transaction_pooling(self, connection: Any) -> bool:
        """Detect transaction-pooling proxies like PgBouncer.

        Calls pg_backend_pid() twice across a statement boundary.
        If the backend PIDs differ, we're under transaction pooling.
        """
        try:
            pid1 = connection.execute(text("SELECT pg_backend_pid()")).scalar()
            # Execute a trivial statement to potentially get a new backend
            connection.execute(text("SELECT 1"))
            pid2 = connection.execute(text("SELECT pg_backend_pid()")).scalar()
            return pid1 != pid2
        except Exception:
            return False

    def _describe_advisory_holder(self, connection: Any, lock_key: int) -> str | None:
        """Get a description of who holds the advisory lock."""
        try:
            row = connection.execute(
                text(
                    "SELECT a.pid, a.client_addr, a.application_name "
                    "FROM pg_locks l "
                    "JOIN pg_stat_activity a ON a.pid = l.pid "
                    "WHERE l.locktype = 'advisory' "
                    "AND (l.classid::bigint << 32 | l.objid::bigint) = CAST(:lock_key AS bigint)"
                ),
                {"lock_key": lock_key},
            ).mappings().first()

            if row is None:
                return None

            parts = [f"pid={row['pid']}"]
            if row.get("client_addr"):
                parts.append(f"client={row['client_addr']}")
            if row.get("application_name"):
                parts.append(f"app={row['application_name']}")
            return "Advisory lock held by: " + ", ".join(parts)
        except Exception:
            return None
