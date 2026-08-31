"""SQLite end to end: model diff to executable migration SQL and back."""

import sqlite3
import tempfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from dbwarden.engine.core.models import ModelColumn, ModelTable
from dbwarden.engine.offline import diff_model_states, model_state_to_dict
from dbwarden.engine.snapshot import (
    diff_models_against_snapshot,
    extract_full_schema_snapshot,
    snapshot_diff_to_sql,
)


@pytest.fixture
def sqlite_backend(monkeypatch):
    """Point config resolution at a SQLite database."""
    config = SimpleNamespace(database_type="sqlite", model_paths=None, model_tables=None)
    monkeypatch.setattr(
        "dbwarden.engine.model_discovery.type_mapping.get_database",
        lambda db_name=None: config,
    )
    monkeypatch.setattr("dbwarden.config.get_database", lambda db_name=None: config)
    return config


def _col(name, type_="TEXT", *, nullable=True, pk=False, default=None, sq_meta=None):
    return ModelColumn(
        name=name, type=type_, nullable=nullable, primary_key=pk, unique=False,
        default=default, foreign_key=None, sq_meta=sq_meta or {},
        autoincrement=True if pk else None,
    )


def _users_snapshot():
    return {
        "tables": {
            "users": {
                "columns": {
                    "id": {"type": "INTEGER", "nullable": False, "primary_key": True, "autoincrement": True},
                    "email": {"type": "VARCHAR(255)", "nullable": True, "primary_key": False},
                    "age": {"type": "INTEGER", "nullable": True, "primary_key": False},
                },
                "primary_key": ["id"],
                "comment": None,
            },
        },
        "indexes": {
            "ix_users_email": {
                "table": "users", "name": "ix_users_email",
                "columns": ["email"], "unique": False,
            },
        },
        "constraints": {},
        "enums": {},
    }


def _users_model():
    return ModelTable(
        name="users",
        columns=[
            _col("id", "INTEGER", nullable=False, pk=True),
            _col("email", "VARCHAR(255)", nullable=False),
            _col("age", "TEXT"),
        ],
        indexes=[{"name": "ix_users_email", "columns": ["email"], "unique": False}],
        uniques=[{"columns": ["email"], "name": "uq_users_email"}],
    )


def _seeded_database():
    connection = sqlite3.connect(":memory:")
    connection.executescript(
        "CREATE TABLE users (id INTEGER PRIMARY KEY AUTOINCREMENT,"
        " email VARCHAR(255), age INTEGER);"
        "CREATE INDEX ix_users_email ON users (email);"
        "INSERT INTO users (email, age) VALUES ('a@b.c', 41);"
    )
    return connection


class TestMigrationGeneration:
    def test_unsupported_changes_collapse_into_one_rebuild(self, sqlite_backend):
        upgrade_ops, _ = diff_models_against_snapshot(
            [_users_model()], _users_snapshot(), db_name=None,
        )
        assert [op["type"] for op in upgrade_ops] == ["recreate_sq_table"]

    def test_generated_sql_satisfies_the_rollback_contract(self, sqlite_backend):
        upgrade_ops, rollback_ops = diff_models_against_snapshot(
            [_users_model()], _users_snapshot(), db_name=None,
        )
        upgrade, rollback, _ = snapshot_diff_to_sql(
            upgrade_ops, rollback_ops, db_name=None, enforce_rollback_contract=True,
        )
        assert not rollback.strip().startswith("--")
        assert "ALTER TABLE users ALTER COLUMN" not in upgrade

    def test_migration_and_rollback_run_against_sqlite(self, sqlite_backend):
        upgrade_ops, rollback_ops = diff_models_against_snapshot(
            [_users_model()], _users_snapshot(), db_name=None,
        )
        upgrade, rollback, _ = snapshot_diff_to_sql(
            upgrade_ops, rollback_ops, db_name=None, enforce_rollback_contract=True,
        )

        connection = _seeded_database()
        connection.executescript(upgrade)
        assert connection.execute("SELECT id, email, age FROM users").fetchall() == [
            (1, "a@b.c", "41"),
        ]
        schema = connection.execute(
            "SELECT sql FROM sqlite_master WHERE name='users'"
        ).fetchone()[0]
        assert "email VARCHAR(255) NOT NULL" in schema
        assert "UNIQUE (email)" in schema

        connection.executescript(rollback)
        schema = connection.execute(
            "SELECT sql FROM sqlite_master WHERE name='users'"
        ).fetchone()[0]
        assert "age INTEGER" in schema
        assert "UNIQUE (email)" not in schema

    def test_a_plain_added_column_still_uses_alter_table(self, sqlite_backend):
        model = ModelTable(
            name="users",
            columns=[
                _col("id", "INTEGER", nullable=False, pk=True),
                _col("email", "VARCHAR(255)"),
                _col("age", "INTEGER"),
                _col("nickname", "TEXT"),
            ],
            indexes=[{"name": "ix_users_email", "columns": ["email"], "unique": False}],
        )
        upgrade_ops, rollback_ops = diff_models_against_snapshot(
            [model], _users_snapshot(), db_name=None,
        )
        assert [op["type"] for op in upgrade_ops] == ["add_column"]
        upgrade, _, _ = snapshot_diff_to_sql(upgrade_ops, rollback_ops, db_name=None)
        assert "ALTER TABLE users ADD COLUMN nickname TEXT" in upgrade

    def test_created_table_carries_its_constraints_inline(self, sqlite_backend, monkeypatch):
        model = ModelTable(
            name="posts",
            columns=[
                _col("id", "INTEGER", nullable=False, pk=True),
                _col("title", "TEXT", nullable=False),
            ],
            uniques=[{"columns": ["title"], "name": "uq_posts_title"}],
        )
        # CREATE TABLE is rendered from the discovered model, which this test
        # supplies directly instead of through model_paths.
        monkeypatch.setattr(
            "dbwarden.engine.snapshot._find_model_table",
            lambda name, db_name=None: model if name == "posts" else None,
        )
        upgrade_ops, rollback_ops = diff_models_against_snapshot(
            [model], {"tables": {}, "indexes": {}, "constraints": {}, "enums": {}},
            db_name=None,
        )
        assert [op["type"] for op in upgrade_ops] == ["create_table"]
        upgrade, _, _ = snapshot_diff_to_sql(upgrade_ops, rollback_ops, db_name=None)
        assert "CONSTRAINT uq_posts_title UNIQUE (title)" in upgrade
        assert "ADD CONSTRAINT" not in upgrade
        sqlite3.connect(":memory:").executescript(upgrade)


