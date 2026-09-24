from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import date, datetime, timezone
from decimal import Decimal

import pytest
import sqlalchemy as sa

from dbwarden.data.expressions import (
    EXPRESSION_LANGUAGE_VERSION,
    Expr,
    ExpressionError,
    canonical_expression,
    case,
    cast,
    col,
    func,
    literal,
    lower_expression,
    mapping,
    param,
)


def test_expr_is_immutable_and_never_a_python_boolean():
    expression = col("enabled").is_(True)

    with pytest.raises(FrozenInstanceError):
        expression._kind = "literal"  # type: ignore[misc]
    with pytest.raises(TypeError, match="Python booleans"):
        bool(expression)


def test_canonical_expression_records_semantics_and_resolves_unique_column():
    expression = func.lower(func.trim(col("email"))) == literal("A@example.com")

    spec = canonical_expression(expression, columns=["users.email"], backend="sqlite")

    assert spec["language_version"] == EXPRESSION_LANGUAGE_VERSION
    assert spec["referenced_columns"] == ["users.email"]
    assert spec["functions"] == ["lower", "trim"]
    assert spec["determinism_class"] == "replayable"
    assert spec["bound_parameters"] == []
    assert spec["canonical_ast"]["left"]["args"][0]["args"][0] == {
        "op": "column",
        "name": "email",
        "table": "users",
    }
    assert (
        spec["normalized_sql"] == '(LOWER(TRIM("users"."email")) = \'A@example.com\')'
    )


def test_bound_parameters_are_frozen_and_literalized():
    instant = datetime(2026, 9, 22, 14, 15, 16, 123456, tzinfo=timezone.utc)
    expression = (
        (col("amount") >= param("minimum", Decimal))
        & (col("created") < param("cutoff", datetime))
        & (col("day") == param("day", date))
    )

    spec = canonical_expression(
        expression,
        columns=["amount", "created", "day"],
        parameters={
            "minimum": Decimal("1.2300"),
            "cutoff": instant,
            "day": date(2026, 9, 22),
        },
        backend="postgresql",
    )

    assert spec["determinism_class"] == "parameter_bound"
    assert spec["bound_parameters"] == [
        {
            "name": "cutoff",
            "type": "TIMESTAMP",
            "value": {"$type": "datetime", "value": "2026-09-22T14:15:16Z"},
        },
        {
            "name": "day",
            "type": "DATE",
            "value": {"$type": "date", "value": "2026-09-22"},
        },
        {
            "name": "minimum",
            "type": "DECIMAL",
            "value": {"$type": "decimal", "value": "1.23"},
        },
    ]
    assert "TIMESTAMPTZ '2026-09-22T14:15:16Z'" in lower_expression(spec, "postgresql")
    assert "DATE '2026-09-22'" in lower_expression(spec, "postgresql")


def test_literals_and_identifiers_are_escaped_not_executed():
    spec = canonical_expression(
        col('na"me', 'odd"table') == "x'; DROP TABLE users; --",
        columns=['odd"table.na"me'],
    )

    assert lower_expression(spec, "sqlite") == (
        '("odd""table"."na""me" = \'x\'\'; DROP TABLE users; --\')'
    )
    assert lower_expression(spec, "mysql") == (
        "(`odd\"table`.`na\"me` = 'x''; DROP TABLE users; --')"
    )


def test_unknown_inputs_and_unsafe_sql_are_rejected():
    with pytest.raises(ExpressionError, match="Unknown column"):
        canonical_expression(col("missing"), columns=["known"])
    with pytest.raises(AttributeError, match="not in expression language"):
        func.random()
    with pytest.raises(ExpressionError, match="Raw SQLAlchemy text"):
        canonical_expression(sa.text("random()"), columns=[])
    with pytest.raises(ExpressionError, match="literal_column"):
        canonical_expression(sa.literal_column("users.email"), columns=["users.email"])
    with pytest.raises(ExpressionError, match="callables"):
        canonical_expression(lambda: 1, columns=[])
    with pytest.raises(ExpressionError, match="Non-finite"):
        canonical_expression(literal(float("inf")), columns=[])
    with pytest.raises(ExpressionError, match="unsafe cast type"):
        cast(col("value"), "TEXT); DROP TABLE users; --")


def test_case_and_mapping_report_incomplete_domains_for_parent_validation():
    incomplete_case = canonical_expression(
        case((col("state") == "new", 1)), columns=["state"]
    )
    incomplete_mapping = canonical_expression(
        mapping(col("state"), {"new": 1}), columns=["state"]
    )
    complete = canonical_expression(
        mapping(col("state"), {"new": 1, "old": 2}, else_=0),
        columns=["state"],
    )

    assert incomplete_case["total_domain_complete"] is False
    assert incomplete_mapping["total_domain_complete"] is False
    assert complete["total_domain_complete"] is True
    assert lower_expression(incomplete_case, "sqlite").endswith("THEN 1 END")


def test_static_mapping_is_canonical_regardless_of_dict_order():
    first = canonical_expression(
        mapping(col("state"), {"b": 2, "a": 1}, else_=0), columns=["state"]
    )
    second = canonical_expression(
        mapping(col("state"), {"a": 1, "b": 2}, else_=0), columns=["state"]
    )

    assert first == second


