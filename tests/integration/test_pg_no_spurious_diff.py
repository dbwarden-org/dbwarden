"""A model that matches the database must produce no migration, on live PostgreSQL.

From a bug report: adding one new table produced a 126-operation migration that
renamed and dropped constraints, recreated indexes, reset column storage and
emitted `ALTER TABLE t DROP COLUMN CONSTRAINT` across 41 untouched tables.

Each of those came from a snapshot detail that never compares equal to what the
model says - a constraint PostgreSQL named itself, a default reported with its
cast, an index's implied null ordering - so the only test that can hold the line
is one that reads a real PostgreSQL schema and asserts the diff is empty.

Usage::

    pytest tests/integration/test_pg_no_spurious_diff.py --pg-integration -v

Environment variables (for CI service containers)::

    PG_HOST  PG_PORT  PG_USER  PG_PASSWORD  PG_DATABASE
"""

from __future__ import annotations

import os
import re

import pytest

pytest.importorskip("sqlalchemy")

# Module level: SQLAlchemy resolves Mapped[...] annotations against the defining
# module's namespace, and this file uses `from __future__ import annotations`.
import sqlalchemy as sa  # noqa: E402
from sqlalchemy import ForeignKey, Integer, Numeric, String, Text  # noqa: E402
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column  # noqa: E402

SCHEMA_SQL = """
DROP TABLE IF EXISTS dbw_orders CASCADE;
DROP TABLE IF EXISTS dbw_users CASCADE;

CREATE TABLE dbw_users (
    id SERIAL PRIMARY KEY,
    email VARCHAR(255) NOT NULL UNIQUE,
    status VARCHAR(20) NOT NULL DEFAULT 'queued',
    nickname VARCHAR(50),
    notes TEXT
);

CREATE TABLE dbw_orders (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES dbw_users (id) ON DELETE CASCADE,
    total NUMERIC(12, 2)
);

CREATE INDEX idx_dbw_orders_user_id ON dbw_orders (user_id);

-- Physical tuning a DBA set deliberately, which no model mentions
ALTER TABLE dbw_users ALTER COLUMN notes SET STORAGE MAIN;
"""


def _statements(script: str) -> list[str]:
    """Split SQL the way dbwarden's migration parser does.

    Statements are separated by blank lines and only then by semicolons, so a
    statement that does not end in one is still a single statement. Splitting
    naively on ";" would glue two of those together.
    """
    from dbwarden.engine.file_parser import split_sql_statements

    out: list[str] = []
    for chunk in re.split(r"\n\s*\n", script):
        body = "\n".join(
            line for line in chunk.splitlines()
            if line.strip() and not line.strip().startswith("--")
        ).strip()
        if not body:
            continue
        out.extend(s for s in split_sql_statements(body) if s.strip())
    return out


def _apply_schema(url: str) -> None:
    engine = sa.create_engine(url)
    try:
        with engine.begin() as conn:
            for statement in _statements(SCHEMA_SQL):
                conn.execute(sa.text(statement))
    finally:
        engine.dispose()


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
    url = container.get_connection_url().replace("+psycopg2", "")
    _pg_url._container = container  # type: ignore[attr-defined]
    return url


def _model_tables():
    """The models a team would write for the schema above."""

    class Base(DeclarativeBase):
        pass

    class User(Base):
        __tablename__ = "dbw_users"

        id: Mapped[int] = mapped_column(Integer, primary_key=True)
        # unique=True, with no constraint name: PostgreSQL calls the constraint
        # dbw_users_email_key, dbwarden would call it uq_dbw_users_email.
        email: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
        status: Mapped[str] = mapped_column(
            String(20), nullable=False, server_default="'queued'",
        )
        nickname: Mapped[str] = mapped_column(String(50), nullable=True)
        notes: Mapped[str] = mapped_column(Text, nullable=True)

    class Order(Base):
        __tablename__ = "dbw_orders"

        id: Mapped[int] = mapped_column(Integer, primary_key=True)
        user_id: Mapped[int] = mapped_column(
            Integer,
            ForeignKey("dbw_users.id", ondelete="cascade"),  # lowercase on purpose
            nullable=False,
        )
        total: Mapped[float] = mapped_column(Numeric(12, 2), nullable=True)

        class Meta:
            indexes = [{"name": "idx_dbw_orders_user_id", "columns": ["user_id"]}]

    return Base, [User, Order]


@pytest.fixture(scope="module")
def pg_url(request):
    if not request.config.getoption("--pg-integration"):
        pytest.skip("needs --pg-integration")
    url = _pg_url()
    yield url
    container = getattr(_pg_url, "_container", None)
    if container is not None:
        container.__exit__(None, None, None)


