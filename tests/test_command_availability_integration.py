from __future__ import annotations

from types import SimpleNamespace

import pytest

from dbwarden.connection.availability import DatabaseAvailability, MultiDatabaseResult


def test_multi_database_result_failed_status_has_hard_failure_code():
    result = MultiDatabaseResult(
        failed=[DatabaseAvailability("primary", False, error_code="connection_failed", message="offline")]
    )
    assert result.status == "failed"
    assert result.exit_code == 1


def test_optional_database_configuration_is_available_to_runner(monkeypatch):
    from dbwarden.connection import availability

    configs = {
        "primary": SimpleNamespace(skip_if_missing=False, sqlalchemy_url="sqlite:///primary.db"),
        "analytics": SimpleNamespace(skip_if_missing=True, sqlalchemy_url="sqlite:///analytics.db"),
    }
    monkeypatch.setattr(availability, "configured_databases", lambda: configs)
    monkeypatch.setattr(
        availability,
        "probe_database",
        lambda name, **kwargs: DatabaseAvailability(name, name == "primary", name == "analytics"),
    )
    called: list[str] = []
    result = availability.run_all_databases(called.append)
    assert called == ["primary"]
    assert result.succeeded == ["primary"]
    assert [item.database for item in result.skipped] == ["analytics"]
