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
        ) ENGINE = ReplacingMergeTree(fencing_token)
        ORDER BY namespace
    """,
}

# --- Status row CRUD ---

_UPSERT_STATUS_ROW = {
    "sqlite": """
        INSERT INTO dbwarden_lock
            (namespace, execution_id, owner_id, migration_version, migration_checksum,
             fencing_token, host, pid, db_connection_id, state, acquired_at, last_heartbeat_at)
        VALUES
            (:namespace, :execution_id, :owner_id, :migration_version, :migration_checksum,
             :fencing_token, :host, :pid, :db_connection_id, :state, :acquired_at, :last_heartbeat_at)
        ON CONFLICT (namespace) DO UPDATE SET
            execution_id = excluded.execution_id,
            owner_id = excluded.owner_id,
            migration_version = excluded.migration_version,
            migration_checksum = excluded.migration_checksum,
            fencing_token = excluded.fencing_token,
            host = excluded.host,
            pid = excluded.pid,
            db_connection_id = excluded.db_connection_id,
            state = excluded.state,
            acquired_at = excluded.acquired_at,
            last_heartbeat_at = excluded.last_heartbeat_at
    """,
    "postgresql": """
        INSERT INTO {schema}.dbwarden_lock
            (namespace, execution_id, owner_id, migration_version, migration_checksum,
             fencing_token, host, pid, db_connection_id, state, acquired_at, last_heartbeat_at)
        VALUES
            (:namespace, :execution_id, :owner_id, :migration_version, :migration_checksum,
             :fencing_token, :host, :pid, :db_connection_id, :state, :acquired_at, :last_heartbeat_at)
        ON CONFLICT (namespace) DO UPDATE SET
            execution_id = EXCLUDED.execution_id,
            owner_id = EXCLUDED.owner_id,
            migration_version = EXCLUDED.migration_version,
            migration_checksum = EXCLUDED.migration_checksum,
            fencing_token = EXCLUDED.fencing_token,
            host = EXCLUDED.host,
            pid = EXCLUDED.pid,
            db_connection_id = EXCLUDED.db_connection_id,
            state = EXCLUDED.state,
            acquired_at = EXCLUDED.acquired_at,
            last_heartbeat_at = EXCLUDED.last_heartbeat_at
    """,
    "mysql": """
        INSERT INTO dbwarden_lock
            (namespace, execution_id, owner_id, migration_version, migration_checksum,
             fencing_token, host, pid, db_connection_id, state, acquired_at, last_heartbeat_at)
        VALUES
            (:namespace, :execution_id, :owner_id, :migration_version, :migration_checksum,
             :fencing_token, :host, :pid, :db_connection_id, :state, :acquired_at, :last_heartbeat_at)
        ON DUPLICATE KEY UPDATE
            execution_id = VALUES(execution_id),
            owner_id = VALUES(owner_id),
            migration_version = VALUES(migration_version),
            migration_checksum = VALUES(migration_checksum),
            fencing_token = VALUES(fencing_token),
            host = VALUES(host),
            pid = VALUES(pid),
            db_connection_id = VALUES(db_connection_id),
            state = VALUES(state),
            acquired_at = VALUES(acquired_at),
            last_heartbeat_at = VALUES(last_heartbeat_at)
    """,
    "clickhouse": """
        INSERT INTO dbwarden_lock
            (namespace, execution_id, owner_id, migration_version, migration_checksum,
             fencing_token, host, pid, db_connection_id, state, acquired_at, last_heartbeat_at)
        SELECT
            :namespace, :execution_id, :owner_id, :migration_version, :migration_checksum,
            ifNull(max(fencing_token), 0) + 1, :host, :pid, :db_connection_id, 'RUNNING',
            :acquired_at, :last_heartbeat_at
        FROM dbwarden_lock
        WHERE namespace = :namespace
          AND (expires_at IS NULL OR expires_at <= now())
        LIMIT 1
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
        SET last_heartbeat_at = :now, expires_at = :expires_at
        WHERE namespace = :namespace AND execution_id = :execution_id
          AND owner_id = :owner_id AND fencing_token = :fencing_token
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

