from __future__ import annotations

import pytest

from dbwarden.engine.model_discovery import ModelColumn, ModelTable
from dbwarden.engine.model_discovery import sql_generation
from dbwarden.engine.offline import diff_model_states, model_state_to_dict
from dbwarden.engine.snapshot import snapshot_diff_to_sql
from tests.support.sql_assertions import assert_sql_contains, normalize_sql

pytestmark = pytest.mark.sql


@pytest.mark.parametrize("backend", ["sqlite", "mysql", "mariadb", "clickhouse"])
@pytest.mark.parametrize("identifier", ["order", "MixedCase", "name with spaces", "quote`name"])
def test_create_table_quotes_non_postgresql_identifiers(monkeypatch, backend, identifier):
    monkeypatch.setattr(sql_generation._type_mapping, "_get_backend_name", lambda _db=None: backend)
    table = ModelTable(
        name=identifier,
        schema="select",
        columns=[
            ModelColumn(identifier, "integer", False, True, False, None, None),
        ],
    )

    sql = sql_generation.generate_create_table_sql(table)

    assert "`" in sql or '"' in sql
    escaped = identifier.replace("`", "``") if backend != "sqlite" else identifier.replace('"', '""')
    assert escaped in sql


def test_offline_generation_is_deterministic():
    before = model_state_to_dict([])
    after = model_state_to_dict([
        ModelTable(
            name="users",
            columns=[
                ModelColumn("id", "integer", False, True, False, None, None),
                ModelColumn("email", "varchar", False, False, True, None, None),
            ],
        )
    ])
    up_one, down_one = diff_model_states(before, after)
    up_two, down_two = diff_model_states(before, after)

    assert up_one == up_two
    assert down_one == down_two


def test_offline_sql_generation_is_deterministic(monkeypatch):
    monkeypatch.setattr(sql_generation._type_mapping, "_get_backend_name", lambda _db=None: "sqlite")
    table = ModelTable(name="users", columns=[ModelColumn("id", "integer", False, True, False, None, None)])
    first = sql_generation.generate_create_table_sql(table)
    second = sql_generation.generate_create_table_sql(table)

    assert normalize_sql(first) == normalize_sql(second)


def test_identifier_quoting_escapes_embedded_quote(monkeypatch):
    monkeypatch.setattr(sql_generation._type_mapping, "_get_backend_name", lambda _db=None: "sqlite")
    table = ModelTable(
        name='quote"name',
        columns=[ModelColumn('column"name', "integer", True, False, False, None, None)],
    )

    sql = sql_generation.generate_create_table_sql(table)

    assert_sql_contains(sql, '"quote""name"', '"column""name"')
