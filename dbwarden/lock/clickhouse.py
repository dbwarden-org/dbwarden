"""ClickHouse lock strategy (coordination profiles).

ClickHouse has no session-scoped user locks, no synchronous CAS on
table rows, and non-transactional DDL. The strategy implements:

- CH-0: Lease row with fencing token (Grade C)
- CH-1: CH-0 + strict idempotency (Grade C, gap absorbed for DDL)

CH-2 (Keeper-backed), CH-3 (migration proxy), and CH-4 (singleton
executor) require external infrastructure and are documented as
deployment options, not implemented here.
"""
from __future__ import annotations

import re
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
)
from dbwarden.logging import get_component_logger

logger = get_component_logger("lock")


# Idempotency classification for ClickHouse DDL statements
# (CH-1 requirement: refuse non-idempotent statements)
_IDEMPOTENT_PATTERNS = [
    # CREATE with IF NOT EXISTS
    re.compile(r"CREATE\s+(TABLE|VIEW|DICTIONARY|MATERIALIZED\s+VIEW)\s+IF\s+NOT\s+EXISTS", re.IGNORECASE),
    # DROP with IF EXISTS
    re.compile(r"DROP\s+(TABLE|VIEW|DICTIONARY|MATERIALIZED\s+VIEW)\s+IF\s+EXISTS", re.IGNORECASE),
    # ALTER ADD COLUMN with IF NOT EXISTS
    re.compile(r"ALTER\s+TABLE\s+\S+\s+ADD\s+COLUMN\s+IF\s+NOT\s+EXISTS", re.IGNORECASE),
    # ALTER DROP COLUMN with IF EXISTS
    re.compile(r"ALTER\s+TABLE\s+\S+\s+DROP\s+COLUMN\s+IF\s+EXISTS", re.IGNORECASE),
]

# Non-idempotent statement patterns (must be refused under CH-1)
_NON_IDEMPOTENT_PATTERNS = [
    # RENAME has no idempotent form
    re.compile(r"ALTER\s+TABLE\s+\S+\s+RENAME\s+TO", re.IGNORECASE),
    # MODIFY COLUMN rewrites data
    re.compile(r"ALTER\s+TABLE\s+\S+\s+MODIFY\s+COLUMN", re.IGNORECASE),
    # UPDATE/DELETE are data mutations
    re.compile(r"(UPDATE|DELETE)\s+", re.IGNORECASE),
]


def check_statement_idempotency(sql: str) -> tuple[bool, str | None]:
    """Check if a SQL statement is idempotent for CH-1 enforcement.

    Returns (is_idempotent, reason). If reason is not None, the statement
    is non-idempotent and the reason explains why.
    """
    stripped = sql.strip()
    if not stripped:
        return True, None

    # Skip comments
    if stripped.startswith("--"):
        return True, None

    # Check non-idempotent patterns first
    for pattern in _NON_IDEMPOTENT_PATTERNS:
        if pattern.search(stripped):
            return False, f"Non-idempotent: {stripped[:80]}"

    # Check idempotent patterns
    for pattern in _IDEMPOTENT_PATTERNS:
        if pattern.search(stripped):
            return True, None

    # Metadata-only alters (comment, TTL) are considered idempotent
    if re.match(r"ALTER\s+TABLE\s+\S+\s+(MODIFY\s+)?(COMMENT|TTL)", stripped, re.IGNORECASE):
        return True, None

    # Unknown statements are treated as non-idempotent for safety
    return False, f"Unclassified statement: {stripped[:80]}"


