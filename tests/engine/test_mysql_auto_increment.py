"""A generated primary key must be generated on every backend that has one.

PostgreSQL gets ``SERIAL`` and SQLite gets ``AUTOINCREMENT`` from the same
model, but MySQL and MariaDB rendered ``id INTEGER NOT NULL PRIMARY KEY`` with
no ``AUTO_INCREMENT``. The migration applies cleanly; the failure arrives later,
on the first insert that omits the key:

    (1364, "Field 'id' doesn't have a default value")

Found by the harness, which applies the migration and then writes a row.
"""

from types import SimpleNamespace

import pytest

from dbwarden.engine.core.models import ModelColumn, ModelTable
from dbwarden.engine.model_discovery.sql_generation import generate_create_table_sql


@pytest.fixture
def backend(monkeypatch):
    """Point generation at one backend without touching real configuration."""

    def _use(name):
        config = SimpleNamespace(
            database_type=name, model_paths=None, model_tables=None,
            sqlalchemy_url=f"{name}://ignored/db",
        )
        monkeypatch.setattr(
            "dbwarden.engine.model_discovery.type_mapping.get_database",
            lambda db_name=None: config,
        )
        monkeypatch.setattr("dbwarden.config.get_database", lambda db_name=None: config)
        return config

    return _use


def _column(name, type_="INTEGER", *, pk=False, autoincrement=None, nullable=True):
    return ModelColumn(
        name=name, type=type_, nullable=nullable, primary_key=pk, unique=False,
        default=None, foreign_key=None, autoincrement=autoincrement,
    )


def _table(*columns, name="branches"):
    return ModelTable(name=name, columns=list(columns))


@pytest.mark.parametrize("dialect", ("mysql", "mariadb"))
class TestGeneratedPrimaryKey:
    def test_integer_primary_key_is_auto_increment(self, backend, dialect):
        backend(dialect)
        sql = generate_create_table_sql(
            _table(_column("id", pk=True, autoincrement=True, nullable=False)),
        )
        assert "AUTO_INCREMENT" in sql, sql

    def test_an_implicit_integer_primary_key_is_also_auto_increment(self, backend, dialect):
        """SQLAlchemy's default is ``autoincrement="auto"``; PostgreSQL honours it."""
        backend(dialect)
        sql = generate_create_table_sql(
            _table(_column("id", pk=True, autoincrement="auto", nullable=False)),
        )
        assert "AUTO_INCREMENT" in sql, sql

    def test_an_opted_out_key_is_left_alone(self, backend, dialect):
        backend(dialect)
        sql = generate_create_table_sql(
            _table(_column("id", pk=True, autoincrement=False, nullable=False)),
        )
        assert "AUTO_INCREMENT" not in sql, sql

    def test_a_non_integer_key_is_left_alone(self, backend, dialect):
        backend(dialect)
        sql = generate_create_table_sql(
            _table(_column("code", "VARCHAR(32)", pk=True, autoincrement=True, nullable=False)),
        )
        assert "AUTO_INCREMENT" not in sql, sql

    def test_a_composite_key_is_left_alone(self, backend, dialect):
        """MySQL allows at most one auto-increment column, and it must be a key."""
        backend(dialect)
        sql = generate_create_table_sql(
            _table(
                _column("branch_id", pk=True, autoincrement=True, nullable=False),
                _column("seq_no", pk=True, autoincrement=True, nullable=False),
            ),
        )
        assert "AUTO_INCREMENT" not in sql, sql

    def test_a_plain_column_is_left_alone(self, backend, dialect):
        backend(dialect)
        sql = generate_create_table_sql(
            _table(
                _column("id", pk=True, autoincrement=True, nullable=False),
                _column("branch_id", nullable=False),
            ),
        )
        assert sql.count("AUTO_INCREMENT") == 1, sql
