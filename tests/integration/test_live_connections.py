"""Live connection smoke tests for every supported backend.

Each test opens a real connection through dbwarden's engine factory
(``dbwarden.connection.connection._get_engine``) and runs ``SELECT 1``.
This is the cheapest thing that catches a regression in a dialect's
connect_args, URL translation, or driver handling - for example the MySQL
keepalive kwargs, which only the MySQLdb (mysqlclient) driver accepts and
PyMySQL rejects.

The containers are optional: pass the matching flag and testcontainers will
start one, or point the env vars at an already-running server. CI supplies
service containers.

Usage::

    pytest tests/integration/test_live_connections.py \
        --pg-integration --mysql-integration --mariadb-integration --ch-integration

Environment variables (for CI service containers)::

    PG_HOST / PG_PORT / PG_USER / PG_PASSWORD / PG_DATABASE
    MYSQL_HOST / MYSQL_PORT / MYSQL_USER / MYSQL_PASSWORD / MYSQL_DATABASE
    MARIADB_HOST / MARIADB_PORT / MARIADB_USER / MARIADB_PASSWORD / MARIADB_DATABASE
    CLICKHOUSE_HOST / CLICKHOUSE_PORT / CLICKHOUSE_USERNAME / CLICKHOUSE_PASSWORD
"""

from __future__ import annotations

import os

import pytest


def _requires_option(pytestconfig: pytest.Config, option: str) -> None:
    if not pytestconfig.getoption(option):
        pytest.skip(f"requires {option}")


def _assert_live_select_one(url: str, db_type: str) -> None:
    import sqlalchemy as sa

    from dbwarden.connection.connection import _get_engine, dispose_engine

    dispose_engine(url, db_type)
    try:
        engine = _get_engine(url, db_type)
        with engine.connect() as conn:
            assert conn.execute(sa.text("SELECT 1")).scalar_one() == 1
    finally:
        dispose_engine(url, db_type)


def _env_url(prefix: str, driver: str = "", default_user: str = "root") -> str | None:
    host = os.environ.get(f"{prefix}_HOST")
    port = os.environ.get(f"{prefix}_PORT")
    if not (host and port):
        return None
    user = os.environ.get(f"{prefix}_USER", default_user)
    password = os.environ.get(f"{prefix}_PASSWORD", "")
    database = os.environ.get(f"{prefix}_DATABASE", "test")
    return f"{driver}://{user}:{password}@{host}:{port}/{database}"


@pytest.fixture(scope="module")
def pg_url(pytestconfig: pytest.Config) -> str:
    _requires_option(pytestconfig, "--pg-integration")

    env_url = _env_url("PG", "postgresql", default_user="postgres")
    if env_url:
        yield env_url
        return

    pytest.importorskip("testcontainers.postgres")
    from testcontainers.postgres import PostgresContainer

    with PostgresContainer("postgres:13-alpine") as pg:
        yield pg.get_connection_url().replace("+psycopg2", "")


@pytest.fixture(scope="module")
def mysql_url(pytestconfig: pytest.Config) -> str:
    _requires_option(pytestconfig, "--mysql-integration")
    pytest.importorskip("pymysql")

    env_url = _env_url("MYSQL", "mysql+pymysql")
    if env_url:
        yield env_url
        return

    pytest.importorskip("testcontainers.mysql")
    from testcontainers.mysql import MySqlContainer

    with MySqlContainer("mysql:8.0", dialect="pymysql") as mysql:
        yield mysql.get_connection_url()


@pytest.fixture(scope="module")
def mariadb_url(pytestconfig: pytest.Config) -> str:
    _requires_option(pytestconfig, "--mariadb-integration")
    pytest.importorskip("pymysql")

    env_url = _env_url("MARIADB", "mariadb+pymysql")
    if env_url:
        yield env_url
        return

    pytest.importorskip("testcontainers.core.container")
    import re

    from testcontainers.core.container import DockerContainer
    from testcontainers.core.wait_strategies import LogMessageWaitStrategy

    # No MariaDB container class ships with testcontainers, and MySqlContainer's
    # MYSQL_* env do not fit the MariaDB image. The doubled "ready for
    # connections" matches once the init server has handed over to the real one.
    ready = LogMessageWaitStrategy(
        re.compile(r".*: ready for connections.*: ready for connections.*", re.DOTALL)
    )
    with (
        DockerContainer("mariadb:11")
        .with_env("MARIADB_ROOT_PASSWORD", "test")
        .with_env("MARIADB_DATABASE", "test")
        .with_exposed_ports(3306)
        .waiting_for(ready)
    ) as mariadb:
        yield (
            f"mariadb+pymysql://root:test@"
            f"{mariadb.get_container_host_ip()}:{mariadb.get_exposed_port(3306)}/test"
        )


@pytest.fixture(scope="module")
def clickhouse_url(pytestconfig: pytest.Config) -> str:
    _requires_option(pytestconfig, "--ch-integration")
    pytest.importorskip("clickhouse_connect")

    host = os.environ.get("CLICKHOUSE_HOST")
    port = os.environ.get("CLICKHOUSE_PORT")
    if host and port:
        user = os.environ.get("CLICKHOUSE_USERNAME", "default")
        password = os.environ.get("CLICKHOUSE_PASSWORD", "")
        creds = f"{user}:{password}@" if password else f"{user}@"
        yield f"http://{creds}{host}:{port}/default"
        return

    pytest.importorskip("testcontainers.clickhouse")
    from testcontainers.clickhouse import ClickHouseContainer

    with ClickHouseContainer(image=pytestconfig.getoption("--ch-image")) as ch:
        creds = f"{ch.username}:{ch.password}@"
        yield (
            f"http://{creds}{ch.get_container_host_ip()}:"
            f"{ch.get_exposed_port(8123)}/default"
        )


@pytest.mark.integration
def test_sqlite_live_connection() -> None:
    _assert_live_select_one("sqlite://", "sqlite")


@pytest.mark.integration
def test_postgresql_live_connection(pg_url: str) -> None:
    _assert_live_select_one(pg_url, "postgresql")


@pytest.mark.integration
def test_mysql_live_connection(mysql_url: str) -> None:
    _assert_live_select_one(mysql_url, "mysql")


@pytest.mark.integration
def test_mariadb_live_connection(mariadb_url: str) -> None:
    _assert_live_select_one(mariadb_url, "mariadb")


@pytest.mark.integration
def test_clickhouse_live_connection(clickhouse_url: str) -> None:
    _assert_live_select_one(clickhouse_url, "clickhouse")
