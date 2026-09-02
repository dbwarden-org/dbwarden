from typing import Any, Optional

import time

from sqlalchemy import Result, Row, text
from sqlalchemy.orm import Session

from dbwarden.connection.connection import get_db_connection
from dbwarden.connection.queries import QueryMethod, get_query
from dbwarden.engine.file_parser import DBWARDEN_AUTOCOMMIT_MARKER
from dbwarden.models import MigrationRecord


def _is_mysql_ddl(statement: str) -> bool:
    s = statement.strip().upper()
    return any(s.startswith(kw) for kw in ("ALTER", "CREATE", "DROP", "TRUNCATE", "RENAME", "LOCK"))


def _has_autocommit_marker(statement: str) -> bool:
    return statement.strip().startswith(DBWARDEN_AUTOCOMMIT_MARKER)


def _strip_autocommit_marker(statement: str) -> str:
    lines = statement.split("\n", 1)
    if len(lines) > 1:
        return lines[1].strip()
    return ""


def _exec_statement(connection, statement: str, *, logger, perf: bool) -> None:
    """Execute a statement, logging elapsed wall-clock time when ``perf`` is set."""
    if not perf:
        connection.execute(text(statement))
        return
    start = time.perf_counter()
    connection.execute(text(statement))
    elapsed_ms = (time.perf_counter() - start) * 1000.0
    logger.info(f"SQL ({elapsed_ms:.1f}ms): {statement.strip()[:120]}")


def _exec_autocommit_timed(
    statement: str, db_name: str | None, *, logger, perf: bool
) -> None:
    """Run an autocommit statement, logging elapsed wall-clock time when ``perf`` is set."""
    if not perf:
        _execute_autocommit(statement, db_name)
        return
    start = time.perf_counter()
    _execute_autocommit(statement, db_name)
    elapsed_ms = (time.perf_counter() - start) * 1000.0
    logger.info(f"SQL ({elapsed_ms:.1f}ms): {statement.strip()[:120]}")


def _execute_autocommit(sql_text: str, db_name: str | None = None) -> None:
    """Execute a single SQL statement with autocommit (outside any transaction).

    Respects the sandbox override: if a sandbox URL is active, the statement
    is executed against the sandbox instead of the real database.
    """
    from sqlalchemy import create_engine
    from dbwarden.connection.connection import _sandbox_url_var

    sandbox_url = _sandbox_url_var.get()
    if sandbox_url is not None:
        engine = create_engine(sandbox_url, isolation_level="AUTOCOMMIT")
        try:
            with engine.connect() as conn:
                conn.execute(text(sql_text))
        finally:
            engine.dispose()
        return

    from dbwarden.config import get_database

    config = get_database(db_name)
    engine = create_engine(config.sqlalchemy_url, isolation_level="AUTOCOMMIT")
    try:
        with engine.connect() as conn:
            if config.database_type == "postgresql" and config.pg_migration_lock_timeout is not None:
                conn.execute(text(f"SET SESSION lock_timeout = '{config.pg_migration_lock_timeout}ms'"))
            conn.execute(text(sql_text))
    finally:
        engine.dispose()


def _set_lock_timeout(connection, db_name: str | None = None) -> None:
    """Set lock_timeout for the current transaction (SET LOCAL)."""
    from dbwarden.config import get_database

    config = get_database(db_name)
    if config.database_type == "postgresql" and config.pg_migration_lock_timeout is not None:
        connection.execute(text(f"SET LOCAL lock_timeout = '{config.pg_migration_lock_timeout}ms'"))


