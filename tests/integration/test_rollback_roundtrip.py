"""Live rollback round-trip harness for backend statement families.

These tests are intentionally integration-only. They prove the rollback
contract against real catalogs: snapshot before, apply forward SQL, apply
rollback SQL, snapshot after, then compare canonical state.

Usage::

    pytest tests/integration/test_rollback_roundtrip.py --pg-integration --ch-integration
"""

from __future__ import annotations

import os
from collections.abc import Callable
from typing import Any

import pytest


def _requires_option(pytestconfig: pytest.Config, option: str) -> None:
    if not pytestconfig.getoption(option):
        pytest.skip(f"requires {option}")


def _requires_role_handler() -> None:
    """`alter_role` is not core: it comes from the dbwarden-pgsql-rbac plugin.

    Core's suite stays hermetic - it does not call `load_plugins()`, because
    registering plugin handlers globally would change what every other diff in
    the session emits. So this skips unless a handler is already registered,
    and the plugin-loaded path is covered by the harness's plugin_integration
    suite.
    """
    from dbwarden.plugin import ObjectPluginRegistry

    for registration in ObjectPluginRegistry.handlers().values():
        if "alter_role" in getattr(registration.handler, "op_types", ()):
            return
    pytest.skip(
        "alter_role is provided by the dbwarden-pgsql-rbac plugin; "
        "no registered handler for it in this environment"
    )


def _canonical_pg_column_defaults(pg_url: str, table_name: str) -> dict[str, Any]:
    import sqlalchemy as sa

    engine = sa.create_engine(pg_url)
    with engine.connect() as conn:
        rows = conn.execute(sa.text("""
            SELECT column_name, data_type, is_nullable, column_default
            FROM information_schema.columns
            WHERE table_schema = 'public' AND table_name = :table_name
            ORDER BY ordinal_position
        """), {"table_name": table_name}).mappings().all()
    return {row["column_name"]: dict(row) for row in rows}


def _canonical_pg_column_statistics(pg_url: str, table_name: str, column_name: str) -> int | None:
    import sqlalchemy as sa

    engine = sa.create_engine(pg_url)
    with engine.connect() as conn:
        return conn.execute(sa.text("""
            SELECT a.attstattarget
            FROM pg_attribute a
            JOIN pg_class c ON c.oid = a.attrelid
            JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE n.nspname = 'public'
              AND c.relname = :table_name
              AND a.attname = :column_name
        """), {"table_name": table_name, "column_name": column_name}).scalar_one_or_none()


def _canonical_pg_role(pg_url: str, role_name: str) -> dict[str, Any] | None:
    import sqlalchemy as sa

    engine = sa.create_engine(pg_url)
    with engine.connect() as conn:
        row = conn.execute(sa.text("""
            SELECT rolname, rolcanlogin, rolcreatedb, rolcreaterole, rolinherit, rolconnlimit
            FROM pg_roles
            WHERE rolname = :role_name
        """), {"role_name": role_name}).mappings().one_or_none()
    return dict(row) if row else None


def _canonical_ch_columns(client: Any, table_name: str, database: str = "default") -> dict[str, Any]:
    rows = client.query(
        """
        SELECT name, type, default_kind, default_expression
        FROM system.columns
        WHERE database = %(database)s AND table = %(table)s
        ORDER BY position
        """,
        parameters={"database": database, "table": table_name},
    ).result_rows
    return {
        name: {
            "type": typ,
            "default_kind": default_kind or None,
            "default_expression": default_expr or None,
        }
        for name, typ, default_kind, default_expr in rows
    }


def _assert_round_trip(
    snapshot: Callable[[], Any],
    apply_sql: Callable[[str], None],
    upgrade_sql: list[str],
    rollback_sql: list[str],
) -> None:
    before = snapshot()
    for sql in upgrade_sql:
        apply_sql(sql)
    after_upgrade = snapshot()
    assert after_upgrade != before
    for sql in rollback_sql:
        apply_sql(sql)
    after_rollback = snapshot()
    assert after_rollback == before


@pytest.fixture(scope="module")
def pg_url(pytestconfig: pytest.Config) -> str:
    _requires_option(pytestconfig, "--pg-integration")

    host = os.environ.get("PG_HOST")
    port = os.environ.get("PG_PORT")
    if host and port:
        user = os.environ.get("PG_USER", "postgres")
        password = os.environ.get("PG_PASSWORD", "postgres")
        database = os.environ.get("PG_DATABASE", "dbwarden_test")
        # A generator fixture has to yield on every path: returning here left
        # the fixture with no value whenever a server was supplied by
        # environment variables, which is how CI runs these.
        yield f"postgresql://{user}:{password}@{host}:{port}/{database}"
        return

    pytest.importorskip("testcontainers.postgres")
    from testcontainers.postgres import PostgresContainer

    with PostgresContainer("postgres:13-alpine") as pg:
        yield pg.get_connection_url().replace("+psycopg2", "")


