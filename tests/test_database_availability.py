from __future__ import annotations

from contextlib import contextmanager

from dbwarden.config_schema import DatabaseEntry
from dbwarden.database.availability import (
    DatabaseAvailability,
    MultiDatabaseResult,
    probe_database,
)
from dbwarden.exceptions import DBDisconnectedError


def test_optional_connection_failure_is_skipped(monkeypatch):
    config = DatabaseEntry(
        database_name="analytics",
        database_type="sqlite",
        database_url_sync="sqlite:///analytics.db",
        skip_if_missing=True,
    )
    monkeypatch.setattr("dbwarden.database.availability.get_database", lambda name: config)

    @contextmanager
    def failing_connection(_name):
        raise DBDisconnectedError("postgresql://user:secret@host/db")
        yield

    monkeypatch.setattr("dbwarden.database.connection.get_db_connection", failing_connection)
    result = probe_database("analytics", optional=True)
    assert result.skipped is True
    assert "secret" not in (result.message or "")
    assert result.error_code == "connection_failed"


def test_partial_success_has_dedicated_exit_code():
    result = MultiDatabaseResult(succeeded=["primary"])
    result.skipped.append(DatabaseAvailability("analytics", False, True, "connection_failed", "offline"))
    assert result.status == "partial_success"
    assert result.exit_code == 3