# Engine-agnostic read for pre-existing dbwarden_lock tables that were
# created as plain MergeTree (no deduplication, possibly duplicate rows per
# namespace). The authoritative row is the one with the highest fencing
# token, so argMax reconstructs it without requiring FINAL (which is only
# valid on ReplacingMergeTree-family engines).
_READ_STATUS_ROW_CH_ARGMAX = """
    SELECT namespace, execution_id, owner_id, migration_version, migration_checksum,
           max_token AS fencing_token, host, pid, db_connection_id, state,
           acquired_at, last_heartbeat_at, expires_at
    FROM (
        SELECT namespace,
               argMax(execution_id, fencing_token)       AS execution_id,
               argMax(owner_id, fencing_token)           AS owner_id,
               argMax(migration_version, fencing_token)  AS migration_version,
               argMax(migration_checksum, fencing_token) AS migration_checksum,
               max(fencing_token)                        AS max_token,
               argMax(host, fencing_token)               AS host,
               argMax(pid, fencing_token)                AS pid,
               argMax(db_connection_id, fencing_token)   AS db_connection_id,
               argMax(state, fencing_token)              AS state,
               argMax(acquired_at, fencing_token)        AS acquired_at,
               argMax(last_heartbeat_at, fencing_token)  AS last_heartbeat_at,
               argMax(expires_at, fencing_token)         AS expires_at
        FROM dbwarden_lock
        WHERE namespace = :namespace
        GROUP BY namespace
    )
"""

_CH_REPLACING_ENGINE = "ReplacingMergeTree(fencing_token)"

# MariaDB shares MySQL's DDL and syntax; alias it so a config declaring
# ``database_type = "mariadb"`` does not fall back to the SQLite templates.
for _queries in (
    _LOCK_TABLE_V2_DDL,
    _UPSERT_STATUS_ROW,
    _UPDATE_HEARTBEAT,
    _UPDATE_STATE,
    _READ_STATUS_ROW,
):
    _queries.setdefault("mariadb", _queries["mysql"])


def _get_ch_lock_engine(connection: Any) -> str | None:
    """Return the engine of dbwarden_lock in the current database.

    Returns None if the engine cannot be determined (e.g. the table does
    not exist or system.tables is unreachable). The result is cached on
    the connection object; _set_ch_lock_engine_cache() updates the cache
    after DDL that changes the engine.
    """
    cached = getattr(connection, "_dbwarden_lock_engine", None)
    if cached is not None:
        return cached
    engine = None
    try:
        engine = connection.execute(
            text(
                "SELECT engine FROM system.tables "
                "WHERE database = currentDatabase() AND name = 'dbwarden_lock'"
            )
        ).scalar()
    except Exception as exc:
        logger.debug("Could not detect dbwarden_lock engine: %s", exc)
        engine = None
    _set_ch_lock_engine_cache(connection, engine)
    return engine


def _set_ch_lock_engine_cache(connection: Any, engine: str | None) -> None:
    try:
        connection._dbwarden_lock_engine = engine
    except Exception as exc:
        # Unusual connection objects: detection just runs uncached
        logger.debug("Could not cache dbwarden_lock engine: %s", exc)


