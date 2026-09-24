import os

import pytest
from sqlalchemy import create_engine

from dbwarden.data import func, literal
from dbwarden.data.expressions import canonical_expression, lower_expression
from dbwarden.data.sql import statement_text


@pytest.fixture(params=["sqlite", "postgresql", "mariadb", "mysql"])
def expression_connection(request):
    backend = request.param
    variable = "POSTGRES" if backend == "postgresql" else backend.upper()
    url = (
        "sqlite://"
        if backend == "sqlite"
        else os.environ.get("DBWARDEN_DATA_TEST_" + variable)
    )
    if not url:
        pytest.skip(f"Set DBWARDEN_DATA_TEST_{variable} for native expression tests")
    engine = create_engine(url)
    with engine.connect() as connection:
        yield backend, connection
    engine.dispose()


def _value(context, expression):
    backend, connection = context
    spec = canonical_expression(expression, columns=[], backend=backend)
    return connection.execute(
        statement_text("SELECT " + lower_expression(spec, backend))
    ).scalar_one()


def test_json_scalars_have_same_null_boolean_and_text_semantics(expression_connection):
    for payload, expected in [
        ('{"value":true}', "true"),
        ('{"value":false}', "false"),
        ('{"value":null}', None),
        ('{"value":[]}', None),
        ('{"value":{}}', None),
        ('{"missing":1}', None),
        ('{"value":""}', ""),
        ('{"value":-12}', "-12"),
        ('{"value":"a\\\\b"}', "a\\b"),
    ]:
        assert (
            _value(
                expression_connection, func.json_extract(literal(payload), "$.value")
            )
            == expected
        )


def test_split_part_preserves_null_empty_and_unicode_fields(expression_connection):
    for source, index, expected in [
        (None, 1, None),
        ("", 1, ""),
        ("a/", 2, ""),
        ("a/b", 3, ""),
        ("a/é/c", 2, "é"),
    ]:
        assert (
            _value(expression_connection, func.split_part(literal(source), "/", index))
            == expected
        )


def test_escaped_literals_round_trip_without_sql_mode_assumptions(
    expression_connection,
):
    for value in ["folder\\file", "a\\'b", "a'quote", "é\\雪", ":value", "50%"]:
        assert _value(expression_connection, literal(value)) == value


def test_literal_colons_do_not_consume_real_bound_parameters(expression_connection):
    _, connection = expression_connection
    assert tuple(
        connection.execute(
            statement_text("SELECT ':value', :value"), {"value": 7}
        ).one()
    ) == (":value", 7)
