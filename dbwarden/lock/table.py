"""Status table DDL and CRUD for migration locking v2.

The status table stores observability data about migration runs.
On native-lock engines (PostgreSQL, MySQL), the row is a dashboard,
not the lock itself. On the fallback engine (ClickHouse), it also
serves as the lease record.
"""
from __future__ import annotations

import socket
from typing import Any

from sqlalchemy import text

from dbwarden.logging import get_component_logger

logger = get_component_logger("lock")


# --- DDL ---

_LOCK_TABLE_V2_DDL = {
    "sqlite": """
        CREATE TABLE IF NOT EXISTS dbwarden_lock (
            namespace           TEXT        NOT NULL DEFAULT 'default',
            execution_id        TEXT        NOT NULL,
            owner_id            TEXT        NOT NULL,
            migration_version   TEXT,
            migration_checksum  TEXT,
            fencing_token       INTEGER     NOT NULL DEFAULT 0,
            host                TEXT,
            pid                 INTEGER,
            db_connection_id    TEXT,
            state               TEXT        NOT NULL DEFAULT 'AVAILABLE',
            acquired_at         TEXT        NOT NULL,
            last_heartbeat_at   TEXT        NOT NULL,
            expires_at          TEXT,
            PRIMARY KEY (namespace)
        )
    """,
    "postgresql": """
        CREATE TABLE IF NOT EXISTS {schema}.dbwarden_lock (
            namespace           TEXT        NOT NULL DEFAULT 'default',
            execution_id        TEXT        NOT NULL,
            owner_id            TEXT        NOT NULL,
            migration_version   TEXT,
            migration_checksum  TEXT,
            fencing_token       BIGINT      NOT NULL DEFAULT 0,
            host                TEXT,
            pid                 INTEGER,
            db_connection_id    TEXT,
            state               TEXT        NOT NULL DEFAULT 'AVAILABLE',
            acquired_at         TIMESTAMPTZ NOT NULL,
            last_heartbeat_at   TIMESTAMPTZ NOT NULL,
            expires_at          TIMESTAMPTZ,
            PRIMARY KEY (namespace)
        )
    """,
    "mysql": """
        CREATE TABLE IF NOT EXISTS dbwarden_lock (
            namespace           VARCHAR(255) NOT NULL DEFAULT 'default',
            execution_id        VARCHAR(255) NOT NULL,
            owner_id            VARCHAR(255) NOT NULL,
            migration_version   VARCHAR(255),
            migration_checksum  VARCHAR(255),
            fencing_token       BIGINT       NOT NULL DEFAULT 0,
            host                VARCHAR(255),
            pid                 INTEGER,
            db_connection_id    VARCHAR(255),
            state               VARCHAR(50)  NOT NULL DEFAULT 'AVAILABLE',
            acquired_at         TIMESTAMP    NOT NULL,
            last_heartbeat_at   TIMESTAMP    NOT NULL,
            expires_at          TIMESTAMP    NULL,
            PRIMARY KEY (namespace)
        )
    """,
    "clickhouse": """
        CREATE TABLE IF NOT EXISTS dbwarden_lock (
            namespace           String      DEFAULT 'default',
            execution_id        String      NOT NULL,
            owner_id            String      NOT NULL,
            migration_version   Nullable(String),
            migration_checksum  Nullable(String),
            fencing_token       Int64       DEFAULT 0,
            host                Nullable(String),
            pid                 Nullable(Int32),
            db_connection_id    Nullable(String),
            state               String      DEFAULT 'AVAILABLE',
            acquired_at         DateTime    NOT NULL,
            last_heartbeat_at   DateTime    NOT NULL,
            expires_at          Nullable(DateTime)
        ) ENGINE = MergeTree()
        ORDER BY namespace
    """,
}

# --- Status row CRUD ---

