import sqlite3

import pytest

from dbwarden.data.expressions import (
    ExpressionError,
    canonical_expression,
    col,
    func,
    literal,
    lower_expression,
)


def test_in_rejects_unordered_inputs():
    with pytest.raises(TypeError, match="ordered iterable"):
        col("id").in_({1, 2})


def test_mapping_ast_rejects_canonical_duplicate_keys():
    ast = {
        "op": "mapping",
        "value": {"op": "column", "name": "id", "table": None},
        "entries": [
            {
                "key": {"op": "literal", "value": 1},
                "value": {"op": "literal", "value": "a"},
            },
            {
                "key": {"op": "literal", "value": 1},
                "value": {"op": "literal", "value": "b"},
            },
        ],
        "has_else": True,
        "else": {"op": "literal", "value": "c"},
    }
    with pytest.raises(ExpressionError, match="unique"):
        canonical_expression(ast, columns=["id"])


def test_lower_revalidates_embedded_parameter_type():
    spec = canonical_expression(
        {"op": "parameter", "name": "limit", "type": "INTEGER"},
        columns=[],
        parameters={"limit": 2},
    )
    spec["bound_parameters"][0]["type"] = "TEXT"
    with pytest.raises(ExpressionError, match="does not match AST"):
        lower_expression(spec, "sqlite")


def test_raw_null_equality_ast_normalizes_to_is_null():
    spec = canonical_expression(
        {
            "op": "eq",
            "left": {"op": "column", "name": "value", "table": None},
            "right": {"op": "literal", "value": None},
        },
        columns=["value"],
    )
    assert spec["canonical_ast"]["op"] == "is"


def test_json_extract_and_split_part_are_deterministic_and_portable():
    json_spec = canonical_expression(
        func.json_extract(col("payload"), "$.name"), columns=["payload"]
    )
    split_spec = canonical_expression(
        func.split_part(col("path"), "/", 3), columns=["path"]
    )
    connection = sqlite3.connect(":memory:")
    connection.execute("CREATE TABLE values_(payload TEXT, path TEXT)")
    connection.execute(
        "INSERT INTO values_ VALUES(?, ?)", ('{"name":"Ada"}', "one/two/three")
    )
    row = connection.execute(
        "SELECT "
        + lower_expression(json_spec, "sqlite")
        + ", "
        + lower_expression(split_spec, "sqlite")
        + " FROM values_"
    ).fetchone()
    assert row == ("Ada", "three")
    assert "JSON_UNQUOTE(JSON_EXTRACT" in lower_expression(json_spec, "mariadb")
    assert "SPLIT_PART" in lower_expression(split_spec, "postgresql")


def test_json_extract_normalizes_scalar_types_and_rejects_complex_values():
    spec = canonical_expression(
        func.json_extract(col("payload"), "$.value"), columns=["payload"]
    )
    connection = sqlite3.connect(":memory:")
    rows = [
        ('{"value":true}', "true"),
        ('{"value":false}', "false"),
        ('{"value":null}', None),
        ('{"value":""}', ""),
        ('{"value":[]}', None),
        ('{"other":1}', None),
    ]
    connection.execute("CREATE TABLE json_values(payload TEXT)")
    for payload, expected in rows:
        connection.execute("DELETE FROM json_values")
        connection.execute("INSERT INTO json_values VALUES(?)", (payload,))
        actual = connection.execute(
            "SELECT " + lower_expression(spec, "sqlite") + " FROM json_values"
        ).fetchone()[0]
        assert actual == expected
    assert "JSON_TYPE" in lower_expression(spec, "mariadb")
    assert "jsonb_typeof" in lower_expression(spec, "postgresql")
    with pytest.raises(ExpressionError, match="unsupported on ClickHouse"):
        lower_expression(spec, "clickhouse")


def test_split_part_propagates_null_and_uses_empty_string_out_of_range():
    spec = canonical_expression(func.split_part(col("path"), "/", 3), columns=["path"])
    sql = lower_expression(spec, "sqlite")
    connection = sqlite3.connect(":memory:")
    connection.execute("CREATE TABLE paths(path TEXT)")
    connection.execute("INSERT INTO paths VALUES(NULL), ('one/two')")
    assert connection.execute("SELECT " + sql + " FROM paths").fetchall() == [
        (None,),
        ("",),
    ]
    assert "IS NULL THEN NULL" in lower_expression(spec, "mariadb")


def test_backslash_literals_use_mode_independent_utf8_hex():
    value = "\\'; DROP TABLE records; --"
    spec = canonical_expression(literal(value), columns=[])
    sqlite_sql = lower_expression(spec, "sqlite")
    assert (
        sqlite3.connect(":memory:").execute("SELECT " + sqlite_sql).fetchone()[0]
        == value
    )
    assert lower_expression(spec, "mysql").startswith("CONVERT(X'")
    assert lower_expression(spec, "mariadb").endswith("USING utf8mb4)")
    assert lower_expression(spec, "postgresql").startswith("convert_from(decode(")
    assert lower_expression(spec, "clickhouse").startswith("unhex(")


@pytest.mark.parametrize(
    "expression",
    [func.json_extract(col("payload"), "name"), func.split_part(col("path"), "", 0)],
)
def test_dynamic_or_invalid_extractors_fail_closed(expression):
    with pytest.raises(ExpressionError):
        canonical_expression(expression, columns=["payload", "path"])
