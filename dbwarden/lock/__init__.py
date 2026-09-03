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
    fencing_token: int = 0
    holder_description: str = ""
    error: str | None = None
    connection: Any = None


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
        fencing_token=status_row.fencing_token,
        holder_description=result.holder_description,
        error=result.error,
        connection=conn if effective_db_type == "sqlite" and result.success else None,
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


def terminate_holder(db_name: str | None = None, *, namespace: str = "default") -> bool:
    """Terminate the holder's server connection and release the lock.

    This is the v2 unlock mechanism per spec Sec 8.5:
    - PostgreSQL: SELECT pg_terminate_backend(pid)
    - MySQL: KILL connection_id
    - SQLite: Refuse (file locks are OS-held)
    - ClickHouse: Advance fencing token

    Returns True if the holder was terminated successfully.
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
        # Get the holder info first
        strategy = get_strategy(db_type)
        holder = strategy.describe_holder(conn, namespace, schema)
        if holder is None:
            logger.info("No lock holder found for namespace '%s'", namespace)
            return True

        # Terminate based on engine type
        if effective_db_type == "postgresql":
            if holder.pid:
                logger.info("Terminating PostgreSQL backend PID %d", holder.pid)
                conn.execute(text("SELECT pg_terminate_backend(:pid)"), {"pid": holder.pid})
                logger.info("PostgreSQL backend terminated")

        elif effective_db_type in ("mysql", "mariadb"):
            if holder.pid:
                logger.info("Terminating MySQL connection ID %d", holder.pid)
                conn.execute(text("KILL :connection_id"), {"connection_id": holder.pid})
                logger.info("MySQL connection terminated")

        elif effective_db_type == "clickhouse":
            # ClickHouse: advance fencing token to invalidate old lease
            logger.info("Advancing ClickHouse fencing token to invalidate lease")
            try:
                conn.execute(
                    text("ALTER TABLE dbwarden_lock UPDATE fencing_token = fencing_token + 1 WHERE namespace = :ns"),
                    {"ns": namespace},
                )
                logger.info("ClickHouse fencing token advanced")
            except Exception as exc:
                logger.warning("Failed to advance fencing token: %s", exc)

        else:
            # SQLite: cannot terminate (file locks are OS-held)
            logger.warning(
                "Cannot terminate SQLite holder. Kill process %d manually on host %s",
                holder.pid or 0,
                holder.host or "unknown",
            )
            return False

        # Update status to AVAILABLE
        _update_state(
            conn,
            namespace=namespace,
            state="AVAILABLE",
            db_type=db_type,
            schema=schema,
        )
        return True

    except Exception as exc:
        logger.warning("Failed to terminate holder: %s", exc)
        return False
    finally:
        try:
            conn.close()
        except Exception:
            pass


# --- INSPECTING procedure and recovery policy ---


@dataclass
class InspectionResult:
    """Result of inspecting a dead predecessor's migration state."""
    predecessor_execution_id: str
    predecessor_migration_version: str | None
    predecessor_checksum: str | None
    candidate_checksum: str | None
    checksum_match: bool
    last_recorded_step: str | None
    is_resumable: bool
    needs_review: bool
    reason: str


def inspect_dead_predecessor(
    db_name: str | None = None,
    *,
    namespace: str = "default",
    candidate_migration_version: str | None = None,
    candidate_checksum: str | None = None,
) -> InspectionResult | None:
    """Inspect a dead predecessor's migration state.

    This implements the INSPECTING procedure from the migration locking spec.
    When a new worker acquires the lock after a DEAD predecessor, it must
    execute this procedure before resuming.

    Returns InspectionResult if a dead predecessor is found, None otherwise.
    """
    from dbwarden.config import get_database
    from dbwarden.connection.connection import get_db_connection, _get_engine, _sandbox_url_var, _sandbox_db_type_var, _probe_connection

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
        # Read current status row
        status = _read_status_row(conn, namespace=namespace, db_type=effective_db_type, schema=schema)
        if status is None:
            return None

        state_str = status.get("state", "AVAILABLE")
        try:
            state = LockState(state_str)
        except ValueError:
            state = LockState.AVAILABLE

        # Only inspect if previous state was DEAD or FAILED
        if state not in (LockState.DEAD, LockState.FAILED):
            return None

        predecessor_execution_id = status.get("execution_id", "")
        predecessor_migration_version = status.get("migration_version")
        predecessor_checksum = status.get("migration_checksum")

        # Compare checksums
        checksum_match = (
            predecessor_checksum is not None
            and candidate_checksum is not None
            and predecessor_checksum == candidate_checksum
        )

        # Determine if resumable
        is_resumable = checksum_match
        needs_review = not checksum_match

        if checksum_match:
            reason = "Checksums match; migration can be resumed"
        elif predecessor_checksum is None:
            reason = "No checksum recorded; requires human review"
        elif candidate_checksum is None:
            reason = "No candidate checksum; requires human review"
        else:
            reason = "Checksum mismatch; migration content changed; requires human review"

        return InspectionResult(
            predecessor_execution_id=predecessor_execution_id,
            predecessor_migration_version=predecessor_migration_version,
            predecessor_checksum=predecessor_checksum,
            candidate_checksum=candidate_checksum,
            checksum_match=checksum_match,
            last_recorded_step=predecessor_migration_version,
            is_resumable=is_resumable,
            needs_review=needs_review,
            reason=reason,
        )

    finally:
        try:
            conn.close()
        except Exception:
            pass


def apply_recovery_policy(
    db_name: str | None = None,
    *,
    namespace: str = "default",
    inspection: InspectionResult,
    policy: str = "halt",
) -> LockState:
    """Apply recovery policy after inspecting a dead predecessor.

    Policies:
    - halt (default): transition to NEEDS_REVIEW, print report, exit
    - resume_idempotent: resume only if all remaining statements are idempotent
    - force: re-run unconditionally (audit-logged)

    Returns the target state to transition to.
    """
    if inspection.is_resumable and policy == "resume_idempotent":
        logger.info(
            "Recovery policy resume_idempotent: checksums match, resuming migration"
        )
        return LockState.RUNNING

    if policy == "force":
        logger.warning(
            "Recovery policy force: re-running migration unconditionally"
        )
        return LockState.RUNNING

    # Default: halt policy
    logger.warning(
        "Recovery policy halt: transitioning to NEEDS_REVIEW. "
        "Predecessor execution=%s, migration=%s, reason=%s",
        inspection.predecessor_execution_id,
        inspection.predecessor_migration_version,
        inspection.reason,
    )
    return LockState.NEEDS_REVIEW


def get_recovery_policy(db_name: str | None = None) -> str:
    """Get the configured recovery policy.

    Returns one of: 'halt', 'resume_idempotent', 'force'.
    """
    from dbwarden.config import get_database
    try:
        config = get_database(db_name)
        return getattr(config, "recovery_policy", "halt") or "halt"
    except Exception:
        return "halt"
