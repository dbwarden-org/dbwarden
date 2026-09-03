"""SQLite lock strategy (Grade B).

SQLite uses the database file's own write lock, held for the entire
migration via BEGIN IMMEDIATE. Crash releases automatically via
OS/journal cleanup. Pause holds the lock (other writers block).

No heartbeat on SQLite: the heartbeat connection cannot write the
status row while the migration transaction holds the write lock.
Staleness is inferred from acquired_at + process liveness.
"""
from __future__ import annotations

import os
import socket
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
    _generate_execution_id,
    _generate_owner_id,
)
from dbwarden.logging import get_component_logger

logger = get_component_logger("lock")


class SQLiteStrategy:
    """SQLite locking via BEGIN IMMEDIATE transaction.

    The entire migration runs in one BEGIN IMMEDIATE transaction,
    which acquires the write lock on the database file.
    Crash releases automatically. Pause holds the lock.
    """

    def ensure_table(self, connection: Any, schema: str = "public") -> None:
        ensure_lock_table(connection, "sqlite", schema)

    def acquire(
        self,
        connection: Any,
        status_row: StatusRow,
        schema: str = "public",
    ) -> AcquireResult:
        # Check if someone else holds the lock
        existing = read_status_row(
            connection, namespace=status_row.namespace, db_type="sqlite", schema=schema
        )
        if existing and existing.get("state") == "RUNNING":
            # Check if the holder's process is alive
            holder_pid = existing.get("pid")
            if holder_pid and _is_process_alive(holder_pid):
                return AcquireResult(
                    success=False,
                    status_row=status_row,
                    holder_description=_describe_existing_holder(existing),
                )
            # Holder is dead; we can take over
            logger.info(
                "Previous holder (pid=%s) is dead; taking over lock",
                holder_pid,
            )

        # Write status row before BEGIN IMMEDIATE
        upsert_status_row(
            connection,
            namespace=status_row.namespace,
            execution_id=status_row.execution_id,
            owner_id=status_row.owner_id,
            migration_version=status_row.migration_version,
            migration_checksum=status_row.migration_checksum,
            fencing_token=status_row.fencing_token,
            db_connection_id=status_row.db_connection_id,
            state="RUNNING",
            db_type="sqlite",
            schema=schema,
        )
        connection.commit()

        # Sec 7.3.2: Explicitly set busy_timeout (default 0 = fail fast)
        from dbwarden.config import get_database
        try:
            config = get_database()
            busy_timeout = getattr(config, "sqlite_busy_timeout", 0) or 0
            connection.execute(text(f"PRAGMA busy_timeout = {busy_timeout}"))
            logger.debug("SQLite busy_timeout set to %d ms", busy_timeout)
        except Exception:
            # Default to 0 (fail fast)
            connection.execute(text("PRAGMA busy_timeout = 0"))

        # BEGIN IMMEDIATE acquires the write lock
        # This lock is held on THIS connection until it is closed or committed.
        # The caller must NOT close this connection until migration is complete.
        try:
            connection.execute(text("BEGIN IMMEDIATE"))
            logger.info("SQLite BEGIN IMMEDIATE acquired on connection")
        except Exception as exc:
            # SQLITE_BUSY: another writer holds the lock
            logger.warning("Failed to acquire SQLite write lock: %s", exc)
            update_state(
                connection,
                namespace=status_row.namespace,
                state="AVAILABLE",
                db_type="sqlite",
                schema=schema,
            )
            connection.commit()
            return AcquireResult(
                success=False,
                status_row=status_row,
                holder_description="SQLite write lock held by another process",
                error=str(exc),
            )

        return AcquireResult(success=True, status_row=status_row)

    def release(
        self,
        connection: Any,
        namespace: str = "default",
        schema: str = "public",
    ) -> bool:
        try:
            # COMMIT releases the SQLite write lock
            connection.execute(text("COMMIT"))
        except Exception:
            # May already be committed; try rollback as fallback
            try:
                connection.execute(text("ROLLBACK"))
            except Exception:
                pass

        # Update status to COMPLETE
        try:
            update_state(
                connection,
                namespace=namespace,
                state="COMPLETE",
                db_type="sqlite",
                schema=schema,
            )
            connection.commit()
        except Exception as exc:
            logger.warning("Failed to update status after release: %s", exc)

        return True

    def describe_holder(
        self,
        connection: Any,
        namespace: str = "default",
        schema: str = "public",
    ) -> HolderInfo | None:
        row = read_status_row(
            connection, namespace=namespace, db_type="sqlite", schema=schema
        )
        if row is None:
            return None

        pid = row.get("pid")
        is_alive = _is_process_alive(pid) if pid else False

        return HolderInfo(
            execution_id=row.get("execution_id", ""),
            owner_id=row.get("owner_id", ""),
            host=row.get("host"),
            pid=pid,
            migration_version=row.get("migration_version"),
            state=row.get("state", "UNKNOWN"),
            acquired_at=row.get("acquired_at"),
            last_heartbeat_at=row.get("last_heartbeat_at"),
            is_alive=is_alive,
        )

    def is_alive(
        self,
        connection: Any,
        namespace: str = "default",
        schema: str = "public",
    ) -> bool:
        row = read_status_row(
            connection, namespace=namespace, db_type="sqlite", schema=schema
        )
        if row is None:
            return False
        pid = row.get("pid")
        return _is_process_alive(pid) if pid else False


def _is_process_alive(pid: int | None) -> bool:
    """Check if a process with the given PID is alive."""
    if pid is None:
        return False
    try:
        os.kill(pid, 0)  # Signal 0: check existence without sending signal
        return True
    except (OSError, ProcessLookupError):
        return False
    except PermissionError:
        # Process exists but we don't have permission to signal it
        return True


def _describe_existing_holder(row: dict) -> str:
    """Build a human-readable description from an existing status row."""
    parts = []
    if row.get("host"):
        parts.append(f"host={row['host']}")
    if row.get("pid"):
        parts.append(f"pid={row['pid']}")
    if row.get("execution_id"):
        parts.append(f"execution={row['execution_id'][:12]}")
    if row.get("migration_version"):
        parts.append(f"migration={row['migration_version']}")
    return "SQLite write lock held by: " + (", ".join(parts) if parts else "unknown process")
