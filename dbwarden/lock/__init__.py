"""Migration locking v2.

Provides per-engine native locking with session-scoped safety,
heartbeat observability, and recovery state machine.

Public API:
    acquire_lock()     -- Acquire the migration lock
    release_lock()     -- Release the migration lock
    check_lock()       -- Check if lock is held
    get_lock_status()  -- Get full status row
    ensure_lock_table() -- Create/migrate the lock table
"""
from __future__ import annotations

import os
import socket
import uuid
from dataclasses import dataclass
from typing import Any

from dbwarden.lock.state import LockState, compute_health, describe_holder, validate_transition
from dbwarden.lock.strategy import (
    AcquireResult,
    HolderInfo,
    StatusRow,
    get_strategy,
    _generate_execution_id,
    _generate_owner_id,
)
from dbwarden.lock.table import (
    ensure_lock_table as _ensure_lock_table,
    read_status_row as _read_status_row,
    update_heartbeat as _update_heartbeat,
    update_state as _update_state,
    upsert_status_row as _upsert_status_row,
)
from dbwarden.logging import get_component_logger

logger = get_component_logger("lock")


def ensure_lock_table(db_name: str | None = None) -> None:
    """Create the v2 lock table if it doesn't exist.

    Also handles migration from v1 schema.
    """
    from dbwarden.config import get_database
    from dbwarden.connection.connection import get_db_connection

    config = get_database(db_name)
    db_type = config.database_type
    schema = getattr(config, "postgres_schema", "public") or "public"

    with get_db_connection(db_name) as conn:
        _ensure_lock_table(conn, db_type, schema)


@dataclass
class LockAcquisition:
    """Result of a lock acquisition attempt."""
    acquired: bool
    execution_id: str
    owner_id: str
    strategy: Any
    namespace: str = "default"
    holder_description: str = ""
    error: str | None = None


def acquire_lock(
    db_name: str | None = None,
    *,
    namespace: str | None = None,
    migration_version: str | None = None,
    migration_checksum: str | None = None,
) -> LockAcquisition:
    """Acquire the migration lock for the given database.

    Uses per-engine native locking (advisory locks for PG, named locks
    for MySQL, BEGIN IMMEDIATE for SQLite, lease for ClickHouse).

    Returns a LockAcquisition with acquired=True on success.
    """
    from dbwarden.config import get_database
    from dbwarden.connection.connection import get_db_connection, _get_engine, _sandbox_url_var, _sandbox_db_type_var, _probe_connection

    config = get_database(db_name)
    db_type = config.database_type
    schema = getattr(config, "postgres_schema", "public") or "public"

    # Resolve namespace: explicit arg > config default > "default"
    effective_namespace = namespace or getattr(config, "lock_namespace", None) or "default"

    # Build strategy kwargs from config
    strategy_kwargs: dict[str, Any] = {}
    if db_type == "clickhouse":
        ttl = getattr(config, "clickhouse_lock_ttl", None)
        if ttl is not None:
            strategy_kwargs["ttl_seconds"] = ttl

    # For lock operations, we need a raw connection (not inside engine.begin()).
    # SQLite needs BEGIN IMMEDIATE which can't run inside an existing transaction.
    sandbox_url = _sandbox_url_var.get()
    sandbox_db_type = _sandbox_db_type_var.get()
    url = sandbox_url if sandbox_url is not None else config.sqlalchemy_url
    effective_db_type = sandbox_db_type if sandbox_db_type is not None else db_type

    engine = _get_engine(url, effective_db_type)
    from dbwarden.logging import get_logger
    _probe_connection(engine, effective_db_type, get_logger(), url)

    if effective_db_type == "clickhouse":
        conn = engine.connect()
    else:
        # Raw connection without BEGIN — SQLite strategy manages its own transaction
        conn = engine.connect()

    strategy = get_strategy(db_type, **strategy_kwargs)
    execution_id = _generate_execution_id()
    owner_id = _generate_owner_id()

    status_row = StatusRow(
        namespace=effective_namespace,
        execution_id=execution_id,
        owner_id=owner_id,
        migration_version=migration_version,
        migration_checksum=migration_checksum,
        host=socket.gethostname(),
        pid=os.getpid(),
    )

    try:
        result = strategy.acquire(conn, status_row, schema)
    except Exception:
        # Close on error
        try:
            conn.close()
        except Exception:
            pass
        raise

    # For SQLite, the connection must stay open to hold BEGIN IMMEDIATE.
    # For other engines, we can close the lock connection since the lock
    # is either session-scoped (PG advisory) or connection-scoped (MySQL GET_LOCK).
    # The caller is responsible for managing the connection lifecycle.
    if effective_db_type != "sqlite" and not result.success:
        try:
            conn.close()
        except Exception:
            pass

    return LockAcquisition(
        acquired=result.success,
        execution_id=execution_id,
        owner_id=owner_id,
        strategy=strategy,
        namespace=effective_namespace,
        holder_description=result.holder_description,
        error=result.error,
    )


