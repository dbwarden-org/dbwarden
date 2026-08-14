from __future__ import annotations

import pytest

from dbwarden.config_schema import structure_database_entry
from dbwarden.exceptions import ConfigurationError


def _entry(**overrides):
    values = {
        "database_name": "primary",
        "database_type": "postgresql",
        "database_url_sync": "postgresql://localhost/app",
    }
    values.update(overrides)
    return structure_database_entry(values)


@pytest.mark.parametrize("value", ["public; DROP SCHEMA public", "public'", "public.schema"])
def test_postgres_schema_rejects_sql_injection(value):
    with pytest.raises(ConfigurationError, match="Invalid pg_schema"):
        _entry(pg_schema=value)


def test_postgres_schema_accepts_identifier():
    assert _entry(pg_schema="app_schema").pg_schema == "app_schema"


@pytest.mark.parametrize("value", [-1, True, "5000", 1.5])
def test_lock_timeout_requires_non_negative_integer(value):
    with pytest.raises(ConfigurationError, match="pg_migration_lock_timeout"):
        _entry(pg_migration_lock_timeout=value)


def test_lock_timeout_accepts_zero():
    assert _entry(pg_migration_lock_timeout=0).pg_migration_lock_timeout == 0