def _record_upgrade(
    *,
    version: str | None,
    filename: str,
    migration_type: str,
    sql_statements: list[str],
    db_name: str | None,
    connection: Any | None = None,
) -> None:
    from dbwarden.engine.checksum import calculate_checksum
    from dbwarden.engine.file_parser import get_description_from_filename

    if connection is not None:
        connection.execute(
            text(get_query(QueryMethod.INSERT_VERSION, db_name)),
            parameters={
                "version": version,
                "description": get_description_from_filename(filename),
                "filename": filename,
                "migration_type": migration_type,
                "checksum": calculate_checksum(sql_statements),
            },
        )
    else:
        with get_db_connection(db_name) as conn:
            conn.execute(
                text(get_query(QueryMethod.INSERT_VERSION, db_name)),
                parameters={
                    "version": version,
                    "description": get_description_from_filename(filename),
                    "filename": filename,
                    "migration_type": migration_type,
                    "checksum": calculate_checksum(sql_statements),
                },
            )


def _record_rollback(
    *, version: str | None, db_name: str | None, connection: Any | None = None
) -> None:
    if connection is not None:
        connection.execute(
            text(get_query(QueryMethod.DELETE_VERSION, db_name)),
            parameters={"version": version},
        )
        optimize_sql = get_query(QueryMethod.OPTIMIZE_MIGRATIONS_TABLE, db_name)
        if optimize_sql:
            connection.execute(text(optimize_sql))
    else:
        with get_db_connection(db_name) as conn:
            conn.execute(
                text(get_query(QueryMethod.DELETE_VERSION, db_name)),
                parameters={"version": version},
            )
            optimize_sql = get_query(QueryMethod.OPTIMIZE_MIGRATIONS_TABLE, db_name)
            if optimize_sql:
                conn.execute(text(optimize_sql))