@pytest.mark.integration
class TestNoSpuriousOperations:
    @pytest.fixture()
    def schema(self, pg_url):
        _apply_schema(pg_url)
        return pg_url

    @pytest.fixture()
    def pg_backend(self, monkeypatch, pg_url):
        from types import SimpleNamespace

        config = SimpleNamespace(
            database_type="postgresql", model_paths=None, model_tables=None,
            sqlalchemy_url=pg_url, pg_schema=None,
        )
        monkeypatch.setattr(
            "dbwarden.engine.model_discovery.type_mapping.get_database",
            lambda db_name=None: config,
        )
        monkeypatch.setattr("dbwarden.config.get_database", lambda db_name=None: config)
        return config

    def _snapshot(self, pg_url):
        from dbwarden.engine.snapshot import extract_full_schema_snapshot

        return extract_full_schema_snapshot(
            sqlalchemy_url=pg_url, database_type="postgresql",
        )

    @staticmethod
    def _owned(ops):
        """Only operations against the tables this test creates."""
        owned = {"dbw_users", "dbw_orders"}
        return [op for op in ops if (op.get("table") or op.get("name")) in owned]

    def _extract(self, model_classes):
        from dbwarden.engine.model_discovery.extraction import extract_table_from_model

        tables = [extract_table_from_model(cls) for cls in model_classes]
        assert all(t is not None for t in tables), "model extraction failed"
        return tables

    def test_matching_models_produce_no_operations(self, schema, pg_url, pg_backend):
        from dbwarden.engine.snapshot import diff_models_against_snapshot

        _, model_classes = _model_tables()
        tables = self._extract(model_classes)
        upgrade_ops, _ = diff_models_against_snapshot(
            tables, self._snapshot(pg_url), db_name=None,
        )
        upgrade_ops = self._owned(upgrade_ops)
        assert upgrade_ops == [], (
            "models that match the database produced operations: "
            f"{[(op['type'], op.get('table'), op.get('column') or op.get('name')) for op in upgrade_ops]}"
        )

    def test_no_operation_touches_a_constraint_named_by_the_database(
        self, schema, pg_url, pg_backend,
    ):
        """PostgreSQL's own constraint name must survive an unnamed model unique."""
        from dbwarden.engine.snapshot import diff_models_against_snapshot

        _, model_classes = _model_tables()
        upgrade_ops, _ = diff_models_against_snapshot(
            self._extract(model_classes), self._snapshot(pg_url), db_name=None,
        )
        touching = [
            op for op in self._owned(upgrade_ops)
            if op["type"] in (
                "rename_unique_constraint",
                "drop_unique_constraint",
                "drop_foreign_key",
                "drop_index",
                "alter_pg_column_meta",
                "alter_column_default",
            )
        ]
        assert touching == []

    def test_adding_one_table_only_touches_that_table(self, schema, pg_url, pg_backend):
        """The reported scenario: one new model, one table's worth of operations."""
        from dbwarden.engine.snapshot import diff_models_against_snapshot

        Base, model_classes = _model_tables()

        class BranchReleasePin(Base):
            __tablename__ = "dbw_branch_release_pins"

            id: Mapped[int] = mapped_column(Integer, primary_key=True)
            branch_id: Mapped[int] = mapped_column(
                Integer, ForeignKey("dbw_users.id"), nullable=False, unique=True,
            )
            release_id: Mapped[int] = mapped_column(
                Integer, ForeignKey("dbw_orders.id"), nullable=False,
            )
            label: Mapped[str] = mapped_column(String(50), nullable=True)

        tables = self._extract([*model_classes, BranchReleasePin])
        upgrade_ops, _ = diff_models_against_snapshot(
            tables, self._snapshot(pg_url), db_name=None,
        )

        assert upgrade_ops, "the new table produced no operations"
        # Scoped to the tables this test owns: the target database may hold
        # unrelated tables, and reporting those is correct declarative
        # behaviour, not the spurious-operation bug under test.
        owned = {"dbw_users", "dbw_orders", "dbw_branch_release_pins"}
        unrelated = [
            op for op in upgrade_ops
            if (op.get("table") or op.get("name")) in owned
            and (op.get("table") or op.get("name")) != "dbw_branch_release_pins"
        ]
        assert unrelated == [], (
            "operations against untouched tables: "
            f"{[(op['type'], op.get('table') or op.get('name')) for op in unrelated]}"
        )

    def test_generated_sql_applies_and_then_converges(
        self, schema, pg_url, pg_backend, monkeypatch,
    ):
        """Apply the migration for real, then diff again: it must come back empty."""
        from dbwarden.engine.snapshot import (
            diff_models_against_snapshot,
            snapshot_diff_to_sql,
        )

        Base, model_classes = _model_tables()

        class Pin(Base):
            __tablename__ = "dbw_pins"

            id: Mapped[int] = mapped_column(Integer, primary_key=True)
            user_id: Mapped[int] = mapped_column(
                Integer, ForeignKey("dbw_users.id", ondelete="CASCADE"), nullable=False,
            )

        tables = self._extract([*model_classes, Pin])
        by_name = {t.name: t for t in tables}
        monkeypatch.setattr(
            "dbwarden.engine.snapshot._find_model_table",
            lambda name, db_name=None: by_name.get(name),
        )

        try:
            upgrade_ops, rollback_ops = diff_models_against_snapshot(
                tables, self._snapshot(pg_url), db_name=None,
            )
            # Apply only what belongs to this test. The target database is
            # shared, and applying another test's operations would make this
            # one fail for reasons that have nothing to do with convergence.
            owned = {"dbw_users", "dbw_orders", "dbw_pins"}
            upgrade_ops = [
                op for op in upgrade_ops
                if (op.get("table") or op.get("name")) in owned
            ]
            rollback_ops = [
                op for op in rollback_ops
                if (op.get("table") or op.get("name")) in owned
            ]
            upgrade, _, _ = snapshot_diff_to_sql(
                upgrade_ops, rollback_ops, db_name=None, concurrent=False,
            )
            assert "DROP COLUMN CONSTRAINT" not in upgrade.upper()

            engine = sa.create_engine(pg_url)
            with engine.begin() as conn:
                for statement in _statements(upgrade):
                    conn.execute(sa.text(statement))
            engine.dispose()

            residual, _ = diff_models_against_snapshot(
                tables, self._snapshot(pg_url), db_name=None,
            )
            residual = [
                op for op in residual
                if (op.get("table") or op.get("name")) in {"dbw_users", "dbw_orders", "dbw_pins"}
            ]
            assert residual == [], (
                "schema did not converge after applying the migration: "
                f"{[(op['type'], op.get('table')) for op in residual]}"
            )
        finally:
            engine = sa.create_engine(pg_url)
            with engine.begin() as conn:
                conn.execute(sa.text("DROP TABLE IF EXISTS dbw_pins CASCADE"))
            engine.dispose()