_UPSERT_STATUS_ROW = {
    "sqlite": """
        INSERT INTO dbwarden_lock
            (namespace, execution_id, owner_id, migration_version, migration_checksum,
             fencing_token, host, pid, state, acquired_at, last_heartbeat_at)
        VALUES
            (:namespace, :execution_id, :owner_id, :migration_version, :migration_checksum,
             :fencing_token, :host, :pid, :state, :acquired_at, :last_heartbeat_at)
        ON CONFLICT (namespace) DO UPDATE SET
            execution_id = excluded.execution_id,
            owner_id = excluded.owner_id,
            migration_version = excluded.migration_version,
            migration_checksum = excluded.migration_checksum,
            fencing_token = excluded.fencing_token,
            host = excluded.host,
            pid = excluded.pid,
            state = excluded.state,
            acquired_at = excluded.acquired_at,
            last_heartbeat_at = excluded.last_heartbeat_at
    """,
    "postgresql": """
        INSERT INTO {schema}.dbwarden_lock
            (namespace, execution_id, owner_id, migration_version, migration_checksum,
             fencing_token, host, pid, state, acquired_at, last_heartbeat_at)
        VALUES
            (:namespace, :execution_id, :owner_id, :migration_version, :migration_checksum,
             :fencing_token, :host, :pid, :state, :acquired_at, :last_heartbeat_at)
        ON CONFLICT (namespace) DO UPDATE SET
            execution_id = EXCLUDED.execution_id,
            owner_id = EXCLUDED.owner_id,
            migration_version = EXCLUDED.migration_version,
            migration_checksum = EXCLUDED.migration_checksum,
            fencing_token = EXCLUDED.fencing_token,
            host = EXCLUDED.host,
            pid = EXCLUDED.pid,
            state = EXCLUDED.state,
            acquired_at = EXCLUDED.acquired_at,
            last_heartbeat_at = EXCLUDED.last_heartbeat_at
    """,
    "mysql": """
        INSERT INTO dbwarden_lock
            (namespace, execution_id, owner_id, migration_version, migration_checksum,
             fencing_token, host, pid, state, acquired_at, last_heartbeat_at)
        VALUES
            (:namespace, :execution_id, :owner_id, :migration_version, :migration_checksum,
             :fencing_token, :host, :pid, :state, :acquired_at, :last_heartbeat_at)
        ON DUPLICATE KEY UPDATE
            execution_id = VALUES(execution_id),
            owner_id = VALUES(owner_id),
            migration_version = VALUES(migration_version),
            migration_checksum = VALUES(migration_checksum),
            fencing_token = VALUES(fencing_token),
            host = VALUES(host),
            pid = VALUES(pid),
            state = VALUES(state),
            acquired_at = VALUES(acquired_at),
            last_heartbeat_at = VALUES(last_heartbeat_at)
    """,
    "clickhouse": """
        INSERT INTO dbwarden_lock
            (namespace, execution_id, owner_id, migration_version, migration_checksum,
             fencing_token, host, pid, state, acquired_at, last_heartbeat_at)
        VALUES
            (:namespace, :execution_id, :owner_id, :migration_version, :migration_checksum,
             :fencing_token, :host, :pid, :state, :acquired_at, :last_heartbeat_at)
    """,
}

_UPDATE_HEARTBEAT = {
    "sqlite": """
        UPDATE dbwarden_lock
        SET last_heartbeat_at = :now
        WHERE namespace = :namespace AND execution_id = :execution_id
    """,
    "postgresql": """
        UPDATE {schema}.dbwarden_lock
        SET last_heartbeat_at = :now
        WHERE namespace = :namespace AND execution_id = :execution_id
    """,
    "mysql": """
        UPDATE dbwarden_lock
        SET last_heartbeat_at = :now
        WHERE namespace = :namespace AND execution_id = :execution_id
    """,
    "clickhouse": """
        ALTER TABLE dbwarden_lock UPDATE
        SET last_heartbeat_at = :now
        WHERE namespace = :namespace AND execution_id = :execution_id
    """,
}

_UPDATE_STATE = {
    "sqlite": """
        UPDATE dbwarden_lock
        SET state = :state
        WHERE namespace = :namespace
    """,
    "postgresql": """
        UPDATE {schema}.dbwarden_lock
        SET state = :state
        WHERE namespace = :namespace
    """,
    "mysql": """
        UPDATE dbwarden_lock
        SET state = :state
        WHERE namespace = :namespace
    """,
    "clickhouse": """
        ALTER TABLE dbwarden_lock UPDATE
        SET state = :state
        WHERE namespace = :namespace
    """,
}