def run_migration(
    sql_statements: list[str],
    version: Optional[str],
    migration_operation: str,
    filename: str,
    migration_type: str = "versioned",
    db_name: str | None = None,
    perf: bool = False,
    connection: Any | None = None,
    namespace: str = "default",
    fencing_token: int = 0,
) -> None:
    """Execute SQL statements and record the migration.

    Statements prefixed with ``-- @dbwarden:autocommit`` are executed
    outside the main transaction (autocommit connection) to support
    PostgreSQL operations that require it (e.g., CREATE INDEX CONCURRENTLY,
    VALIDATE CONSTRAINT). All other statements run in a single transaction
    with savepoint-based rollback.

    Note: MySQL/MariaDB DDL statements (ALTER, CREATE, DROP, etc.)
    implicitly commit the current transaction and destroy any
    active savepoints. For MySQL/MariaDB, savepoints are disabled
    and DDL statements are executed individually (they auto-commit).

    Args:
        connection: Optional long-lived migration connection. When provided,
            statements execute on this connection instead of opening a new one.
            The caller is responsible for the connection lifecycle.
        namespace: Lock namespace for ClickHouse fencing (default: "default").
        fencing_token: Current fencing token for ClickHouse per-statement checks.
    """
    from dbwarden.logging import get_logger

    logger = get_logger(db_name=db_name)

    # Separate autocommit statements from transactional ones
    txn_statements: list[str] = []
    autocommit_statements: list[str] = []
    for stmt in sql_statements:
        if _has_autocommit_marker(stmt):
            autocommit_statements.append(_strip_autocommit_marker(stmt))
        else:
            txn_statements.append(stmt)

    def _run_on_connection(conn, is_external_conn=False):
        """Run migration statements on the given connection.

        Args:
            conn: The database connection.
            is_external_conn: If True, don't use savepoints (for long-lived
                connections where the caller manages the transaction).
        """
        is_mysql = conn.dialect.name in ("mysql", "mariadb")
        is_clickhouse = conn.dialect.name in ("clickhouse", "clickhousedb")
        _set_lock_timeout(conn, db_name)

        # CH-1: Per-statement fence check before DDL execution
        if is_clickhouse and fencing_token > 0:
            from dbwarden.lock.clickhouse import ClickHouseStrategy
            strategy = ClickHouseStrategy()
            if not strategy.fence_check(conn, namespace, fencing_token):
                from dbwarden.exceptions import LockError
                raise LockError(
                    "ClickHouse fencing token mismatch or lease expired. "
                    "Another worker may have acquired the lease."
                )

        # Connection loss handling (Sec 8.3.2): Map connection-loss errors
        # to immediate abort. A reconnected worker holds no lock and could
        # mutate unsafely, so we must NOT reconnect.
        try:
            if is_mysql:
                try:
                    for statement in txn_statements:
                        _exec_statement(conn, statement, logger=logger, perf=perf)

                except Exception:
                    raise

                # Complete autocommit work before recording migration state.
                for stmt in autocommit_statements:
                    _exec_autocommit_timed(stmt, db_name, logger=logger, perf=perf)
                if not autocommit_statements and migration_operation == "upgrade":
                    from dbwarden.engine.checksum import calculate_checksum
                    from dbwarden.engine.file_parser import get_description_from_filename

                    conn.execute(
                        text(get_query(QueryMethod.INSERT_VERSION, db_name)),
                        parameters={
                            "version": version,
                            "description": get_description_from_filename(filename),
                            "filename": filename,
                            "migration_type": migration_type,
                            "checksum": calculate_checksum(sql_statements),
                        },
                    )
                elif not autocommit_statements and migration_operation == "rollback":
                    conn.execute(
                        text(get_query(QueryMethod.DELETE_VERSION, db_name)),
                        parameters={"version": version},
                    )
                    optimize_sql = get_query(QueryMethod.OPTIMIZE_MIGRATIONS_TABLE, db_name)
                    if optimize_sql:
                        conn.execute(text(optimize_sql))

                if autocommit_statements and migration_operation == "upgrade":
                    _record_upgrade(
                        version=version,
                        filename=filename,
                        migration_type=migration_type,
                        sql_statements=sql_statements,
                        db_name=db_name,
                        connection=conn,
                    )
                elif autocommit_statements and migration_operation == "rollback":
                    _record_rollback(version=version, db_name=db_name, connection=conn)
                return

            # For external connections (long-lived), don't use savepoints
            # The caller manages the transaction lifecycle, but we commit after
            # each migration to persist DDL changes
            if is_external_conn:
                try:
                    for statement in txn_statements:
                        _exec_statement(conn, statement, logger=logger, perf=perf)

                    if not autocommit_statements and migration_operation == "upgrade":
                        from dbwarden.engine.checksum import calculate_checksum
                        from dbwarden.engine.file_parser import get_description_from_filename

                        conn.execute(
                            text(get_query(QueryMethod.INSERT_VERSION, db_name)),
                            parameters={
                                "version": version,
                                "description": get_description_from_filename(filename),
                                "filename": filename,
                                "migration_type": migration_type,
                                "checksum": calculate_checksum(sql_statements),
                            },
                        )
                    elif not autocommit_statements and migration_operation == "rollback":
                        conn.execute(
                            text(get_query(QueryMethod.DELETE_VERSION, db_name)),
                            parameters={"version": version},
                        )
                        optimize_sql = get_query(QueryMethod.OPTIMIZE_MIGRATIONS_TABLE, db_name)
                        if optimize_sql:
                            conn.execute(text(optimize_sql))

                    # Commit to persist DDL changes (especially important for SQLite)
                    conn.commit()
                except Exception:
                    # Rollback on error
                    try:
                        conn.rollback()
                    except Exception:
                        pass
                    raise
                return

            # For per-operation connections, use savepoints for rollback safety
            try:
                savepoint = conn.begin_nested()
                has_savepoint = True
            except Exception:
                has_savepoint = False
                if txn_statements and len(txn_statements) > 1:
                    get_logger(db_name=db_name).warning(
                        "Database does not support savepoints. "
                        "Multi-statement migrations may leave partial changes on failure."
                    )
            try:
                for statement in txn_statements:
                    _exec_statement(conn, statement, logger=logger, perf=perf)

                if not autocommit_statements and migration_operation == "upgrade":
                    from dbwarden.engine.checksum import calculate_checksum
                    from dbwarden.engine.file_parser import get_description_from_filename

                    conn.execute(
                        text(get_query(QueryMethod.INSERT_VERSION, db_name)),
                        parameters={
                            "version": version,
                            "description": get_description_from_filename(filename),
                            "filename": filename,
                            "migration_type": migration_type,
                            "checksum": calculate_checksum(sql_statements),
                        },
                    )
                elif not autocommit_statements and migration_operation == "rollback":
                    conn.execute(
                        text(get_query(QueryMethod.DELETE_VERSION, db_name)),
                        parameters={"version": version},
                    )
                    optimize_sql = get_query(QueryMethod.OPTIMIZE_MIGRATIONS_TABLE, db_name)
                    if optimize_sql:
                        conn.execute(text(optimize_sql))

                if has_savepoint:
                    savepoint.commit()
            except Exception:
                if has_savepoint:
                    savepoint.rollback()
                raise

        except Exception as exc:
            # Connection loss handling (Sec 8.3.2): Map connection-loss errors
            # to immediate abort. A reconnected worker holds no lock and could
            # mutate unsafely, so we must NOT reconnect.
            import sqlalchemy.exc as sa_exc
            if isinstance(exc, (sa_exc.DBAPIError, sa_exc.OperationalError, sa_exc.InterfaceError)):
                from dbwarden.exceptions import LockError
                raise LockError(
                    f"Connection lost during migration: {exc}. "
                    "Aborting for safety. A reconnected worker would hold no lock."
                ) from exc
            raise

    if connection is not None:
        # Use the provided long-lived connection (no savepoints)
        _run_on_connection(connection, is_external_conn=True)
    else:
        # Open a new connection per operation (legacy behavior)
        with get_db_connection(db_name) as conn:
            _run_on_connection(conn, is_external_conn=False)

    # Run autocommit statements after the transactional block commits
    for stmt in autocommit_statements:
        _exec_autocommit_timed(stmt, db_name, logger=logger, perf=perf)
    if autocommit_statements and migration_operation == "upgrade":
        _record_upgrade(
            version=version,
            filename=filename,
            migration_type=migration_type,
            sql_statements=sql_statements,
            db_name=db_name,
            connection=connection,
        )
    elif autocommit_statements and migration_operation == "rollback":
        _record_rollback(version=version, db_name=db_name, connection=connection)