@pytest.mark.integration
class TestPendingMigrationsAreParsedCorrectly:
    """`ALTER TABLE ... ADD CONSTRAINT` in a pending migration is not a column."""

    def test_merged_migrations_do_not_invent_columns(self, pg_url, tmp_path, monkeypatch):
        from types import SimpleNamespace

        from dbwarden.commands.make_migrations.snapshot_merge import (
            _merge_pending_migrations_into_snapshot,
        )
        from dbwarden.engine.snapshot import (
            diff_models_against_snapshot,
            extract_full_schema_snapshot,
        )

        config = SimpleNamespace(
            database_type="postgresql", model_paths=None, model_tables=None,
            sqlalchemy_url=pg_url, pg_schema=None,
        )
        monkeypatch.setattr(
            "dbwarden.engine.model_discovery.type_mapping.get_database",
            lambda db_name=None: config,
        )
        monkeypatch.setattr("dbwarden.config.get_database", lambda db_name=None: config)

        _apply_schema(pg_url)

        # A migration in the same shape dbwarden itself writes for a new table.
        (tmp_path / "primary__0001_initial.sql").write_text(
            "-- upgrade\n\n"
            "CREATE TABLE dbw_things (\n    id SERIAL PRIMARY KEY,\n"
            "    email VARCHAR(255)\n);\n\n"
            "ALTER TABLE dbw_things ADD CONSTRAINT uq_dbw_things_email UNIQUE (email);\n\n"
            "ALTER TABLE dbw_things ADD CONSTRAINT fk_dbw_things_user "
            "FOREIGN KEY (id) REFERENCES dbw_users (id);\n\n"
            "-- rollback\n\nDROP TABLE dbw_things;\n"
        )

        snapshot = extract_full_schema_snapshot(
            sqlalchemy_url=pg_url, database_type="postgresql",
        )
        _merge_pending_migrations_into_snapshot(snapshot, str(tmp_path))

        merged = snapshot["tables"]["dbw_things"]["columns"]
        assert set(merged) == {"id", "email"}

        _, model_classes = _model_tables()
        from dbwarden.engine.model_discovery.extraction import extract_table_from_model

        tables = [extract_table_from_model(cls) for cls in model_classes]
        upgrade_ops, _ = diff_models_against_snapshot(tables, snapshot, db_name=None)

        drops = [op for op in upgrade_ops if op["type"] == "drop_column"]
        assert not any(op.get("column", "").lower() == "constraint" for op in drops)