def release_lock(
    db_name: str | None = None,
    *,
    namespace: str = "default",
    strategy: Any = None,
) -> bool:
    """Release the migration lock.

    Args:
        db_name: Database name from config.
        namespace: Lock namespace (default: "default").
        strategy: The strategy instance used for acquisition. If None,
                  auto-selects based on database type.
    """
    from dbwarden.config import get_database
    from dbwarden.connection.connection import _get_engine, _sandbox_url_var, _sandbox_db_type_var, _probe_connection

    config = get_database(db_name)
    db_type = config.database_type
    schema = getattr(config, "postgres_schema", "public") or "public"

    if strategy is None:
        strategy = get_strategy(db_type)

    sandbox_url = _sandbox_url_var.get()
    sandbox_db_type = _sandbox_db_type_var.get()
    url = sandbox_url if sandbox_url is not None else config.sqlalchemy_url
    effective_db_type = sandbox_db_type if sandbox_db_type is not None else db_type

    engine = _get_engine(url, effective_db_type)
    from dbwarden.logging import get_logger
    _probe_connection(engine, effective_db_type, get_logger(), url)
    conn = engine.connect()

    try:
        return strategy.release(conn, namespace, schema)
    finally:
        try:
            conn.close()
        except Exception:
            pass


def update_lock_heartbeat(
    db_name: str | None = None,
    *,
    namespace: str = "default",
    execution_id: str,
) -> None:
    """Update the heartbeat timestamp for a running migration."""
    from dbwarden.config import get_database
    from dbwarden.connection.connection import get_db_connection

    config = get_database(db_name)
    db_type = config.database_type
    schema = getattr(config, "postgres_schema", "public") or "public"

    with get_db_connection(db_name) as conn:
        _update_heartbeat(
            conn,
            namespace=namespace,
            execution_id=execution_id,
            db_type=db_type,
            schema=schema,
        )


def check_lock(db_name: str | None = None, *, namespace: str = "default") -> bool:
    """Check if the migration lock is currently held and alive."""
    from dbwarden.config import get_database
    from dbwarden.connection.connection import _get_engine, _sandbox_url_var, _sandbox_db_type_var, _probe_connection

    config = get_database(db_name)
    db_type = config.database_type
    schema = getattr(config, "postgres_schema", "public") or "public"

    strategy = get_strategy(db_type)

    sandbox_url = _sandbox_url_var.get()
    sandbox_db_type = _sandbox_db_type_var.get()
    url = sandbox_url if sandbox_url is not None else config.sqlalchemy_url
    effective_db_type = sandbox_db_type if sandbox_db_type is not None else db_type

    engine = _get_engine(url, effective_db_type)
    from dbwarden.logging import get_logger
    _probe_connection(engine, effective_db_type, get_logger(), url)
    conn = engine.connect()

    try:
        holder = strategy.describe_holder(conn, namespace, schema)
        if holder is None:
            return False
        return holder.is_alive
    finally:
        try:
            conn.close()
        except Exception:
            pass


def get_lock_status(db_name: str | None = None, *, namespace: str = "default") -> dict[str, Any] | None:
    """Get the full lock status row for a namespace.

    Returns a dict with all status fields, or None if no row exists.
    """
    from dbwarden.config import get_database
    from dbwarden.connection.connection import _get_engine, _sandbox_url_var, _sandbox_db_type_var, _probe_connection

    config = get_database(db_name)
    db_type = config.database_type
    schema = getattr(config, "postgres_schema", "public") or "public"

    sandbox_url = _sandbox_url_var.get()
    sandbox_db_type = _sandbox_db_type_var.get()
    url = sandbox_url if sandbox_url is not None else config.sqlalchemy_url
    effective_db_type = sandbox_db_type if sandbox_db_type is not None else db_type

    engine = _get_engine(url, effective_db_type)
    from dbwarden.logging import get_logger
    _probe_connection(engine, effective_db_type, get_logger(), url)
    conn = engine.connect()

    try:
        return _read_status_row(conn, namespace=namespace, db_type=db_type, schema=schema)
    finally:
        try:
            conn.close()
        except Exception:
            pass


def force_release_lock(db_name: str | None = None, *, namespace: str = "default") -> bool:
    """Force release a lock as an explicit recovery operation.

    This is the v1-compatible force release. For v2, prefer
    terminate_holder() which kills the holder's server connection.
    """
    from dbwarden.config import get_database
    from dbwarden.connection.connection import _get_engine, _sandbox_url_var, _sandbox_db_type_var, _probe_connection

    config = get_database(db_name)
    db_type = config.database_type
    schema = getattr(config, "postgres_schema", "public") or "public"

    sandbox_url = _sandbox_url_var.get()
    sandbox_db_type = _sandbox_db_type_var.get()
    url = sandbox_url if sandbox_url is not None else config.sqlalchemy_url
    effective_db_type = sandbox_db_type if sandbox_db_type is not None else db_type

    engine = _get_engine(url, effective_db_type)
    from dbwarden.logging import get_logger
    _probe_connection(engine, effective_db_type, get_logger(), url)
    conn = engine.connect()

    try:
        _update_state(
            conn,
            namespace=namespace,
            state="AVAILABLE",
            db_type=db_type,
            schema=schema,
        )
        return True
    except Exception as exc:
        logger.warning("Failed to force release lock: %s", exc)
        return False
    finally:
        try:
            conn.close()
        except Exception:
            pass