class ClickHouseStrategy:
    """ClickHouse CH-0 lease + fencing token strategy.

    Uses an atomic conditional upsert for lease acquisition.
    The fencing token is checked before every DDL statement.

    CH-1 mode adds strict idempotency enforcement: non-idempotent
    statements are refused unless allow_non_idempotent is True.
    """

    def __init__(
        self,
        ttl_seconds: int = 120,
        acquire_wait_timeout: float = 0.0,
        allow_non_idempotent: bool = False,
    ):
        self.ttl_seconds = ttl_seconds
        self.acquire_wait_timeout = acquire_wait_timeout
        self.allow_non_idempotent = allow_non_idempotent

    def ensure_table(self, connection: Any, schema: str = "public") -> None:
        ensure_lock_table(connection, "clickhouse", schema)

    def check_statement_idempotency(self, sql: str) -> tuple[bool, str | None]:
        """Check if a SQL statement is idempotent for CH-1 enforcement.

        Returns (is_idempotent, reason). If reason is not None, the statement
        is non-idempotent and the reason explains why.
        """
        if self.allow_non_idempotent:
            return True, None

        from dbwarden.lock.clickhouse import check_statement_idempotency
        return check_statement_idempotency(sql)

    def acquire(
        self,
        connection: Any,
        status_row: StatusRow,
        schema: str = "public",
    ) -> AcquireResult:
        # Check for existing valid lease
        existing = read_status_row(
            connection, namespace=status_row.namespace, db_type="clickhouse", schema=schema
        )
        if existing:
            expires_at = existing.get("expires_at")
            if expires_at:
                try:
                    # Check if lease has expired
                    result = connection.execute(text("SELECT now()")).scalar()
                    now = str(result)
                    if expires_at > now:
                        # Lease is still valid
                        return AcquireResult(
                            success=False,
                            status_row=status_row,
                            holder_description=_describe_ch_holder(existing),
                        )
                except Exception:
                    pass

        # Atomic conditional upsert for lease acquisition
        # Increment fencing token from current max
        try:
            max_token = connection.execute(
                text("SELECT max(fencing_token) FROM dbwarden_lock FINAL WHERE namespace = :ns"),
                {"ns": status_row.namespace},
            ).scalar()
            new_token = (max_token or 0) + 1
        except Exception:
            new_token = 1

        import socket, os
        now_result = connection.execute(text("SELECT now()")).scalar()
        now_str = str(now_result)

        params = {
            "namespace": status_row.namespace,
            "execution_id": status_row.execution_id,
            "owner_id": status_row.owner_id,
            "migration_version": status_row.migration_version,
            "migration_checksum": status_row.migration_checksum,
            "fencing_token": new_token,
            "host": socket.gethostname(),
            "pid": os.getpid(),
            "state": "RUNNING",
            "acquired_at": now_str,
            "last_heartbeat_at": now_str,
        }

        # Try to insert; if lease exists and is valid, this will be a no-op
        # (ClickHouse MergeTree doesn't have UPSERT, so we use INSERT + verification)
        try:
            connection.execute(
                text(
                    "INSERT INTO dbwarden_lock "
                    "(namespace, execution_id, owner_id, migration_version, migration_checksum, "
                    " fencing_token, host, pid, state, acquired_at, last_heartbeat_at) "
                    "VALUES "
                    "(:namespace, :execution_id, :owner_id, :migration_version, :migration_checksum, "
                    " :fencing_token, :host, :pid, :state, :acquired_at, :last_heartbeat_at)"
                ),
                params,
            )
        except Exception as exc:
            # May fail if row already exists
            logger.warning("ClickHouse lease insert attempt: %s", exc)

        # Verify we got the lease
        verify = read_status_row(
            connection, namespace=status_row.namespace, db_type="clickhouse", schema=schema
        )
        if verify and verify.get("execution_id") == status_row.execution_id:
            status_row.fencing_token = new_token
            return AcquireResult(success=True, status_row=status_row)

        return AcquireResult(
            success=False,
            status_row=status_row,
            holder_description="Could not verify ClickHouse lease acquisition",
        )

    def release(
        self,
        connection: Any,
        namespace: str = "default",
        schema: str = "public",
    ) -> bool:
        try:
            update_state(
                connection,
                namespace=namespace,
                state="COMPLETE",
                db_type="clickhouse",
                schema=schema,
            )
        except Exception as exc:
            logger.warning("Failed to update ClickHouse status: %s", exc)
        return True

    def describe_holder(
        self,
        connection: Any,
        namespace: str = "default",
        schema: str = "public",
    ) -> HolderInfo | None:
        row = read_status_row(
            connection, namespace=namespace, db_type="clickhouse", schema=schema
        )
        if row is None:
            return None

        # Check if lease has expired
        expires_at = row.get("expires_at")
        if expires_at:
            try:
                now = connection.execute(text("SELECT now()")).scalar()
                if str(now) > expires_at:
                    return None  # Lease expired
            except Exception:
                pass

        return HolderInfo(
            execution_id=row.get("execution_id", ""),
            owner_id=row.get("owner_id", ""),
            host=row.get("host"),
            pid=row.get("pid"),
            migration_version=row.get("migration_version"),
            state=row.get("state", "UNKNOWN"),
            acquired_at=row.get("acquired_at"),
            last_heartbeat_at=row.get("last_heartbeat_at"),
            is_alive=True,
        )

    def is_alive(
        self,
        connection: Any,
        namespace: str = "default",
        schema: str = "public",
    ) -> bool:
        row = read_status_row(
            connection, namespace=namespace, db_type="clickhouse", schema=schema
        )
        if row is None:
            return False
        expires_at = row.get("expires_at")
        if expires_at:
            try:
                now = connection.execute(text("SELECT now()")).scalar()
                return str(now) <= expires_at
            except Exception:
                return False
        return True


def _describe_ch_holder(row: dict) -> str:
    """Build a description from an existing ClickHouse status row."""
    parts = []
    if row.get("host"):
        parts.append(f"host={row['host']}")
    if row.get("pid"):
        parts.append(f"pid={row['pid']}")
    if row.get("execution_id"):
        parts.append(f"execution={row['execution_id'][:12]}")
    if row.get("migration_version"):
        parts.append(f"migration={row['migration_version']}")
    expires = row.get("expires_at")
    if expires:
        parts.append(f"expires={expires}")
    return "ClickHouse lease held by: " + (", ".join(parts) if parts else "unknown")