def fetch_latest_versioned_migration(
    db_name: str | None = None,
) -> Optional[MigrationRecord]:
    """Get the most recently applied versioned migration."""
    if not migrations_table_exists(db_name):
        return None

    with get_db_connection(db_name) as connection:
        result = connection.execute(
            text(get_query(QueryMethod.GET_LATEST_VERSION, db_name))
        )
        latest_migration = result.first()

    if not latest_migration:
        return None
    return MigrationRecord(
        order_executed=0,
        version=latest_migration.version,
        description=latest_migration.description,
        filename=latest_migration.filename,
        migration_type=latest_migration.migration_type,
        applied_at=latest_migration.applied_at,
        checksum=latest_migration.checksum,
    )


def create_migrations_table_if_not_exists(db_name: str | None = None) -> None:
    """Create the migrations table if it doesn't exist."""
    with get_db_connection(db_name) as connection:
        connection.execute(
            text(get_query(QueryMethod.CREATE_MIGRATIONS_TABLE, db_name))
        )


def migrations_table_exists(db_name: str | None = None) -> bool:
    """Check if migrations table exists."""
    with get_db_connection(db_name) as connection:
        result = connection.execute(
            text(get_query(QueryMethod.CHECK_IF_MIGRATIONS_TABLE_EXISTS, db_name))
        )
        return result.scalar_one_or_none() is not None


def get_migration_records(db_name: str | None = None) -> list[MigrationRecord]:
    """Get all migration records."""
    if not migrations_table_exists(db_name):
        return []

    with get_db_connection(db_name) as connection:
        results = connection.execute(
            text(get_query(QueryMethod.GET_ALL_MIGRATIONS, db_name))
        )
        return [
            MigrationRecord(
                order_executed=i,
                version=row.version,
                description=row.description,
                filename=row.filename,
                migration_type=row.migration_type,
                applied_at=row.applied_at,
                checksum=row.checksum,
            )
            for i, row in enumerate(results.fetchall())
        ]