@pytest.fixture(scope="module")
def ch_client(pytestconfig: pytest.Config):
    _requires_option(pytestconfig, "--ch-integration")
    pytest.importorskip("testcontainers.clickhouse")
    pytest.importorskip("clickhouse_connect")
    import clickhouse_connect

    host = os.environ.get("CLICKHOUSE_HOST")
    port = os.environ.get("CLICKHOUSE_PORT")
    if host and port:
        return clickhouse_connect.get_client(
            host=host,
            port=int(port),
            username=os.environ.get("CLICKHOUSE_USERNAME", "default"),
            password=os.environ.get("CLICKHOUSE_PASSWORD", ""),
        )

    from testcontainers.clickhouse import ClickHouseContainer

    with ClickHouseContainer(image=pytestconfig.getoption("--ch-image")) as ch:
        yield clickhouse_connect.get_client(
            host=ch.get_container_host_ip(),
            port=ch.get_exposed_port(8123),
            username=ch.username,
            password=ch.password,
        )


@pytest.mark.integration
def test_pg_column_default_round_trips(pg_url: str) -> None:
    import sqlalchemy as sa
    from dbwarden.engine.snapshot import snapshot_diff_to_sql

    engine = sa.create_engine(pg_url)
    table_name = "dbw_rt_default"
    with engine.begin() as conn:
        conn.execute(sa.text(f"DROP TABLE IF EXISTS {table_name}"))
        conn.execute(sa.text(f"CREATE TABLE {table_name} (flag boolean DEFAULT false)"))

    ops = [{
        "type": "alter_column_default",
        "table": table_name,
        "column": "flag",
        "default": "TRUE",
        "__rollback_attrs": {
            "table": table_name,
            "column": "flag",
            "default": "FALSE",
        },
    }]
    upgrade_sql, rollback_sql, _ = snapshot_diff_to_sql(ops, [], db_name=None)

    def apply_sql(sql: str) -> None:
        with engine.begin() as conn:
            conn.execute(sa.text(sql))

    _assert_round_trip(
        lambda: _canonical_pg_column_defaults(pg_url, table_name),
        apply_sql,
        [upgrade_sql],
        [rollback_sql],
    )


@pytest.mark.integration
def test_pg_column_statistics_round_trips(pg_url: str) -> None:
    import sqlalchemy as sa
    from dbwarden.engine.snapshot import snapshot_diff_to_sql

    engine = sa.create_engine(pg_url)
    table_name = "dbw_rt_stats"
    with engine.begin() as conn:
        conn.execute(sa.text(f"DROP TABLE IF EXISTS {table_name}"))
        conn.execute(sa.text(f"CREATE TABLE {table_name} (payload text)"))
        conn.execute(sa.text(f"ALTER TABLE {table_name} ALTER COLUMN payload SET STATISTICS 100"))

    ops = [{
        "type": "alter_column_statistics",
        "table": table_name,
        "column": "payload",
        "statistics": 1000,
        "__rollback_attrs": {
            "table": table_name,
            "column": "payload",
            "statistics": 100,
        },
    }]
    upgrade_sql, rollback_sql, _ = snapshot_diff_to_sql(ops, [], db_name=None)

    def apply_sql(sql: str) -> None:
        with engine.begin() as conn:
            conn.execute(sa.text(sql))

    _assert_round_trip(
        lambda: _canonical_pg_column_statistics(pg_url, table_name, "payload"),
        apply_sql,
        [upgrade_sql],
        [rollback_sql],
    )


@pytest.mark.integration
def test_pg_role_alter_round_trips(pg_url: str) -> None:
    import sqlalchemy as sa
    from dbwarden.engine.snapshot import snapshot_diff_to_sql

    _requires_role_handler()

    engine = sa.create_engine(pg_url)
    role_name = "dbw_rt_role"
    with engine.begin() as conn:
        conn.execute(sa.text(f"DROP ROLE IF EXISTS {role_name}"))
        conn.execute(sa.text(f"CREATE ROLE {role_name} LOGIN NOCREATEDB CONNECTION LIMIT 5"))

    ops = [{
        "type": "alter_role",
        "role_name": role_name,
        "role_info": {"login": False, "createdb": True, "connlimit": 20},
        "__rollback_attrs": {
            "role_name": role_name,
            "role_info": {"login": True, "createdb": False, "connlimit": 5},
        },
    }]
    upgrade_sql, rollback_sql, _ = snapshot_diff_to_sql(ops, [], db_name=None)

    def apply_sql(sql: str) -> None:
        with engine.begin() as conn:
            conn.execute(sa.text(sql))

    try:
        _assert_round_trip(
            lambda: _canonical_pg_role(pg_url, role_name),
            apply_sql,
            [upgrade_sql],
            [rollback_sql],
        )
    finally:
        with engine.begin() as conn:
            conn.execute(sa.text(f"DROP ROLE IF EXISTS {role_name}"))


@pytest.mark.integration
def test_clickhouse_add_column_round_trips(ch_client: Any) -> None:
    table_name = "dbw_rt_add_column"
    for sql in (
        f"DROP TABLE IF EXISTS {table_name}",
        f"CREATE TABLE {table_name} (id UInt64) ENGINE = MergeTree() ORDER BY id",
    ):
        ch_client.command(sql)

    _assert_round_trip(
        lambda: _canonical_ch_columns(ch_client, table_name),
        ch_client.command,
        [f"ALTER TABLE {table_name} ADD COLUMN is_merge Bool DEFAULT false"],
        [f"ALTER TABLE {table_name} DROP COLUMN is_merge"],
    )