_READ_STATUS_ROW = {
    "sqlite": """
        SELECT namespace, execution_id, owner_id, migration_version, migration_checksum,
               fencing_token, host, pid, db_connection_id, state, acquired_at,
               last_heartbeat_at, expires_at
        FROM dbwarden_lock WHERE namespace = :namespace
    """,
    "postgresql": """
        SELECT namespace, execution_id, owner_id, migration_version, migration_checksum,
               fencing_token, host, pid, db_connection_id, state, acquired_at,
               last_heartbeat_at, expires_at
        FROM {schema}.dbwarden_lock WHERE namespace = :namespace
    """,
    "mysql": """
        SELECT namespace, execution_id, owner_id, migration_version, migration_checksum,
               fencing_token, host, pid, db_connection_id, state, acquired_at,
               last_heartbeat_at, expires_at
        FROM dbwarden_lock WHERE namespace = :namespace
    """,
    "clickhouse": """
        SELECT namespace, execution_id, owner_id, migration_version, migration_checksum,
               fencing_token, host, pid, db_connection_id, state, acquired_at,
               last_heartbeat_at, expires_at
        FROM dbwarden_lock FINAL WHERE namespace = :namespace
    """,
}


def _get_db_type(connection: Any) -> str:
    """Infer database type from the connection's dialect."""
    dialect_name = connection.dialect.name
    if dialect_name == "sqlite":
        return "sqlite"
    if dialect_name == "postgresql":
        return "postgresql"
    if dialect_name in ("mysql", "mariadb"):
        return "mysql"
    if dialect_name in ("clickhouse", "clickhousedb"):
        return "clickhouse"
    return "sqlite"


def _format_query(template: str, db_type: str, schema: str = "public") -> str:
    """Format a query template with schema substitution if needed."""
    if db_type == "postgresql":
        return template.format(schema=schema)
    return template


# --- Public API ---


def ensure_lock_table(connection: Any, db_type: str, schema: str = "public") -> None:
    """Create the v2 lock table if it doesn't exist.

    Also handles migration from v1 schema (adds missing columns).
    """
    ddl = _LOCK_TABLE_V2_DDL.get(db_type, _LOCK_TABLE_V2_DDL["sqlite"])
    formatted = _format_query(ddl, db_type, schema)
    connection.execute(text(formatted))

    # v1 → v2 migration: add missing columns if table exists with old schema
    _migrate_v1_to_v2(connection, db_type, schema)

    logger.debug("Lock table ensured (db_type=%s)", db_type)


def _migrate_v1_to_v2(connection: Any, db_type: str, schema: str = "public") -> None:
    """Detect v1 lock table schema and migrate to v2.

    v1 schema had: id, locked, acquired_at, owner_token
    v2 schema has: namespace (PK), execution_id, owner_id, state, etc.

    Since we can't add a PRIMARY KEY via ALTER TABLE in SQLite,
    we drop and recreate the table.
    """
    # Check if old v1 columns exist (locked, owner_token)
    try:
        if db_type == "sqlite":
            result = connection.execute(
                text("PRAGMA table_info(dbwarden_lock)")
            )
            columns = {row[1] for row in result.fetchall()}
        elif db_type == "postgresql":
            result = connection.execute(
                text(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_schema = :schema AND table_name = 'dbwarden_lock'"
                ),
                {"schema": schema},
            )
            columns = {row[0] for row in result.fetchall()}
        elif db_type == "mysql":
            result = connection.execute(
                text(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_name = 'dbwarden_lock'"
                ),
            )
            columns = {row[0] for row in result.fetchall()}
        else:
            return  # ClickHouse: MergeTree, no ALTER TABLE ADD COLUMN support in this path
    except Exception:
        return  # Table doesn't exist yet; create will handle it

    # If old 'locked' column exists but new 'namespace' doesn't, we need migration
    if "locked" in columns and "namespace" not in columns:
        logger.info("Migrating lock table from v1 to v2 schema")
        _recreate_lock_table_v2(connection, db_type, schema)
    elif "namespace" not in columns and "locked" not in columns:
        # Neither v1 nor v2; fresh table, DDL already created it
        pass