def get_migrated_versions(db_name: str | None = None) -> list[str]:
    """Get all applied migration versions."""
    if not migrations_table_exists(db_name):
        return []

    with get_db_connection(db_name) as connection:
        results = connection.execute(
            text(get_query(QueryMethod.GET_MIGRATED_VERSIONS, db_name))
        )
        return [row.version for row in results.fetchall()]


def get_applied_checksums(db_name: str | None = None) -> set[str]:
    """Get all applied migration checksums."""
    if not migrations_table_exists(db_name):
        return set()

    with get_db_connection(db_name) as connection:
        results = connection.execute(
            text(get_query(QueryMethod.GET_DISTINCT_CHECKSUMS, db_name))
        )
        return {row.checksum for row in results.fetchall()}


def get_latest_versions(
    db_name: str | None = None,
    limit: int | None = None,
    starting_version: str | None = None,
) -> list[str]:
    """Get recent migration versions."""
    if limit:
        with get_db_connection(db_name) as connection:
            result = connection.execute(
                text(get_query(QueryMethod.GET_LATEST_VERSIONS_LIMIT, db_name)),
                parameters={"limit": limit},
            )
            return [row.version for row in result.fetchall()]
    elif starting_version:
        with get_db_connection(db_name) as connection:
            result = connection.execute(
                text(get_query(QueryMethod.GET_LATEST_VERSIONS_FROM, db_name)),
                parameters={"starting_version": starting_version},
            )
            return [row.version for row in result.fetchall()]
    else:
        return []


def get_existing_runs_on_change_filenames_to_checksums(
    db_name: str | None = None,
) -> dict[str, str]:
    """
    Get filename to checksum mapping for runs-on-change migrations.

    Returns:
        dict[str, str]: Dictionary mapping filenames to their stored checksum values.
    """
    if not migrations_table_exists(db_name):
        return {}

    with get_db_connection(db_name) as connection:
        results = connection.execute(
            text(get_query(QueryMethod.GET_RUNS_ON_CHANGE_CHECKSUMS, db_name))
        )
        return {row.filename: row.checksum for row in results.fetchall()}


def get_existing_runs_always_filenames(db_name: str | None = None) -> set[str]:
    """
    Get filenames of all runs-always migrations in the database.

    Returns:
        set[str]: Set of runs-always migration filenames.
    """
    if not migrations_table_exists(db_name):
        return set()

    with get_db_connection(db_name) as connection:
        results = connection.execute(
            text(get_query(QueryMethod.GET_RUNS_ALWAYS_FILENAMES, db_name))
        )
        return {row.filename for row in results.fetchall()}


