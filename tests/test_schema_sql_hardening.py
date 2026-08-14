from __future__ import annotations

from dbwarden.engine.snapshot.sql_gen import snapshot_diff_to_sql


def test_schema_name_is_escaped_in_create_schema_sql():
    upgrade, _rollback, _changes = snapshot_diff_to_sql(
        [{"type": "create_schema", "schema": 'tenant"quoted'}],
        [],
        database="primary",
        db_name="primary",
    )
    assert 'CREATE SCHEMA IF NOT EXISTS "tenant""quoted";' in upgrade
