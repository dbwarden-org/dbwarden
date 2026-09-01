from __future__ import annotations

import pytest
from sqlalchemy import create_engine

from dbwarden.connection.connection import sandbox_override
from dbwarden.repositories.migrations_repo import _execute_autocommit


def test_sandbox_autocommit_propagates_sql_errors(tmp_path):
    url = f"sqlite:///{tmp_path / 'sandbox.db'}"
    with sandbox_override(url, "sqlite"):
        with pytest.raises(Exception):
            _execute_autocommit("THIS IS NOT SQL")


def test_sandbox_autocommit_persists_successful_statement(tmp_path):
    url = f"sqlite:///{tmp_path / 'sandbox.db'}"
    with sandbox_override(url, "sqlite"):
        _execute_autocommit("CREATE TABLE hardening_check (id INTEGER)")

    engine = create_engine(url)
    try:
        assert "hardening_check" in __import__("sqlalchemy", fromlist=["inspect"]).inspect(engine).get_table_names()
    finally:
        engine.dispose()