def test_boolean_associativity_and_order_have_one_canonical_form():
    a = col("a") == 1
    b = col("b") == 2
    c = col("c") == 3

    first = canonical_expression((a & b) & c, columns=["a", "b", "c"])
    second = canonical_expression(c & (b & a), columns=["a", "b", "c"])

    assert first == second


def test_operators_cast_and_alias_lowering():
    expression = (
        ((cast(col("score", "users"), "INTEGER") + 2) * 3 >= 12)
        & col("status", "users").in_(["new", "ready"])
        & col("deleted_at", "users").is_not(None)
    )
    spec = canonical_expression(
        expression,
        columns=["users.score", "users.status", "users.deleted_at"],
    )

    sql = lower_expression(spec, "sqlite", aliases={"users": "u"})

    assert 'CAST("u"."score" AS INTEGER)' in sql
    assert "(\"u\".\"status\" IN ('new', 'ready'))" in sql
    assert '("u"."deleted_at" IS NOT NULL)' in sql

    unqualified = canonical_expression(col("score") + 1, columns=["score"])
    assert lower_expression(unqualified, "sqlite", aliases={"": "source"}) == (
        '("source"."score" + 1)'
    )
    assert lower_expression(unqualified, "sqlite", aliases={None: "source"}) == (
        '("source"."score" + 1)'
    )


def test_sqlalchemy_safe_nodes_convert_to_the_same_ast():
    users = sa.table("users", sa.column("email"), sa.column("active"))
    native = func.lower(col("email", "users")) == "x"
    sqlalchemy_expression = sa.func.lower(users.c.email) == "x"

    assert canonical_expression(
        native, columns=["users.email"]
    ) == canonical_expression(
        sqlalchemy_expression,
        columns=["users.email"],
    )
    assert canonical_expression(
        sa.and_(users.c.active.is_(True), users.c.email.in_(["a", "b"])),
        columns=["users.active", "users.email"],
    )["referenced_columns"] == ["users.active", "users.email"]
    with pytest.raises(ExpressionError, match="not allowed"):
        canonical_expression(sa.func.random(), columns=[])


def test_sqlalchemy_simple_case_and_string_concat_are_converted_explicitly():
    users = sa.table(
        "users",
        sa.column("kind", sa.String()),
        sa.column("first", sa.String()),
        sa.column("last", sa.String()),
    )
    expression = sa.case(
        {"person": users.c.first + users.c.last}, value=users.c.kind, else_="unknown"
    )

    spec = canonical_expression(
        expression,
        columns=["users.kind", "users.first", "users.last"],
    )

    assert spec["functions"] == ["concat"]
    assert spec["canonical_ast"]["whens"][0]["when"]["op"] == "eq"
    assert lower_expression(spec, "sqlite").startswith("CASE WHEN")


def test_backend_pinned_function_requires_settings_and_supported_backend():
    expression = func.date_trunc("day", col("created_at"), "UTC")

    with pytest.raises(ExpressionError, match="recorded backend settings"):
        canonical_expression(expression, columns=["created_at"], backend="postgresql")
    with pytest.raises(ExpressionError, match="PostgreSQL"):
        canonical_expression(
            expression,
            columns=["created_at"],
            backend="sqlite",
            settings={"backend_version": "3.50"},
        )
    with pytest.raises(ExpressionError, match="explicit string literals"):
        canonical_expression(
            func.date_trunc(col("unit"), col("created_at"), "UTC"),
            columns=["unit", "created_at"],
            backend="postgresql",
            settings={"backend_version": "17"},
        )

    spec = canonical_expression(
        expression,
        columns=["created_at"],
        backend="postgresql",
        settings={
            "backend_version": "17",
            "timezone": "UTC",
            "collation": "C",
            "search_path": "public",
            "encoding": "UTF8",
        },
    )

    assert spec["determinism_class"] == "backend_pinned"
    assert spec["functions"] == ["date_trunc"]
    assert (
        lower_expression(spec, "postgresql")
        == "DATE_TRUNC('day', \"created_at\", 'UTC')"
    )
    for key in ("backend_version", "timezone", "collation", "search_path", "encoding"):
        incomplete = {
            **spec,
            "backend_settings": {
                k: v for k, v in spec["backend_settings"].items() if k != key
            },
        }
        with pytest.raises(ExpressionError, match=key):
            lower_expression(incomplete, "postgresql")


def test_ast_round_trip_preserves_canonical_form():
    original = canonical_expression(
        func.coalesce(col("display_name"), func.concat(col("first"), " ", col("last"))),
        columns=["display_name", "first", "last"],
    )

    assert (
        canonical_expression(original, columns=["display_name", "first", "last"])
        == original
    )
    assert (
        lower_expression(original["canonical_ast"], "sqlite")
        == original["normalized_sql"]
    )


def test_parameter_names_and_values_must_match_exactly():
    expression: Expr = col("id") == param("wanted", int)

    with pytest.raises(ExpressionError, match="Missing bound parameters"):
        canonical_expression(expression, columns=["id"])
    with pytest.raises(ExpressionError, match="Unknown bound parameters"):
        canonical_expression(
            expression, columns=["id"], parameters={"wanted": 1, "typo": 2}
        )
    with pytest.raises(ExpressionError, match="does not match"):
        canonical_expression(expression, columns=["id"], parameters={"wanted": "1"})