def run_repeatable_migration(
    sql_statements: list[str],
    filename: str,
    migration_type: str,
    db_name: str | None = None,
    perf: bool = False,
    connection: Any | None = None,
) -> None:
    """
    Execute and update an existing repeatable migration record.

    Runs the SQL statements and updates the existing migration record
    with a new checksum and applied_at timestamp.

    Statements prefixed with ``-- @dbwarden:autocommit`` are executed
    outside the main transaction (autocommit connection).

    Args:
        sql_statements: List of SQL statements to execute.
        filename: The migration filename.
        migration_type: Type of repeatable migration (runs_always or runs_on_change).
        db_name: Database name.
        connection: Optional long-lived migration connection.
    """
    from dbwarden.engine.checksum import calculate_checksum
    from dbwarden.engine.file_parser import get_description_from_filename

    checksum = calculate_checksum(sql_statements)
    description = get_description_from_filename(filename)

    txn_statements: list[str] = []
    autocommit_statements: list[str] = []
    for stmt in sql_statements:
        if _has_autocommit_marker(stmt):
            autocommit_statements.append(_strip_autocommit_marker(stmt))
        else:
            txn_statements.append(stmt)

    from dbwarden.logging import get_logger

    logger = get_logger(db_name=db_name)

    def _run_on_connection(conn, is_external_conn=False):
        """Run repeatable migration on the given connection.

        Args:
            conn: The database connection.
            is_external_conn: If True, don't use savepoints (for long-lived
                connections where the caller manages the transaction).
        """
        is_mysql = conn.dialect.name in ("mysql", "mariadb")
        _set_lock_timeout(conn, db_name)

        if is_mysql:
            try:
                for statement in txn_statements:
                    _exec_statement(conn, statement, logger=logger, perf=perf)

                if not autocommit_statements:
                    conn.execute(
                        text(get_query(QueryMethod.UPSERT_REPEATABLE_MIGRATION, db_name)),
                        parameters={
                            "description": description,
                            "filename": filename,
                            "migration_type": migration_type,
                            "checksum": checksum,
                        },
                    )
            except Exception:
                raise

            for stmt in autocommit_statements:
                _exec_autocommit_timed(stmt, db_name, logger=logger, perf=perf)
            if autocommit_statements:
                if connection is not None:
                    conn.execute(
                        text(get_query(QueryMethod.UPSERT_REPEATABLE_MIGRATION, db_name)),
                        parameters={
                            "description": description,
                            "filename": filename,
                            "migration_type": migration_type,
                            "checksum": checksum,
                        },
                    )
                else:
                    with get_db_connection(db_name) as record_connection:
                        record_connection.execute(
                            text(get_query(QueryMethod.UPSERT_REPEATABLE_MIGRATION, db_name)),
                            parameters={
                                "description": description,
                                "filename": filename,
                                "migration_type": migration_type,
                                "checksum": checksum,
                            },
                        )
            return

        # For external connections (long-lived), don't use savepoints
        # Commit after each migration to persist changes
        if is_external_conn:
            try:
                for statement in txn_statements:
                    _exec_statement(conn, statement, logger=logger, perf=perf)

                if not autocommit_statements:
                    conn.execute(
                        text(get_query(QueryMethod.UPSERT_REPEATABLE_MIGRATION, db_name)),
                        parameters={
                            "description": description,
                            "filename": filename,
                            "migration_type": migration_type,
                            "checksum": checksum,
                        },
                    )

                # Commit to persist changes
                conn.commit()
            except Exception:
                try:
                    conn.rollback()
                except Exception:
                    pass
                raise
            return

        # For per-operation connections, use savepoints for rollback safety
        savepoint = conn.begin_nested()
        try:
            for statement in txn_statements:
                _exec_statement(conn, statement, logger=logger, perf=perf)

            if not autocommit_statements:
                conn.execute(
                    text(get_query(QueryMethod.UPSERT_REPEATABLE_MIGRATION, db_name)),
                    parameters={
                        "description": description,
                        "filename": filename,
                        "migration_type": migration_type,
                        "checksum": checksum,
                    },
                )

            savepoint.commit()
        except Exception:
            savepoint.rollback()
            raise

    if connection is not None:
        _run_on_connection(connection, is_external_conn=True)
    else:
        with get_db_connection(db_name) as conn:
            _run_on_connection(conn, is_external_conn=False)

    for stmt in autocommit_statements:
        _exec_autocommit_timed(stmt, db_name, logger=logger, perf=perf)
    if autocommit_statements:
        if connection is not None:
            connection.execute(
                text(get_query(QueryMethod.UPSERT_REPEATABLE_MIGRATION, db_name)),
                parameters={
                    "description": description,
                    "filename": filename,
                    "migration_type": migration_type,
                    "checksum": checksum,
                },
            )
        else:
            with get_db_connection(db_name) as record_connection:
                record_connection.execute(
                    text(get_query(QueryMethod.UPSERT_REPEATABLE_MIGRATION, db_name)),
                    parameters={
                        "description": description,
                        "filename": filename,
                        "migration_type": migration_type,
                        "checksum": checksum,
                    },
                )
