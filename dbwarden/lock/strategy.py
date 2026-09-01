"""Lock strategy protocol and auto-dispatch for per-engine locking.

Each database engine implements a strategy that provides the actual
locking mechanism. The auto-dispatch selects the right strategy based
on the database type.
"""
from __future__ import annotations

import hashlib
import os
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from dbwarden.logging import get_component_logger

logger = get_component_logger("lock")


@dataclass
class StatusRow:
    """In-memory representation of the lock status row."""
    namespace: str = "default"
    execution_id: str = ""
    owner_id: str = ""
    migration_version: str | None = None
    migration_checksum: str | None = None
    fencing_token: int = 0
    host: str = ""
    pid: int = 0
    db_connection_id: str | None = None
    state: str = "AVAILABLE"
    acquired_at: str = ""
    last_heartbeat_at: str = ""
    expires_at: str | None = None


@dataclass
class AcquireResult:
    """Result of a lock acquisition attempt."""
    success: bool
    status_row: StatusRow
    holder_description: str = ""
    error: str | None = None


@dataclass
class HolderInfo:
    """Information about the current lock holder."""
    execution_id: str
    owner_id: str
    host: str | None
    pid: int | None
    migration_version: str | None
    state: str
    acquired_at: str | None
    last_heartbeat_at: str | None
    is_alive: bool


@runtime_checkable
class LockStrategy(Protocol):
    """Protocol for per-engine lock strategies.

    Each engine (PostgreSQL, MySQL, SQLite, ClickHouse) implements
    this protocol to provide its native locking mechanism.
    """

    def ensure_table(self, connection: Any, schema: str = "public") -> None:
        """Create the status table if it doesn't exist."""
        ...

    def acquire(
        self,
        connection: Any,
        status_row: StatusRow,
        schema: str = "public",
    ) -> AcquireResult:
        """Attempt to acquire the migration lock.

        On success, returns AcquireResult(success=True).
        On failure, returns AcquireResult(success=False) with holder_description.
        """
        ...

    def release(
        self,
        connection: Any,
        namespace: str = "default",
        schema: str = "public",
    ) -> bool:
        """Release the migration lock. Returns True on success."""
        ...

    def describe_holder(
        self,
        connection: Any,
        namespace: str = "default",
        schema: str = "public",
    ) -> HolderInfo | None:
        """Get information about the current lock holder. Returns None if free."""
        ...

    def is_alive(
        self,
        connection: Any,
        namespace: str = "default",
        schema: str = "public",
    ) -> bool:
        """Check if the lock holder's process is still alive."""
        ...


def _derive_lock_key(namespace: str, database: str) -> int:
    """Derive a 64-bit lock key from namespace and database name.

    Used by PostgreSQL advisory locks and MySQL named locks.
    """
    raw = f"dbwarden:{namespace}:{database}"
    digest = hashlib.md5(raw.encode()).hexdigest()[:16]
    return int(digest, 16)


def _generate_execution_id() -> str:
    """Generate a unique execution ID for this migration run."""
    return uuid.uuid4().hex


def _generate_owner_id() -> str:
    """Generate a unique owner ID for this process."""
    return uuid.uuid4().hex


def get_strategy(db_type: str, **kwargs: Any) -> LockStrategy:
    """Select the lock strategy for the given database type.

    Auto-dispatches to the appropriate engine-specific implementation.
    Keyword arguments are forwarded to the strategy constructor.
    """
    if db_type == "postgresql":
        from dbwarden.lock.postgresql import PostgreSQLStrategy
        return PostgreSQLStrategy()
    if db_type in ("mysql", "mariadb"):
        from dbwarden.lock.mysql import MySQLStrategy
        return MySQLStrategy()
    if db_type == "clickhouse":
        from dbwarden.lock.clickhouse import ClickHouseStrategy
        return ClickHouseStrategy(**kwargs)
    # Default: SQLite (Grade B)
    from dbwarden.lock.sqlite import SQLiteStrategy
    return SQLiteStrategy()