class TestOfflineDiff:
    def test_table_option_change_collapses_into_a_rebuild(self, sqlite_backend):
        before = model_state_to_dict([
            ModelTable(name="sessions", columns=[_col("k", "TEXT", nullable=False, pk=True)]),
        ])
        after = model_state_to_dict([
            ModelTable(
                name="sessions",
                columns=[_col("k", "TEXT", nullable=False, pk=True)],
                sq_table={"sq_without_rowid": True},
            ),
        ])
        upgrade_ops, rollback_ops = diff_model_states(before, after, db_name=None)
        assert [op["type"] for op in upgrade_ops] == ["recreate_sq_table"]

        upgrade, rollback, _ = snapshot_diff_to_sql(
            upgrade_ops, rollback_ops, db_name=None, enforce_rollback_contract=True,
        )
        assert "WITHOUT ROWID" in upgrade

        connection = sqlite3.connect(":memory:")
        connection.executescript("CREATE TABLE sessions (k TEXT NOT NULL PRIMARY KEY);")
        connection.executescript(upgrade)
        assert "WITHOUT ROWID" in connection.execute(
            "SELECT sql FROM sqlite_master WHERE name='sessions'"
        ).fetchone()[0]
        connection.executescript(rollback)
        assert "WITHOUT ROWID" not in connection.execute(
            "SELECT sql FROM sqlite_master WHERE name='sessions'"
        ).fetchone()[0]


class TestSnapshotRoundTrip:
    def test_sqlite_features_survive_snapshot_and_diff_clean(self, sqlite_backend):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "app.db"
            connection = sqlite3.connect(path)
            connection.executescript(
                "CREATE TABLE articles ("
                " id INTEGER PRIMARY KEY AUTOINCREMENT,"
                " title TEXT NOT NULL,"
                " slug TEXT GENERATED ALWAYS AS (lower(title)) STORED);"
                "CREATE TABLE sessions (k TEXT PRIMARY KEY, v TEXT) WITHOUT ROWID;"
            )
            connection.close()

            snapshot = extract_full_schema_snapshot(
                sqlalchemy_url=f"sqlite:///{path}", database_type="sqlite",
            )

        articles = snapshot["tables"]["articles"]
        assert articles["columns"]["slug"]["sq_column"]["sq_generated"] == "lower(title)"
        assert snapshot["tables"]["sessions"]["sq_table"] == {"sq_without_rowid": True}

        model_tables = [
            ModelTable(
                name="articles",
                columns=[
                    _col("id", "INTEGER", nullable=False, pk=True),
                    _col("title", "TEXT", nullable=False),
                    _col("slug", "TEXT", sq_meta={"sq_generated": "lower(title)"}),
                ],
            ),
            ModelTable(
                name="sessions",
                columns=[
                    _col("k", "TEXT", nullable=False, pk=True),
                    _col("v", "TEXT"),
                ],
                sq_table={"sq_without_rowid": True},
            ),
        ]
        upgrade_ops, _ = diff_models_against_snapshot(model_tables, snapshot, db_name=None)
        assert upgrade_ops == []

    def test_primary_key_nullability_does_not_churn(self, sqlite_backend):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "app.db"
            connection = sqlite3.connect(path)
            connection.executescript("CREATE TABLE t (id INTEGER PRIMARY KEY, a TEXT);")
            connection.close()
            snapshot = extract_full_schema_snapshot(
                sqlalchemy_url=f"sqlite:///{path}", database_type="sqlite",
            )
        assert snapshot["tables"]["t"]["columns"]["id"]["nullable"] is False
