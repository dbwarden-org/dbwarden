"""A generated migration must produce constraints the application can rely on.

From a bug report: an initial migration was generated with every `UNIQUE`
constraint missing, so the schema applied cleanly and only failed later, at
runtime, on the first upsert::

    asyncpg.exceptions.InvalidColumnReferenceError: there is no unique or
    exclusion constraint matching the ON CONFLICT specification

A test that only inspects generated SQL cannot catch that class of bug, because
the SQL is valid either way. These tests apply the migration to a real
PostgreSQL server and then run the statement the application would run.

Usage::

    pytest tests/integration/test_pg_constraint_emission.py --pg-integration -v

Environment variables (for CI service containers)::

    PG_HOST  PG_PORT  PG_USER  PG_PASSWORD  PG_DATABASE
"""

from __future__ import annotations

import os
from types import SimpleNamespace
from unittest.mock import patch

import pytest

pytest.importorskip("sqlalchemy")

import sqlalchemy as sa  # noqa: E402

from dbwarden.commands.make_migrations.pipeline import generate_migration_sql  # noqa: E402
from dbwarden.engine.core.models import ModelColumn, ModelTable  # noqa: E402
from dbwarden.engine.snapshot import (  # noqa: E402
    diff_models_against_snapshot,
    snapshot_diff_to_sql,
)

EMPTY_SNAPSHOT = {"tables": {}, "indexes": {}, "constraints": {}, "enums": {}}


def _pg_url() -> str:
    host = os.environ.get("PG_HOST")
    port = os.environ.get("PG_PORT")
    if host and port:
        user = os.environ.get("PG_USER", "postgres")
        password = os.environ.get("PG_PASSWORD", "postgres")
        database = os.environ.get("PG_DATABASE", "dbwarden_test")
        return f"postgresql://{user}:{password}@{host}:{port}/{database}"

    pytest.importorskip("testcontainers.postgres")
    from testcontainers.postgres import PostgresContainer

    container = PostgresContainer("postgres:16-alpine")
    container.__enter__()
    _pg_url._container = container  # type: ignore[attr-defined]
    return container.get_connection_url().replace("+psycopg2", "")


@pytest.fixture(scope="module")
def pg_url(request):
    if not request.config.getoption("--pg-integration"):
        pytest.skip("needs --pg-integration")
    url = _pg_url()
    yield url
    container = getattr(_pg_url, "_container", None)
    if container is not None:
        container.__exit__(None, None, None)


@pytest.fixture
def pg_backend(monkeypatch, pg_url):
    config = SimpleNamespace(
        database_type="postgresql", model_paths=None, model_tables=None,
        sqlalchemy_url=pg_url, pg_schema=None, postgres_schema=None,
    )
    for target in (
        "dbwarden.engine.model_discovery.type_mapping.get_database",
        "dbwarden.config.get_database",
        "dbwarden.commands.make_migrations.pipeline.get_database",
    ):
        monkeypatch.setattr(target, lambda db_name=None: config)
    monkeypatch.setattr(
        "dbwarden.commands.make_migrations.pipeline.get_multi_db_config",
        lambda: SimpleNamespace(default="primary"),
    )
    return config


def _col(name, type_, *, nullable=True, pk=False, unique=False):
    return ModelColumn(
        name=name, type=type_, nullable=nullable, primary_key=pk, unique=unique,
        default=None, foreign_key=None,
        # An autoincrementing key renders as SERIAL, so rows can be inserted
        # without supplying an id.
        autoincrement=True if pk else None,
    )


def _sync_heartbeat_model():
    return ModelTable(
        name="dbw_sync_heartbeat",
        columns=[
            _col("id", "integer", nullable=False, pk=True),
            _col("branch_id", "varchar", nullable=False),
            _col("last_seen", "varchar"),
        ],
        uniques=[{
            "columns": ["branch_id"],
            "name": "uq_dbw_sync_heartbeat_branch_id",
        }],
    )


def _apply(url: str, sql: str) -> None:
    engine = sa.create_engine(url)
    try:
        with engine.begin() as conn:
            for statement in sql.split(";"):
                body = "\n".join(
                    line for line in statement.splitlines()
                    if line.strip() and not line.strip().startswith("--")
                ).strip()
                if body:
                    conn.execute(sa.text(body))
    finally:
        engine.dispose()