def _recreate_lock_table_v2(connection: Any, db_type: str, schema: str = "public") -> None:
    """Drop v1 lock table and recreate with v2 schema.

    This is necessary because SQLite doesn't support adding PRIMARY KEY
    via ALTER TABLE, and the v2 schema requires PRIMARY KEY (namespace).
    """
    table_name = f"{schema}.dbwarden_lock" if db_type == "postgresql" else "dbwarden_lock"

    # Save existing data if any
    existing_data = []
    try:
        result = connection.execute(text(f"SELECT * FROM {table_name}"))
        existing_data = result.fetchall()
    except Exception:
        pass

    # Drop old table
    try:
        connection.execute(text(f"DROP TABLE IF EXISTS {table_name}"))
    except Exception:
        pass

    # Create v2 table
    ddl = _LOCK_TABLE_V2_DDL.get(db_type, _LOCK_TABLE_V2_DDL["sqlite"])
    formatted = _format_query(ddl, db_type, schema)
    connection.execute(text(formatted))

    logger.info("Lock table recreated with v2 schema")


def upsert_status_row(
    connection: Any,
    *,
    namespace: str = "default",
    execution_id: str,
    owner_id: str,
    migration_version: str | None = None,
    migration_checksum: str | None = None,
    fencing_token: int = 0,
    state: str = "RUNNING",
    db_type: str | None = None,
    schema: str = "public",
) -> None:
    """Write or update the status row for a namespace."""
    if db_type is None:
        db_type = _get_db_type(connection)

    import platform
    params = {
        "namespace": namespace,
        "execution_id": execution_id,
        "owner_id": owner_id,
        "migration_version": migration_version,
        "migration_checksum": migration_checksum,
        "fencing_token": fencing_token,
        "host": socket.gethostname(),
        "pid": __import__("os").getpid(),
        "state": state,
        "acquired_at": _server_now(connection, db_type),
        "last_heartbeat_at": _server_now(connection, db_type),
    }

    template = _UPSERT_STATUS_ROW.get(db_type, _UPSERT_STATUS_ROW["sqlite"])
    formatted = _format_query(template, db_type, schema)
    connection.execute(text(formatted), params)


def update_heartbeat(
    connection: Any,
    *,
    namespace: str = "default",
    execution_id: str,
    db_type: str | None = None,
    schema: str = "public",
) -> None:
    """Update the heartbeat timestamp for a running migration."""
    if db_type is None:
        db_type = _get_db_type(connection)

    params = {
        "namespace": namespace,
        "execution_id": execution_id,
        "now": _server_now(connection, db_type),
    }

    template = _UPDATE_HEARTBEAT.get(db_type, _UPDATE_HEARTBEAT["sqlite"])
    formatted = _format_query(template, db_type, schema)
    connection.execute(text(formatted), params)


def update_state(
    connection: Any,
    *,
    namespace: str = "default",
    state: str,
    db_type: str | None = None,
    schema: str = "public",
) -> None:
    """Update the state field of the status row."""
    if db_type is None:
        db_type = _get_db_type(connection)

    params = {
        "namespace": namespace,
        "state": state,
    }

    template = _UPDATE_STATE.get(db_type, _UPDATE_STATE["sqlite"])
    formatted = _format_query(template, db_type, schema)
    connection.execute(text(formatted), params)


def read_status_row(
    connection: Any,
    *,
    namespace: str = "default",
    db_type: str | None = None,
    schema: str = "public",
) -> dict[str, Any] | None:
    """Read the status row for a namespace. Returns None if not found."""
    if db_type is None:
        db_type = _get_db_type(connection)

    template = _READ_STATUS_ROW.get(db_type, _READ_STATUS_ROW["sqlite"])
    formatted = _format_query(template, db_type, schema)
    result = connection.execute(
        text(formatted), {"namespace": namespace}
    ).mappings().first()
    if result is None:
        return None
    return dict(result)


def _server_now(connection: Any, db_type: str) -> str:
    """Get the current server timestamp as a string."""
    if db_type == "sqlite":
        result = connection.execute(text("SELECT datetime('now')")).scalar()
    elif db_type == "clickhouse":
        result = connection.execute(text("SELECT now()")).scalar()
    else:
        result = connection.execute(text("SELECT now()")).scalar()
    return str(result) if result else ""
