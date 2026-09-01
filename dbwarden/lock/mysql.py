"""MySQL/MariaDB lock strategy (Grade A).

Uses named user locks via GET_LOCK() on the migration connection.
The lock is bound to the connection; worker death triggers connection
close, which releases the lock automatically.

Key properties:
- Single named lock per session (MariaDB compatibility)
- Primary check: @@global.read_only must be 0
- Session wait_timeout set to 86400s with TCP keepalive
- Holder discovery via IS_USED_LOCK()
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


class MySQLStrategy:
    """MySQL/MariaDB named lock strategy.

    Uses GET_LOCK() (non-blocking with timeout) for mutual exclusion.
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
        ensure_lock_table(connection, "mysql", schema)

    def acquire(
        self,
        connection: Any,
        status_row: StatusRow,
        schema: str = "public",
    ) -> AcquireResult:
        # Step 1: Primary check
        if not self._check_writable(connection):
            return AcquireResult(
                success=False,
                status_row=status_row,
                holder_description="Target is read-only (@@global.read_only or super_read_only is set)",
                error="Cannot acquire lock on a read-only instance",
            )

        # Step 2: Configure session for long-running locks
        try:
            connection.execute(text("SET SESSION wait_timeout = 86400"))
            connection.execute(text("SET SESSION interactive_timeout = 86400"))
        except Exception:
            pass  # Some MySQL versions may not allow this

        # Step 3: Acquire named lock
        lock_name = _mysql_lock_name(status_row.namespace, "primary")
        deadline = time.monotonic() + self.acquire_wait_timeout
        attempt = 0

        while True:
            attempt += 1
            # GET_LOCK returns: 1=acquired, 0=timeout, NULL=error
            # Use 0 timeout for non-blocking check
            result = connection.execute(
                text("SELECT GET_LOCK(:lock_name, 0)"),
                {"lock_name": lock_name},
            )
            lock_result = result.scalar()

            if lock_result == 1:
                break
            elif lock_result is None:
                return AcquireResult(
                    success=False,
                    status_row=status_row,
                    holder_description="GET_LOCK returned NULL (error)",
                    error="GET_LOCK failed",
                )

            # Lock held by someone else
            holder = self._describe_named_lock_holder(connection, lock_name)
            logger.info(
                "Named lock contention (attempt %d): %s", attempt, holder
            )

            if time.monotonic() >= deadline:
                return AcquireResult(
                    success=False,
                    status_row=status_row,
                    holder_description=holder or "Unknown holder",
                    error=f"Could not acquire named lock within {self.acquire_wait_timeout}s",
                )

            backoff = min(
                self.acquire_backoff_base * (2 ** min(attempt - 1, 5)),
                self.acquire_backoff_max,
            )
            import random
            backoff *= 0.8 + random.random() * 0.4
            time.sleep(backoff)

        # Step 4: Write status row
        status_row.db_connection_id = str(
            self._get_connection_id(connection)
        )

        upsert_status_row(
            connection,
            namespace=status_row.namespace,
            execution_id=status_row.execution_id,
            owner_id=status_row.owner_id,
            migration_version=status_row.migration_version,
            migration_checksum=status_row.migration_checksum,
            fencing_token=status_row.fencing_token,
            state="RUNNING",
            db_type="mysql",
            schema=schema,
        )

        return AcquireResult(success=True, status_row=status_row)

    def release(
        self,
        connection: Any,
        namespace: str = "default",
        schema: str = "public",
    ) -> bool:
        lock_name = _mysql_lock_name(namespace, "primary")
        try:
            connection.execute(
                text("SELECT RELEASE_LOCK(:lock_name)"),
                {"lock_name": lock_name},
            )
        except Exception as exc:
            logger.warning("Failed to release named lock: %s", exc)

        # Update status to COMPLETE
        try:
            update_state(
                connection,
                namespace=namespace,
                state="COMPLETE",
                db_type="mysql",
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
        lock_name = _mysql_lock_name(namespace, "primary")

        # IS_USED_LOCK returns the connection ID of the holder, or NULL/0
        result = connection.execute(
            text("SELECT IS_USED_LOCK(:lock_name)"),
            {"lock_name": lock_name},
        )
        holder_conn_id = result.scalar()

        if not holder_conn_id:
            return None

        # Get holder details
        row = connection.execute(
            text(
                "SELECT ID, USER, HOST, DB, COMMAND, TIME, STATE "
                "FROM information_schema.PROCESSLIST "
                "WHERE ID = :conn_id"
            ),
            {"conn_id": holder_conn_id},
        ).mappings().first()

        # Also read the status row
        status = read_status_row(
            connection, namespace=namespace, db_type="mysql", schema=schema
        )

        return HolderInfo(
            execution_id=status.get("execution_id", "") if status else "",
            owner_id=status.get("owner_id", "") if status else "",
            host=row.get("HOST", "") if row else "",
            pid=holder_conn_id,
            migration_version=status.get("migration_version") if status else None,
            state=status.get("state", "UNKNOWN") if status else "UNKNOWN",
            acquired_at=status.get("acquired_at") if status else None,
            last_heartbeat_at=status.get("last_heartbeat_at") if status else None,
            is_alive=True,  # If IS_USED_LOCK shows it, it's alive
        )

    def is_alive(
        self,
        connection: Any,
        namespace: str = "default",
        schema: str = "public",
    ) -> bool:
        lock_name = _mysql_lock_name(namespace, "primary")
        result = connection.execute(
            text("SELECT IS_USED_LOCK(:lock_name)"),
            {"lock_name": lock_name},
        )
        return result.scalar() is not None

    def _check_writable(self, connection: Any) -> bool:
        """Verify the instance is writable (not read-only)."""
        try:
            ro = connection.execute(text("SELECT @@global.read_only")).scalar()
            sro = connection.execute(text("SELECT @@global.super_read_only")).scalar()
            return not ro and not sro
        except Exception:
            return True

    def _get_connection_id(self, connection: Any) -> int | None:
        """Get the current connection's ID."""
        try:
            result = connection.execute(text("SELECT CONNECTION_ID()"))
            return result.scalar()
        except Exception:
            return None

    def _describe_named_lock_holder(self, connection: Any, lock_name: str) -> str | None:
        """Get a description of who holds the named lock."""
        try:
            result = connection.execute(
                text("SELECT IS_USED_LOCK(:lock_name)"),
                {"lock_name": lock_name},
            )
            conn_id = result.scalar()
            if not conn_id:
                return None

            row = connection.execute(
                text(
                    "SELECT USER, HOST, COMMAND, TIME "
                    "FROM information_schema.PROCESSLIST "
                    "WHERE ID = :conn_id"
                ),
                {"conn_id": conn_id},
            ).mappings().first()

            if row is None:
                return f"Named lock held by connection {conn_id} (details unavailable)"

            parts = [f"connection={conn_id}"]
            if row.get("USER"):
                parts.append(f"user={row['USER']}")
            if row.get("HOST"):
                parts.append(f"host={row['HOST']}")
            return "Named lock held by: " + ", ".join(parts)
        except Exception:
            return None


def _mysql_lock_name(namespace: str, database: str) -> str:
    """Generate a MySQL lock name (≤64 chars, UTF8-safe)."""
    raw = f"dbwarden.{namespace}.{database}"
    if len(raw) <= 64:
        return raw
    # Truncate with hash suffix
    digest = hashlib.md5(raw.encode()).hexdigest()[:8]
    return f"dbwarden.{namespace}.{digest}"