def _drop(url: str, table: str) -> None:
    engine = sa.create_engine(url)
    try:
        with engine.begin() as conn:
            conn.execute(sa.text(f"DROP TABLE IF EXISTS {table} CASCADE"))
    finally:
        engine.dispose()


def _unique_constraints(url: str, table: str) -> set[str]:
    engine = sa.create_engine(url)
    try:
        with engine.connect() as conn:
            rows = conn.execute(
                sa.text(
                    "SELECT conname FROM pg_constraint "
                    "WHERE conrelid = CAST(:t AS regclass) AND contype = 'u'"
                ),
                {"t": table},
            ).fetchall()
    finally:
        engine.dispose()
    return {row[0] for row in rows}


def _fallback_sql(tables):
    with patch("dbwarden.engine.snapshot.find_latest_snapshot", return_value=None), \
         patch(
             "dbwarden.engine.snapshot.extract_full_schema_snapshot",
             side_effect=OSError("database unreachable"),
         ), \
         patch(
             "dbwarden.commands.make_migrations.pipeline.extract_tables_from_database",
             return_value={},
         ):
        upgrade, rollback, _ = generate_migration_sql(tables, None, None, None)
    return upgrade, rollback


def _snapshot_sql(tables):
    upgrade_ops, rollback_ops = diff_models_against_snapshot(
        tables, dict(EMPTY_SNAPSHOT), db_name=None,
    )
    by_name = {t.name: t for t in tables}
    with patch(
        "dbwarden.engine.snapshot._find_model_table",
        lambda name, db_name=None: by_name.get(name),
    ):
        upgrade, rollback, _ = snapshot_diff_to_sql(
            upgrade_ops, rollback_ops, db_name=None, concurrent=False,
        )
    return upgrade, rollback


@pytest.mark.integration
@pytest.mark.parametrize("generate", [_fallback_sql, _snapshot_sql], ids=["fallback", "snapshot"])
class TestUniqueConstraintReachesTheDatabase:
    def test_upsert_on_the_unique_column_succeeds(self, pg_url, pg_backend, generate):
        """The exact statement that failed in the report."""
        table = _sync_heartbeat_model()
        _drop(pg_url, table.name)
        upgrade, _ = generate([table])
        _apply(pg_url, upgrade)

        engine = sa.create_engine(pg_url)
        try:
            with engine.begin() as conn:
                for _ in range(2):
                    conn.execute(sa.text(
                        f"INSERT INTO {table.name} (branch_id, last_seen) "
                        "VALUES ('b1', 'now') "
                        "ON CONFLICT (branch_id) DO UPDATE SET last_seen = 'later'"
                    ))
                count = conn.execute(
                    sa.text(f"SELECT count(*) FROM {table.name}")
                ).scalar()
                last_seen = conn.execute(
                    sa.text(f"SELECT last_seen FROM {table.name}")
                ).scalar()
        finally:
            engine.dispose()
            _drop(pg_url, table.name)

        assert count == 1
        assert last_seen == "later"

    def test_the_constraint_exists_under_its_declared_name(self, pg_url, pg_backend, generate):
        table = _sync_heartbeat_model()
        _drop(pg_url, table.name)
        upgrade, _ = generate([table])
        _apply(pg_url, upgrade)
        try:
            names = _unique_constraints(pg_url, table.name)
        finally:
            _drop(pg_url, table.name)
        assert names == {"uq_dbw_sync_heartbeat_branch_id"}

    def test_duplicate_values_are_rejected(self, pg_url, pg_backend, generate):
        table = _sync_heartbeat_model()
        _drop(pg_url, table.name)
        upgrade, _ = generate([table])
        _apply(pg_url, upgrade)

        engine = sa.create_engine(pg_url)
        try:
            with engine.begin() as conn:
                conn.execute(sa.text(
                    f"INSERT INTO {table.name} (branch_id) VALUES ('b1')"
                ))
            with pytest.raises(sa.exc.IntegrityError):
                with engine.begin() as conn:
                    conn.execute(sa.text(
                        f"INSERT INTO {table.name} (branch_id) VALUES ('b1')"
                    ))
        finally:
            engine.dispose()
            _drop(pg_url, table.name)

    def test_rollback_removes_the_constraint(self, pg_url, pg_backend, generate):
        table = _sync_heartbeat_model()
        _drop(pg_url, table.name)
        upgrade, rollback = generate([table])
        _apply(pg_url, upgrade)
        try:
            assert _unique_constraints(pg_url, table.name)
            _apply(pg_url, rollback)
            remaining = sa.create_engine(pg_url)
            try:
                with remaining.connect() as conn:
                    exists = conn.execute(sa.text(
                        "SELECT to_regclass(:t)"
                    ), {"t": table.name}).scalar()
            finally:
                remaining.dispose()
            assert exists is None
        finally:
            _drop(pg_url, table.name)