def _migrate_ch_lock_engine(connection: Any) -> bool:
    """Migrate a plain-MergeTree dbwarden_lock to ReplacingMergeTree.

    Lock tables created by older versions use plain MergeTree, which keeps
    every inserted row (duplicates per namespace) and does not support
    SELECT ... FINAL. The migration copies the authoritative row per
    namespace (highest fencing_token) into a ReplacingMergeTree table and
    swaps it in with an atomic RENAME, preserving existing lease state.

    Returns True if the table is ReplacingMergeTree after this call.
    """
    engine = _get_ch_lock_engine(connection)
    if engine is None:
        return False  # Nothing to migrate (table absent / undetectable)
    if "ReplacingMergeTree" in engine:
        return True

    logger.info(
        "Migrating dbwarden_lock from %s to %s", engine, _CH_REPLACING_ENGINE
    )
    new_ddl = _LOCK_TABLE_V2_DDL["clickhouse"].replace(
        "dbwarden_lock", "dbwarden_lock_migration", 1
    )
    connection.execute(text(new_ddl))
    # Deduplicate to the authoritative row per namespace: the row with
    # the highest fencing_token wins.
    connection.execute(
        text(
            """
            INSERT INTO dbwarden_lock_migration
                (namespace, execution_id, owner_id, migration_version,
                 migration_checksum, fencing_token, host, pid, db_connection_id,
                 state, acquired_at, last_heartbeat_at, expires_at)
            SELECT namespace,
                   argMax(execution_id, fencing_token),
                   argMax(owner_id, fencing_token),
                   argMax(migration_version, fencing_token),
                   argMax(migration_checksum, fencing_token),
                   max(fencing_token),
                   argMax(host, fencing_token),
                   argMax(pid, fencing_token),
                   argMax(db_connection_id, fencing_token),
                   argMax(state, fencing_token),
                   argMax(acquired_at, fencing_token),
                   argMax(last_heartbeat_at, fencing_token),
                   argMax(expires_at, fencing_token)
            FROM dbwarden_lock
            GROUP BY namespace
            """
        )
    )
    connection.execute(
        text(
            "RENAME TABLE dbwarden_lock TO dbwarden_lock_legacy, "
            "dbwarden_lock_migration TO dbwarden_lock"
        )
    )
    connection.execute(text("DROP TABLE dbwarden_lock_legacy"))
    _set_ch_lock_engine_cache(connection, _CH_REPLACING_ENGINE)
    logger.info("dbwarden_lock migrated to %s", _CH_REPLACING_ENGINE)
    return True


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

    v1 lock tables are no longer supported. If a v1 table exists,
    it will be dropped and recreated with the v2 schema.
    """
    ddl = _LOCK_TABLE_V2_DDL.get(db_type, _LOCK_TABLE_V2_DDL["sqlite"])
    formatted = _format_query(ddl, db_type, schema)
    connection.execute(text(formatted))

    if db_type == "clickhouse":
        # New tables are created as ReplacingMergeTree(fencing_token).
        # Tables created by older versions may still be plain MergeTree;
        # migrate them in place (atomic RENAME swap). If the migration
        # fails, reads fall back to argMax semantics, so locking stays
        # correct and the migration is retried on the next ensure call.
        try:
            _migrate_ch_lock_engine(connection)
        except Exception as exc:
            logger.warning(
                "Could not migrate dbwarden_lock to %s: %s",
                _CH_REPLACING_ENGINE,
                exc,
            )

    # Ensure v2 schema (drops v1 table if it exists)
    _ensure_v2_schema(connection, db_type, schema)

    logger.debug("Lock table ensured (db_type=%s)", db_type)


def _ensure_v2_schema(connection: Any, db_type: str, schema: str = "public") -> None:
    """Ensure the lock table has v2 schema.

    If a v1 table exists (with 'locked' column), drop and recreate with v2 schema.
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
        elif db_type in ("mysql", "mariadb"):
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

    # If old 'locked' column exists but new 'namespace' doesn't, drop and recreate
    if "locked" in columns and "namespace" not in columns:
        logger.info("Dropping v1 lock table and recreating with v2 schema")
        _recreate_lock_table_v2(connection, db_type, schema)


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
    db_connection_id: str | None = None,
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
        "db_connection_id": db_connection_id,
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
    owner_id: str = "",
    fencing_token: int = 0,
    ttl_seconds: int = 45,
    db_type: str | None = None,
    schema: str = "public",
) -> None:
    """Update the heartbeat timestamp for a running migration."""
    if db_type is None:
        db_type = _get_db_type(connection)

    from datetime import datetime, timedelta
    now_str = _server_now(connection, db_type)
    now_dt = datetime.fromisoformat(now_str.replace("Z", "+00:00"))
    expires_dt = now_dt + timedelta(seconds=ttl_seconds)
    expires_str = str(expires_dt)

    params = {
        "namespace": namespace,
        "execution_id": execution_id,
        "owner_id": owner_id,
        "fencing_token": fencing_token,
        "now": now_str,
        "expires_at": expires_str,
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

    if db_type == "clickhouse":
        # FINAL is only valid on ReplacingMergeTree-family engines. New
        # tables use ReplacingMergeTree(fencing_token); pre-existing plain
        # MergeTree tables are read with argMax-by-fencing_token instead,
        # which reconstructs the same authoritative row.
        engine = _get_ch_lock_engine(connection)
        if engine is not None and "ReplacingMergeTree" in engine:
            template = _READ_STATUS_ROW["clickhouse"]
        else:
            template = _READ_STATUS_ROW_CH_ARGMAX
    else:
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