@pytest.mark.integration
class TestColumnLevelUniqueIsNotDuplicated:
    """`unique=True` must create exactly one constraint, not two."""

    def test_only_one_unique_constraint_is_created(self, pg_url, pg_backend):
        table = ModelTable(
            name="dbw_users_unique",
            columns=[
                _col("id", "integer", nullable=False, pk=True),
                _col("email", "varchar", nullable=False, unique=True),
            ],
            uniques=[{"columns": ["email"]}],
        )
        _drop(pg_url, table.name)
        upgrade, _ = _snapshot_sql([table])
        _apply(pg_url, upgrade)
        try:
            names = _unique_constraints(pg_url, table.name)
        finally:
            _drop(pg_url, table.name)
        assert len(names) == 1, f"expected one unique constraint, got {names}"
        assert names == {"uq_dbw_users_unique_email"}


@pytest.mark.integration
class TestForeignKeyReachesTheDatabase:
    """A foreign key to a plain new table must actually be enforced.

    The referenced table declares no constraints of its own, which used to make
    the add-foreign-key guard treat it as missing and drop the constraint.
    """

    @staticmethod
    def _tables():
        owners = ModelTable(
            name="dbw_fk_owners",
            columns=[_col("id", "integer", nullable=False, pk=True)],
        )
        pets = ModelTable(
            name="dbw_fk_pets",
            columns=[
                _col("id", "integer", nullable=False, pk=True),
                _col("owner_id", "integer"),
            ],
            foreign_keys=[{
                "columns": ["owner_id"],
                "referred_table": "dbw_fk_owners",
                "referred_columns": ["id"],
                "on_delete": "CASCADE",
            }],
        )
        return [owners, pets]

    def _setup(self, pg_url):
        tables = self._tables()
        for table in reversed(tables):
            _drop(pg_url, table.name)
        upgrade, _ = _snapshot_sql(tables)
        _apply(pg_url, upgrade)
        return tables

    def _teardown(self, pg_url, tables):
        for table in reversed(tables):
            _drop(pg_url, table.name)

    def test_the_foreign_key_is_enforced(self, pg_url, pg_backend):
        tables = self._setup(pg_url)
        engine = sa.create_engine(pg_url)
        try:
            with pytest.raises(sa.exc.IntegrityError):
                with engine.begin() as conn:
                    conn.execute(sa.text(
                        "INSERT INTO dbw_fk_pets (owner_id) VALUES (999)"
                    ))
        finally:
            engine.dispose()
            self._teardown(pg_url, tables)

    def test_on_delete_cascade_is_applied(self, pg_url, pg_backend):
        tables = self._setup(pg_url)
        engine = sa.create_engine(pg_url)
        try:
            with engine.begin() as conn:
                conn.execute(sa.text("INSERT INTO dbw_fk_owners (id) VALUES (1)"))
                conn.execute(sa.text(
                    "INSERT INTO dbw_fk_pets (owner_id) VALUES (1)"
                ))
                conn.execute(sa.text("DELETE FROM dbw_fk_owners WHERE id = 1"))
                remaining = conn.execute(
                    sa.text("SELECT count(*) FROM dbw_fk_pets")
                ).scalar()
        finally:
            engine.dispose()
            self._teardown(pg_url, tables)
        assert remaining == 0

    def test_the_constraint_is_listed_in_the_catalog(self, pg_url, pg_backend):
        tables = self._setup(pg_url)
        engine = sa.create_engine(pg_url)
        try:
            with engine.connect() as conn:
                count = conn.execute(sa.text(
                    "SELECT count(*) FROM pg_constraint "
                    "WHERE conrelid = CAST('dbw_fk_pets' AS regclass) AND contype = 'f'"
                )).scalar()
        finally:
            engine.dispose()
            self._teardown(pg_url, tables)
        assert count == 1, "expected exactly one foreign key, not zero or a duplicate"
