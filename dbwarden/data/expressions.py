from __future__ import annotations

import json
import math
import re
import unicodedata
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any, NoReturn

from sqlalchemy.orm.attributes import InstrumentedAttribute
from sqlalchemy.sql import operators as sa_operators
from sqlalchemy.sql.elements import (
    BinaryExpression,
    BindParameter,
    BooleanClauseList,
    Case,
    Cast,
    ClauseElement,
    ColumnClause,
    False_,
    Grouping,
    Label,
    Null,
    TextClause,
    True_,
    UnaryExpression,
)
from sqlalchemy.sql.functions import FunctionElement
from sqlalchemy.sql.sqltypes import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    Float,
    Integer,
    Numeric,
    SmallInteger,
    String,
    Text,
    Time,
)
from sqlalchemy.sql.type_api import TypeEngine

EXPRESSION_LANGUAGE_VERSION = 1

_MISSING = object()
_BACKENDS = {"sqlite", "postgresql", "mysql", "mariadb", "clickhouse"}
_BINARY_SQL = {
    "add": "+",
    "sub": "-",
    "mul": "*",
    "div": "/",
    "mod": "%",
    "eq": "=",
    "ne": "<>",
    "lt": "<",
    "le": "<=",
    "gt": ">",
    "ge": ">=",
    "and": "AND",
    "or": "OR",
}
_SA_BINARY = {
    sa_operators.add: "add",
    sa_operators.sub: "sub",
    sa_operators.mul: "mul",
    sa_operators.truediv: "div",
    sa_operators.mod: "mod",
    sa_operators.eq: "eq",
    sa_operators.ne: "ne",
    sa_operators.lt: "lt",
    sa_operators.le: "le",
    sa_operators.gt: "gt",
    sa_operators.ge: "ge",
    sa_operators.and_: "and",
    sa_operators.or_: "or",
}
_FUNCTIONS = {
    "lower": (1, 1, "replayable"),
    "upper": (1, 1, "replayable"),
    "trim": (1, 1, "replayable"),
    "concat": (2, None, "replayable"),
    "coalesce": (2, None, "replayable"),
    "date_trunc": (3, 3, "backend_pinned"),
    "json_extract": (2, 2, "replayable"),
    "split_part": (3, 3, "replayable"),
}
_TYPE_PATTERN = re.compile(
    r"^(INTEGER|BIGINT|SMALLINT|FLOAT|REAL|DOUBLE|TEXT|STRING|BOOLEAN|BOOL|DATE|"
    r"DATETIME|TIMESTAMP|TIME|JSON|VARCHAR(?:\([1-9][0-9]*\))?|"
    r"(?:DECIMAL|NUMERIC)(?:\([1-9][0-9]*(?:,[0-9]+)?\))?)$"
)


class ExpressionError(ValueError):
    """Raised when an expression is outside language version 1."""


@dataclass(frozen=True, eq=False, slots=True)
class Expr:
    """Immutable expression-language version 1 node."""

    _kind: str
    _args: tuple[Any, ...]

    def _binary(self, op: str, other: Any) -> Expr:
        return Expr("binary", (op, self, _coerce(other)))

    def _reverse(self, op: str, other: Any) -> Expr:
        return Expr("binary", (op, _coerce(other), self))

    def __add__(self, other: Any) -> Expr:
        return self._binary("add", other)

    def __radd__(self, other: Any) -> Expr:
        return self._reverse("add", other)

    def __sub__(self, other: Any) -> Expr:
        return self._binary("sub", other)

    def __rsub__(self, other: Any) -> Expr:
        return self._reverse("sub", other)

    def __mul__(self, other: Any) -> Expr:
        return self._binary("mul", other)

    def __rmul__(self, other: Any) -> Expr:
        return self._reverse("mul", other)

    def __truediv__(self, other: Any) -> Expr:
        return self._binary("div", other)

    def __rtruediv__(self, other: Any) -> Expr:
        return self._reverse("div", other)

    def __mod__(self, other: Any) -> Expr:
        return self._binary("mod", other)

    def __rmod__(self, other: Any) -> Expr:
        return self._reverse("mod", other)

    def __eq__(self, other: object) -> Expr:  # type: ignore[override]
        if other is None:
            return self.is_(None)
        return self._binary("eq", other)

    def __ne__(self, other: object) -> Expr:  # type: ignore[override]
        if other is None:
            return self.is_not(None)
        return self._binary("ne", other)

    def __lt__(self, other: Any) -> Expr:
        return self._binary("lt", other)

    def __le__(self, other: Any) -> Expr:
        return self._binary("le", other)

    def __gt__(self, other: Any) -> Expr:
        return self._binary("gt", other)

    def __ge__(self, other: Any) -> Expr:
        return self._binary("ge", other)

    def __and__(self, other: Any) -> Expr:
        return self._binary("and", other)

    def __rand__(self, other: Any) -> Expr:
        return self._reverse("and", other)

    def __or__(self, other: Any) -> Expr:
        return self._binary("or", other)

    def __ror__(self, other: Any) -> Expr:
        return self._reverse("or", other)

    def __invert__(self) -> Expr:
        return Expr("unary", ("not", self))

    def __neg__(self) -> Expr:
        return Expr("unary", ("neg", self))

    def __bool__(self) -> NoReturn:
        raise TypeError(
            "Expressions cannot be used as Python booleans; use '&', '|' or '~'"
        )

    def is_(self, other: Any) -> Expr:
        value = _coerce(other)
        if value._kind != "literal" or value._args[0] not in (None, True, False):
            raise ExpressionError("is_ accepts only None, True, or False")
        return Expr("is", (False, self, value))

    def is_not(self, other: Any) -> Expr:
        value = _coerce(other)
        if value._kind != "literal" or value._args[0] not in (None, True, False):
            raise ExpressionError("is_not accepts only None, True, or False")
        return Expr("is", (True, self, value))

    def in_(self, values: Iterable[Any]) -> Expr:
        if isinstance(values, (str, bytes, Mapping, set, frozenset)):
            raise TypeError("in_ requires an ordered iterable of values")
        return Expr("in", (self, tuple(_coerce(value) for value in values)))

    def cast(self, type_: Any) -> Expr:
        return cast(self, type_)


class _FunctionNamespace:
    __slots__ = ()

    def __getattr__(self, name: str) -> Any:
        if name not in _FUNCTIONS:
            raise AttributeError(
                f"Function {name!r} is not in expression language version 1"
            )

        def call(*args: Any) -> Expr:
            minimum, maximum, _determinism = _FUNCTIONS[name]
            if len(args) < minimum or (maximum is not None and len(args) > maximum):
                expected = str(minimum) if minimum == maximum else f"at least {minimum}"
                raise TypeError(f"func.{name} expects {expected} arguments")
            return Expr("function", (name, tuple(_coerce(arg) for arg in args)))

        return call


func = _FunctionNamespace()


def col(name: str, table: str | None = None) -> Expr:
    if not isinstance(name, str) or not name or "\x00" in name:
        raise ExpressionError("Column name must be a non-empty string without NUL")
    if table is not None and (
        not isinstance(table, str) or not table or "\x00" in table
    ):
        raise ExpressionError("Table name must be a non-empty string without NUL")
    if table is None and "." in name:
        table, name = name.rsplit(".", 1)
    return Expr("column", (_text(name), _text(table) if table is not None else None))


def literal(value: Any) -> Expr:
    _encode_literal(value)
    return Expr("literal", (value,))


def param(name: str, type: Any = None) -> Expr:
    if not isinstance(name, str) or not name or "\x00" in name:
        raise ExpressionError("Parameter name must be a non-empty string without NUL")
    return Expr(
        "parameter", (_text(name), _normalize_type(type) if type is not None else None)
    )


def cast(value: Any, type_: Any) -> Expr:
    return Expr("cast", (_coerce(value), _normalize_type(type_)))


def case(*whens: tuple[Any, Any], else_: Any = _MISSING) -> Expr:
    if not whens:
        raise ExpressionError("case requires at least one branch")
    branches: list[tuple[Expr, Expr]] = []
    for branch in whens:
        if not isinstance(branch, tuple) or len(branch) != 2:
            raise TypeError("Each case branch must be a (condition, value) tuple")
        branches.append((_coerce(branch[0]), _coerce(branch[1])))
    return Expr(
        "case",
        (tuple(branches), _MISSING if else_ is _MISSING else _coerce(else_)),
    )


def mapping(value: Any, values: Mapping[Any, Any], *, else_: Any = _MISSING) -> Expr:
    if not isinstance(values, Mapping) or not values:
        raise ExpressionError("mapping requires at least one static entry")
    entries = [(literal(key), _coerce(result)) for key, result in values.items()]
    entries.sort(key=lambda entry: _stable_json(_encode_literal(entry[0]._args[0])))
    keys = [_stable_json(_to_ast(entry[0])) for entry in entries]
    if len(set(keys)) != len(keys):
        raise ExpressionError("mapping keys must be canonically unique")
    return Expr(
        "mapping",
        (
            _coerce(value),
            tuple(entries),
            _MISSING if else_ is _MISSING else _coerce(else_),
        ),
    )


map_values = mapping


def canonical_expression(
    value: Any,
    *,
    columns: Iterable[str],
    parameters: Mapping[str, Any] | None = None,
    backend: str = "sqlite",
    settings: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Validate and freeze an expression into canonical JSON-compatible data."""
    backend = _normalize_backend(backend)
    embedded_parameters: dict[str, Any] = {}
    if isinstance(value, Mapping):
        if "canonical_ast" in value:
            version = value.get("language_version")
            if version != EXPRESSION_LANGUAGE_VERSION:
                raise ExpressionError(
                    f"Unsupported expression language version: {version!r}"
                )
            expression = _from_ast(value["canonical_ast"])
            embedded_parameter_items = value.get("bound_parameters", [])
        elif "op" in value:
            expression = _from_ast(value)
        else:
            raise ExpressionError(
                "Expression dictionaries must be canonical specs or AST nodes"
            )
    else:
        expression = _coerce(value)

    expression = _normalize(_resolve_columns(expression, columns))
    refs, functions, parameter_types, total_domain_complete = _inspect(expression)
    if "embedded_parameter_items" in locals():
        embedded_parameters = _bound_parameter_values(
            embedded_parameter_items, parameter_types
        )
    supplied = dict(parameters) if parameters is not None else embedded_parameters
    missing = sorted(set(parameter_types) - set(supplied))
    extra = sorted(set(supplied) - set(parameter_types))
    if missing:
        raise ExpressionError(f"Missing bound parameters: {', '.join(missing)}")
    if extra:
        raise ExpressionError(f"Unknown bound parameters: {', '.join(extra)}")

    encoded_parameters = [
        {
            "name": name,
            "type": parameter_types[name],
            "value": _encode_parameter(parameter_types[name], supplied[name]),
        }
        for name in sorted(parameter_types)
    ]
    canonical_settings = _encode_settings(settings or {})
    pinned = any(_FUNCTIONS[name][2] == "backend_pinned" for name in functions)
    if "date_trunc" in functions and backend != "postgresql":
        raise ExpressionError("date_trunc is supported only by the PostgreSQL backend")
    if pinned:
        _validate_pinned_settings(canonical_settings)

    ast = _to_ast(expression)
    determinism = (
        "backend_pinned"
        if pinned
        else ("parameter_bound" if encoded_parameters else "replayable")
    )
    proof = {
        "kind": "versioned_whitelist",
        "language_version": EXPRESSION_LANGUAGE_VERSION,
        "backend_settings": canonical_settings if pinned else None,
    }
    bound_lookup = {
        item["name"]: _decode_literal(item["value"]) for item in encoded_parameters
    }
    normalized_sql = _lower(expression, backend, {}, bound_lookup)
    return {
        "language_version": EXPRESSION_LANGUAGE_VERSION,
        "canonical_ast": ast,
        "normalized_sql": normalized_sql,
        "referenced_columns": sorted(refs),
        "functions": sorted(functions),
        "bound_parameters": encoded_parameters,
        "determinism_class": determinism,
        "volatility_proof": proof,
        "backend_semantics_version": f"{backend}:1",
        "backend_settings": canonical_settings,
        "total_domain_complete": total_domain_complete,
    }


def lower_expression(
    spec_or_ast: Expr | Mapping[str, Any],
    backend: str,
    *,
    aliases: Mapping[str | None, str] | None = None,
) -> str:
    """Render a validated expression spec or version 1 AST as backend SQL."""
    backend = _normalize_backend(backend)
    parameters: dict[str, Any] = {}
    settings: Mapping[str, Any] = {}
    if isinstance(spec_or_ast, Expr):
        expression = spec_or_ast
    elif "canonical_ast" in spec_or_ast:
        version = spec_or_ast.get("language_version")
        if version != EXPRESSION_LANGUAGE_VERSION:
            raise ExpressionError(
                f"Unsupported expression language version: {version!r}"
            )
        expression = _from_ast(spec_or_ast["canonical_ast"])
        parameter_items = spec_or_ast.get("bound_parameters", [])
        settings = spec_or_ast.get("backend_settings") or {}
    elif "op" in spec_or_ast:
        expression = _from_ast(spec_or_ast)
    else:
        raise ExpressionError(
            "Expected Expr, canonical expression spec, or canonical AST"
        )

    _refs, functions, parameter_types, _total_domain_complete = _inspect(expression)
    if "parameter_items" in locals():
        parameters = _bound_parameter_values(parameter_items, parameter_types)
    missing = sorted(set(parameter_types) - set(parameters))
    if missing:
        raise ExpressionError(f"Missing bound parameters: {', '.join(missing)}")
    if "date_trunc" in functions and backend != "postgresql":
        raise ExpressionError("date_trunc is supported only by the PostgreSQL backend")
    if any(_FUNCTIONS[name][2] == "backend_pinned" for name in functions):
        _validate_pinned_settings(settings)

    safe_aliases: dict[str | None, str] = {}
    for source, alias in (aliases or {}).items():
        if source is not None and (not isinstance(source, str) or "\x00" in source):
            raise ExpressionError("Alias sources must be strings or None")
        if not isinstance(alias, str) or not alias or "\x00" in alias:
            raise ExpressionError("Alias values must be non-empty strings")
        safe_aliases[source] = alias
    return _lower(expression, backend, safe_aliases, parameters)


def _coerce(value: Any) -> Expr:
    if isinstance(value, Expr):
        return value
    if isinstance(value, InstrumentedAttribute):
        return _from_sqlalchemy(value.__clause_element__())
    if isinstance(value, ClauseElement):
        return _from_sqlalchemy(value)
    if callable(value):
        raise ExpressionError("Python callables are not expression values")
    return literal(value)


def _from_sqlalchemy(value: ClauseElement) -> Expr:
    if isinstance(value, (TextClause,)):
        raise ExpressionError("Raw SQLAlchemy text is not allowed")
    if isinstance(value, ColumnClause):
        if value.is_literal:
            raise ExpressionError("SQLAlchemy literal_column is not allowed")
        table = getattr(value, "table", None)
        table_name = None
        if table is not None:
            table_name = getattr(table, "fullname", None) or getattr(
                table, "name", None
            )
        return col(str(value.name), str(table_name) if table_name else None)
    if isinstance(value, BindParameter):
        return literal(value.value)
    if isinstance(value, Null):
        return literal(None)
    if isinstance(value, True_):
        return literal(True)
    if isinstance(value, False_):
        return literal(False)
    if isinstance(value, Grouping):
        return _from_sqlalchemy(value.element)
    if isinstance(value, Label):
        return _from_sqlalchemy(value.element)
    if isinstance(value, Cast):
        return cast(_from_sqlalchemy(value.clause), value.type)
    if isinstance(value, Case):
        compared = _from_sqlalchemy(value.value) if value.value is not None else None
        branches = tuple(
            (
                compared == _from_sqlalchemy(condition)
                if compared is not None
                else _from_sqlalchemy(condition),
                _from_sqlalchemy(result),
            )
            for condition, result in value.whens
        )
        else_value = _MISSING if value.else_ is None else _from_sqlalchemy(value.else_)
        return Expr("case", (branches, else_value))
    if isinstance(value, FunctionElement):
        name = str(value.name).lower()
        if name not in _FUNCTIONS:
            raise ExpressionError(f"SQLAlchemy function {name!r} is not allowed")
        return getattr(func, name)(*(_from_sqlalchemy(arg) for arg in value.clauses))
    if isinstance(value, BooleanClauseList):
        op = _SA_BINARY.get(value.operator)
        if op not in ("and", "or"):
            raise ExpressionError("Unsupported SQLAlchemy boolean operator")
        clauses = [_from_sqlalchemy(clause) for clause in value.clauses]
        if not clauses:
            raise ExpressionError("Empty SQLAlchemy boolean expression")
        result = clauses[0]
        for clause in clauses[1:]:
            result = Expr("binary", (op, result, clause))
        return result
    if isinstance(value, BinaryExpression):
        if value.operator in (sa_operators.in_op, sa_operators.not_in_op):
            bound = value.right
            if (
                not isinstance(bound, BindParameter)
                or not isinstance(bound.value, Iterable)
                or isinstance(bound.value, (str, bytes, Mapping))
            ):
                raise ExpressionError("SQLAlchemy IN requires a static value list")
            result = _from_sqlalchemy(value.left).in_(bound.value)
            return ~result if value.operator is sa_operators.not_in_op else result
        if value.operator in (sa_operators.is_, sa_operators.is_not):
            left = _from_sqlalchemy(value.left)
            right_expression = _from_sqlalchemy(value.right)
            return (
                left.is_not(right_expression._args[0])
                if value.operator is sa_operators.is_not
                else left.is_(right_expression._args[0])
            )
        if value.operator is sa_operators.concat_op:
            return func.concat(
                _from_sqlalchemy(value.left), _from_sqlalchemy(value.right)
            )
        op = _SA_BINARY.get(value.operator)
        if op is None:
            raise ExpressionError("Unsupported SQLAlchemy binary operator")
        return Expr(
            "binary", (op, _from_sqlalchemy(value.left), _from_sqlalchemy(value.right))
        )
    if isinstance(value, UnaryExpression) and value.operator is sa_operators.inv:
        return ~_from_sqlalchemy(value.element)
    raise ExpressionError(
        f"Unsupported SQLAlchemy expression node: {type(value).__name__}"
    )


def _resolve_columns(expression: Expr, columns: Iterable[str]) -> Expr:
    allowed: set[str] = set()
    for item in columns:
        if not isinstance(item, str) or not item or "\x00" in item:
            raise ExpressionError("columns must contain non-empty strings without NUL")
        allowed.add(_text(item))

    def resolve(node: Expr) -> Expr:
        if node._kind == "column":
            name, table = node._args
            reference = f"{table}.{name}" if table else name
            if reference in allowed:
                return node
            if table is None:
                matches = sorted(
                    candidate
                    for candidate in allowed
                    if candidate.rsplit(".", 1)[-1] == name
                )
                if len(matches) == 1:
                    return col(matches[0])
                if len(matches) > 1:
                    raise ExpressionError(f"Ambiguous column reference: {name}")
            raise ExpressionError(f"Unknown column reference: {reference}")
        return _map_children(node, resolve)

    return resolve(expression)


def _map_children(node: Expr, transform: Any) -> Expr:
    kind, args = node._kind, node._args
    if kind in ("literal", "column", "parameter"):
        return node
    if kind == "binary":
        return Expr(kind, (args[0], transform(args[1]), transform(args[2])))
    if kind in ("unary",):
        return Expr(kind, (args[0], transform(args[1])))
    if kind == "is":
        return Expr(kind, (args[0], transform(args[1]), transform(args[2])))
    if kind == "in":
        return Expr(
            kind, (transform(args[0]), tuple(transform(item) for item in args[1]))
        )
    if kind == "function":
        return Expr(kind, (args[0], tuple(transform(item) for item in args[1])))
    if kind == "cast":
        return Expr(kind, (transform(args[0]), args[1]))
    if kind == "case":
        branches = tuple((transform(a), transform(b)) for a, b in args[0])
        return Expr(
            kind, (branches, _MISSING if args[1] is _MISSING else transform(args[1]))
        )
    if kind == "mapping":
        entries = tuple((transform(a), transform(b)) for a, b in args[1])
        return Expr(
            kind,
            (
                transform(args[0]),
                entries,
                _MISSING if args[2] is _MISSING else transform(args[2]),
            ),
        )
    raise ExpressionError(f"Unknown expression node: {kind}")


def _normalize(expression: Expr) -> Expr:
    node = _map_children(expression, _normalize)
    if node._kind == "binary" and node._args[0] in ("and", "or"):
        operator_name = node._args[0]
        operands: list[Expr] = []

        def collect(item: Expr) -> None:
            if item._kind == "binary" and item._args[0] == operator_name:
                collect(item._args[1])
                collect(item._args[2])
            else:
                operands.append(item)

        collect(node)
        operands.sort(key=lambda item: _stable_json(_to_ast(item)))
        result = operands[0]
        for operand in operands[1:]:
            result = Expr("binary", (operator_name, result, operand))
        return result
    if node._kind == "mapping":
        source, entries, fallback = node._args
        ordered = tuple(
            sorted(entries, key=lambda entry: _stable_json(_to_ast(entry[0])))
        )
        return Expr("mapping", (source, ordered, fallback))
    return node


def _inspect(
    expression: Expr,
) -> tuple[set[str], set[str], dict[str, str | None], bool]:
    refs: set[str] = set()
    functions: set[str] = set()
    parameters: dict[str, str | None] = {}
    complete = True

    def visit(node: Expr) -> Expr:
        nonlocal complete
        if node._kind == "column":
            name, table = node._args
            refs.add(f"{table}.{name}" if table else name)
        elif node._kind == "parameter":
            name, type_name = node._args
            if name in parameters and parameters[name] != type_name:
                raise ExpressionError(f"Parameter {name!r} has conflicting types")
            parameters[name] = type_name
        elif node._kind == "function":
            name, arguments = node._args
            if name not in _FUNCTIONS:
                raise ExpressionError(f"Function {name!r} is not allowed")
            minimum, maximum, _determinism = _FUNCTIONS[name]
            if len(arguments) < minimum or (
                maximum is not None and len(arguments) > maximum
            ):
                raise ExpressionError(f"Function {name!r} has invalid arity")
            if name == "date_trunc" and any(
                argument._kind != "literal" or not isinstance(argument._args[0], str)
                for argument in (arguments[0], arguments[2])
            ):
                raise ExpressionError(
                    "date_trunc unit and timezone must be explicit string literals"
                )
            if name == "json_extract":
                path = arguments[1]
                if (
                    path._kind != "literal"
                    or not isinstance(path._args[0], str)
                    or not path._args[0].startswith("$")
                ):
                    raise ExpressionError(
                        "json_extract path must be a literal JSON path beginning with '$'"
                    )
            if name == "split_part":
                delimiter, index = arguments[1:]
                if (
                    delimiter._kind != "literal"
                    or not isinstance(delimiter._args[0], str)
                    or not delimiter._args[0]
                    or index._kind != "literal"
                    or type(index._args[0]) is not int
                    or not 1 <= index._args[0] <= 64
                ):
                    raise ExpressionError(
                        "split_part requires a nonempty literal delimiter and literal index from 1 to 64"
                    )
            functions.add(name)
        elif node._kind == "binary" and node._args[0] not in _BINARY_SQL:
            raise ExpressionError(f"Operator {node._args[0]!r} is not allowed")
        elif node._kind == "unary" and node._args[0] not in ("not", "neg"):
            raise ExpressionError(f"Unary operator {node._args[0]!r} is not allowed")
        elif (
            node._kind == "case"
            and node._args[1] is _MISSING
            or node._kind == "mapping"
            and node._args[2] is _MISSING
        ):
            complete = False
        return _map_children(node, visit)

    visit(expression)
    return refs, functions, parameters, complete


def _to_ast(node: Expr) -> dict[str, Any]:
    kind, args = node._kind, node._args
    if kind == "column":
        return {"op": "column", "name": args[0], "table": args[1]}
    if kind == "literal":
        return {"op": "literal", "value": _encode_literal(args[0])}
    if kind == "parameter":
        return {"op": "parameter", "name": args[0], "type": args[1]}
    if kind == "binary":
        return {"op": args[0], "left": _to_ast(args[1]), "right": _to_ast(args[2])}
    if kind == "unary":
        return {"op": args[0], "value": _to_ast(args[1])}
    if kind == "is":
        return {
            "op": "is_not" if args[0] else "is",
            "left": _to_ast(args[1]),
            "right": _to_ast(args[2]),
        }
    if kind == "in":
        return {
            "op": "in",
            "value": _to_ast(args[0]),
            "values": [_to_ast(item) for item in args[1]],
        }
    if kind == "function":
        return {
            "op": "function",
            "name": args[0],
            "args": [_to_ast(item) for item in args[1]],
        }
    if kind == "cast":
        return {"op": "cast", "value": _to_ast(args[0]), "type": args[1]}
    if kind == "case":
        return {
            "op": "case",
            "whens": [{"when": _to_ast(a), "then": _to_ast(b)} for a, b in args[0]],
            "has_else": args[1] is not _MISSING,
            "else": None if args[1] is _MISSING else _to_ast(args[1]),
        }
    if kind == "mapping":
        return {
            "op": "mapping",
            "value": _to_ast(args[0]),
            "entries": [{"key": _to_ast(a), "value": _to_ast(b)} for a, b in args[1]],
            "has_else": args[2] is not _MISSING,
            "else": None if args[2] is _MISSING else _to_ast(args[2]),
        }
    raise ExpressionError(f"Unknown expression node: {kind}")


def _from_ast(ast: Any) -> Expr:
    if not isinstance(ast, Mapping) or not isinstance(ast.get("op"), str):
        raise ExpressionError("Canonical AST nodes must be objects with an op")
    op = ast["op"]
    if op == "column":
        return col(_required_string(ast, "name"), ast.get("table"))
    if op == "literal":
        if "value" not in ast:
            raise ExpressionError("literal AST requires value")
        return literal(_decode_literal(ast["value"]))
    if op == "parameter":
        return param(_required_string(ast, "name"), ast.get("type"))
    if op in _BINARY_SQL:
        left, right = _from_ast(ast.get("left")), _from_ast(ast.get("right"))
        if op in {"eq", "ne"} and right._kind == "literal" and right._args[0] is None:
            return left.is_not(None) if op == "ne" else left.is_(None)
        if op in {"eq", "ne"} and left._kind == "literal" and left._args[0] is None:
            return right.is_not(None) if op == "ne" else right.is_(None)
        return Expr("binary", (op, left, right))
    if op in ("not", "neg"):
        return Expr("unary", (op, _from_ast(ast.get("value"))))
    if op in ("is", "is_not"):
        left, right = _from_ast(ast.get("left")), _from_ast(ast.get("right"))
        return (
            left.is_not(right._args[0]) if op == "is_not" else left.is_(right._args[0])
        )
    if op == "in":
        values = ast.get("values")
        if not isinstance(values, list):
            raise ExpressionError("in AST requires a values list")
        return Expr(
            "in",
            (_from_ast(ast.get("value")), tuple(_from_ast(item) for item in values)),
        )
    if op == "function":
        name = _required_string(ast, "name")
        args = ast.get("args")
        if name not in _FUNCTIONS or not isinstance(args, list):
            raise ExpressionError(f"Function {name!r} is not allowed")
        return getattr(func, name)(*(_from_ast(item) for item in args))
    if op == "cast":
        return cast(_from_ast(ast.get("value")), ast.get("type"))
    if op == "case":
        whens = ast.get("whens")
        if not isinstance(whens, list) or not whens:
            raise ExpressionError("case AST requires branches")
        branches = []
        for branch in whens:
            if not isinstance(branch, Mapping):
                raise ExpressionError("case branch must be an object")
            branches.append(
                (_from_ast(branch.get("when")), _from_ast(branch.get("then")))
            )
        else_value = (
            _from_ast(ast.get("else")) if ast.get("has_else") is True else _MISSING
        )
        return Expr("case", (tuple(branches), else_value))
    if op == "mapping":
        entries = ast.get("entries")
        if not isinstance(entries, list) or not entries:
            raise ExpressionError("mapping AST requires entries")
        parsed = []
        for entry in entries:
            if not isinstance(entry, Mapping):
                raise ExpressionError("mapping entry must be an object")
            key = _from_ast(entry.get("key"))
            if key._kind != "literal":
                raise ExpressionError("mapping keys must be literals")
            parsed.append((key, _from_ast(entry.get("value"))))
        keys = [_stable_json(_to_ast(key)) for key, _value in parsed]
        if len(set(keys)) != len(keys):
            raise ExpressionError("mapping keys must be canonically unique")
        else_value = (
            _from_ast(ast.get("else")) if ast.get("has_else") is True else _MISSING
        )
        return Expr("mapping", (_from_ast(ast.get("value")), tuple(parsed), else_value))
    raise ExpressionError(f"Unknown expression op: {op!r}")


def _lower(
    node: Expr,
    backend: str,
    aliases: Mapping[str | None, str],
    parameters: Mapping[str, Any],
) -> str:
    kind, args = node._kind, node._args
    if kind == "column":
        name, table = args
        if table:
            table = aliases.get(table, table)
            qualified_table = ".".join(
                _quote_identifier(part, backend) for part in table.split(".")
            )
            return f"{qualified_table}.{_quote_identifier(name, backend)}"
        fallback = aliases.get("") or aliases.get(None)
        if fallback:
            return f"{_quote_identifier(fallback, backend)}.{_quote_identifier(name, backend)}"
        return _quote_identifier(name, backend)
    if kind == "literal":
        return _sql_literal(args[0], backend)
    if kind == "parameter":
        if args[0] not in parameters:
            raise ExpressionError(f"Missing bound parameter: {args[0]}")
        return _sql_literal(parameters[args[0]], backend)
    if kind == "binary":
        return f"({_lower(args[1], backend, aliases, parameters)} {_BINARY_SQL[args[0]]} {_lower(args[2], backend, aliases, parameters)})"
    if kind == "unary":
        prefix = "NOT " if args[0] == "not" else "-"
        return f"({prefix}{_lower(args[1], backend, aliases, parameters)})"
    if kind == "is":
        keyword = "IS NOT" if args[0] else "IS"
        return f"({_lower(args[1], backend, aliases, parameters)} {keyword} {_lower(args[2], backend, aliases, parameters)})"
    if kind == "in":
        if not args[1]:
            return "(1 = 0)"
        values = ", ".join(
            _lower(item, backend, aliases, parameters) for item in args[1]
        )
        return f"({_lower(args[0], backend, aliases, parameters)} IN ({values}))"
    if kind == "function":
        name, function_args = args
        lowered = [_lower(item, backend, aliases, parameters) for item in function_args]
        if name == "concat" and backend in ("sqlite", "postgresql"):
            return "(" + " || ".join(lowered) + ")"
        if name == "json_extract":
            return _json_extract_sql(lowered[0], lowered[1], backend)
        if name == "split_part":
            return _split_part_sql(
                lowered[0], lowered[1], function_args[2]._args[0], backend
            )
        return f"{name.upper()}({', '.join(lowered)})"
    if kind == "cast":
        return f"CAST({_lower(args[0], backend, aliases, parameters)} AS {_lower_type(args[1], backend)})"
    if kind == "case":
        clauses = " ".join(
            f"WHEN {_lower(condition, backend, aliases, parameters)} THEN {_lower(result, backend, aliases, parameters)}"
            for condition, result in args[0]
        )
        fallback = (
            ""
            if args[1] is _MISSING
            else f" ELSE {_lower(args[1], backend, aliases, parameters)}"
        )
        return f"CASE {clauses}{fallback} END"
    if kind == "mapping":
        source = _lower(args[0], backend, aliases, parameters)
        clauses = " ".join(
            f"WHEN {_lower(key, backend, aliases, parameters)} THEN {_lower(result, backend, aliases, parameters)}"
            for key, result in args[1]
        )
        fallback = (
            ""
            if args[2] is _MISSING
            else f" ELSE {_lower(args[2], backend, aliases, parameters)}"
        )
        return f"CASE {source} {clauses}{fallback} END"
    raise ExpressionError(f"Unknown expression node: {kind}")


def _encode_literal(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ExpressionError("Non-finite floating-point literals are not allowed")
        return 0.0 if value == 0 else value
    if isinstance(value, str):
        if "\x00" in value:
            raise ExpressionError("String literals cannot contain NUL")
        return _text(value)
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise ExpressionError("Non-finite decimal literals are not allowed")
        return {"$type": "decimal", "value": _decimal_text(value)}
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ExpressionError("Datetime literals must be timezone-aware")
        normalized = value.astimezone(timezone.utc).replace(microsecond=0)
        return {
            "$type": "datetime",
            "value": normalized.isoformat().replace("+00:00", "Z"),
        }
    if isinstance(value, date):
        return {"$type": "date", "value": value.isoformat()}
    raise ExpressionError(f"Unsupported literal type: {type(value).__name__}")


def _encode_parameter(type_name: str | None, value: Any) -> Any:
    if value is not None and type_name is not None:
        base = type_name.split("(", 1)[0]
        valid = {
            "INTEGER": isinstance(value, int) and not isinstance(value, bool),
            "BIGINT": isinstance(value, int) and not isinstance(value, bool),
            "SMALLINT": isinstance(value, int) and not isinstance(value, bool),
            "FLOAT": isinstance(value, (int, float)) and not isinstance(value, bool),
            "REAL": isinstance(value, (int, float)) and not isinstance(value, bool),
            "DOUBLE": isinstance(value, (int, float)) and not isinstance(value, bool),
            "DECIMAL": isinstance(value, (int, Decimal))
            and not isinstance(value, bool),
            "NUMERIC": isinstance(value, (int, Decimal))
            and not isinstance(value, bool),
            "BOOLEAN": isinstance(value, bool),
            "TEXT": isinstance(value, str),
            "VARCHAR": isinstance(value, str),
            "DATE": isinstance(value, date) and not isinstance(value, datetime),
            "DATETIME": isinstance(value, datetime),
            "TIMESTAMP": isinstance(value, datetime),
            "TIME": False,
            "JSON": False,
        }.get(base, False)
        if not valid:
            raise ExpressionError(
                f"Bound parameter value does not match declared type {type_name}"
            )
    return _encode_literal(value)


def _decode_literal(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, str)):
        return _text(value) if isinstance(value, str) else value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ExpressionError("Non-finite floating-point literals are not allowed")
        return 0.0 if value == 0 else value
    if not isinstance(value, Mapping) or set(value) != {"$type", "value"}:
        raise ExpressionError("Invalid tagged literal")
    tag, raw = value["$type"], value["value"]
    if not isinstance(raw, str):
        raise ExpressionError("Tagged literal value must be a string")
    try:
        if tag == "decimal":
            decoded_decimal = Decimal(raw)
            if not decoded_decimal.is_finite():
                raise ExpressionError("Non-finite decimal literals are not allowed")
            return decoded_decimal
        if tag == "datetime":
            decoded_datetime = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            if decoded_datetime.tzinfo is None:
                raise ValueError
            return decoded_datetime
        if tag == "date":
            return date.fromisoformat(raw)
    except (ValueError, ArithmeticError) as exc:
        raise ExpressionError(f"Invalid {tag!r} literal") from exc
    raise ExpressionError(f"Unknown tagged literal type: {tag!r}")


def _sql_literal(value: Any, backend: str) -> str:
    encoded = _encode_literal(value)
    if encoded is None:
        return "NULL"
    if encoded is True:
        return "TRUE"
    if encoded is False:
        return "FALSE"
    if isinstance(encoded, int):
        return str(encoded)
    if isinstance(encoded, float):
        return repr(encoded)
    if isinstance(encoded, str):
        if "\\" in encoded or ":" in encoded:
            hex_value = encoded.encode("utf-8").hex()
            if backend in {"mysql", "mariadb"}:
                return f"CONVERT(X'{hex_value}' USING utf8mb4)"
            if backend == "postgresql":
                return f"convert_from(decode('{hex_value}', 'hex'), 'UTF8')"
            if backend == "clickhouse":
                return f"unhex('{hex_value}')"
            return f"CAST(X'{hex_value}' AS TEXT)"
        return "'" + encoded.replace("'", "''") + "'"
    tag = encoded["$type"]
    raw = encoded["value"]
    if tag == "decimal":
        return raw
    escaped = raw.replace("'", "''")
    if backend == "postgresql":
        return f"{'TIMESTAMPTZ' if tag == 'datetime' else 'DATE'} '{escaped}'"
    return f"'{escaped}'"


def _split_part_sql(source: str, delimiter: str, index: int, backend: str) -> str:
    if backend == "postgresql":
        return f"SPLIT_PART({source}, {delimiter}, {index})"
    if backend in {"mysql", "mariadb"}:
        parts = (
            f"(1 + ((CHAR_LENGTH({source}) - CHAR_LENGTH(REPLACE({source}, {delimiter}, ''))) "
            f"/ CHAR_LENGTH({delimiter})))"
        )
        return (
            f"CASE WHEN {source} IS NULL THEN NULL WHEN {index} <= {parts} THEN "
            f"SUBSTRING_INDEX(SUBSTRING_INDEX({source}, {delimiter}, {index}), {delimiter}, -1) "
            "ELSE '' END"
        )
    if backend == "clickhouse":
        return (
            f"if(isNull({source}), NULL, "
            f"arrayElement(splitByString({delimiter}, {source}), {index}))"
        )
    position = f"INSTR(rest, {delimiter})"
    split = (
        "(WITH RECURSIVE dbw_split(part, rest, n) AS ("
        f"SELECT CASE WHEN INSTR({source}, {delimiter}) = 0 THEN {source} "
        f"ELSE SUBSTR({source}, 1, INSTR({source}, {delimiter}) - 1) END, "
        f"CASE WHEN INSTR({source}, {delimiter}) = 0 THEN NULL "
        f"ELSE SUBSTR({source}, INSTR({source}, {delimiter}) + LENGTH({delimiter})) END, 1 "
        "UNION ALL SELECT "
        f"CASE WHEN {position} = 0 THEN rest ELSE SUBSTR(rest, 1, {position} - 1) END, "
        f"CASE WHEN {position} = 0 THEN NULL ELSE SUBSTR(rest, {position} + LENGTH({delimiter})) END, n + 1 "
        f"FROM dbw_split WHERE rest IS NOT NULL AND n < {index}) "
        f"SELECT COALESCE((SELECT part FROM dbw_split WHERE n = {index}), ''))"
    )
    return f"CASE WHEN {source} IS NULL THEN NULL ELSE {split} END"


def _json_extract_sql(source: str, path: str, backend: str) -> str:
    if backend == "postgresql":
        value = (
            f"jsonb_path_query_first(CAST({source} AS JSONB), CAST({path} AS JSONPATH))"
        )
        scalar = f"({value} #>> '{{}}')"
        return (
            f"CASE jsonb_typeof({value}) "
            f"WHEN 'boolean' THEN LOWER({scalar}) "
            f"WHEN 'string' THEN {scalar} WHEN 'number' THEN {scalar} ELSE NULL END"
        )
    if backend in {"mysql", "mariadb"}:
        value = f"JSON_EXTRACT({source}, {path})"
        scalar = f"JSON_UNQUOTE({value})"
        return (
            f"CASE JSON_TYPE({value}) WHEN 'BOOLEAN' THEN LOWER({scalar}) "
            f"WHEN 'STRING' THEN {scalar} WHEN 'INTEGER' THEN {scalar} "
            f"WHEN 'DOUBLE' THEN {scalar} WHEN 'DECIMAL' THEN {scalar} ELSE NULL END"
        )
    if backend == "clickhouse":
        raise ExpressionError(
            "json_extract is unsupported on ClickHouse because JSON_VALUE cannot "
            "distinguish a missing value from an empty string"
        )
    type_sql = f"JSON_TYPE({source}, {path})"
    value = f"CAST(JSON_EXTRACT({source}, {path}) AS TEXT)"
    return (
        f"CASE {type_sql} WHEN 'true' THEN 'true' WHEN 'false' THEN 'false' "
        f"WHEN 'text' THEN {value} WHEN 'integer' THEN {value} "
        f"WHEN 'real' THEN {value} ELSE NULL END"
    )


def _normalize_type(type_: Any) -> str:
    if isinstance(type_, type) and issubclass(type_, TypeEngine):
        type_ = type_()
    if isinstance(type_, TypeEngine):
        if isinstance(type_, Text):
            return "TEXT"
        if isinstance(type_, String):
            return f"VARCHAR({type_.length})" if type_.length else "TEXT"
        if isinstance(type_, BigInteger):
            return "BIGINT"
        if isinstance(type_, SmallInteger):
            return "SMALLINT"
        if isinstance(type_, Integer):
            return "INTEGER"
        if isinstance(type_, Numeric):
            if type_.precision is None:
                return "DECIMAL"
            return f"DECIMAL({type_.precision},{type_.scale or 0})"
        if isinstance(type_, Float):
            return "FLOAT"
        if isinstance(type_, Boolean):
            return "BOOLEAN"
        if isinstance(type_, DateTime):
            return "TIMESTAMP"
        if isinstance(type_, Date):
            return "DATE"
        if isinstance(type_, Time):
            return "TIME"
        raise ExpressionError(
            f"Unsupported SQLAlchemy cast type: {type(type_).__name__}"
        )
    if type_ is str:
        return "TEXT"
    if type_ is int:
        return "INTEGER"
    if type_ is float:
        return "FLOAT"
    if type_ is bool:
        return "BOOLEAN"
    if type_ is Decimal:
        return "DECIMAL"
    if type_ is date:
        return "DATE"
    if type_ is datetime:
        return "TIMESTAMP"
    if not isinstance(type_, str):
        raise ExpressionError(f"Unsupported cast type: {type(type_).__name__}")
    normalized = re.sub(r"\s+", "", type_.upper())
    if not _TYPE_PATTERN.fullmatch(normalized):
        raise ExpressionError(f"Unsupported or unsafe cast type: {type_!r}")
    aliases = {"STRING": "TEXT", "BOOL": "BOOLEAN", "NUMERIC": "DECIMAL"}
    return aliases.get(normalized, normalized)


def _lower_type(type_name: str, backend: str) -> str:
    if backend in {"mysql", "mariadb"}:
        base = type_name.split("(", 1)[0]
        if base in ("INTEGER", "BIGINT", "SMALLINT"):
            return "SIGNED"
        if base in ("TEXT", "STRING", "VARCHAR"):
            return "CHAR" + (
                type_name[len("VARCHAR") :] if type_name.startswith("VARCHAR(") else ""
            )
        if base in ("BOOLEAN", "BOOL"):
            return "UNSIGNED"
        if base == "JSON" and backend == "mariadb":
            return "CHAR"
    if backend == "sqlite":
        base = type_name.split("(", 1)[0]
        if base in ("BOOLEAN", "INTEGER", "BIGINT", "SMALLINT"):
            return "INTEGER"
        if base in ("FLOAT", "REAL", "DOUBLE", "DECIMAL", "NUMERIC"):
            return "REAL"
        if base in (
            "DATE",
            "DATETIME",
            "TIMESTAMP",
            "TIME",
            "TEXT",
            "VARCHAR",
            "STRING",
            "JSON",
        ):
            return "TEXT"
    return type_name


def _normalize_backend(backend: str) -> str:
    if not isinstance(backend, str):
        raise ExpressionError("Expression backend must be a string")
    normalized = backend.lower()
    if normalized == "postgres":
        normalized = "postgresql"
    if normalized not in _BACKENDS:
        raise ExpressionError(f"Unsupported expression backend: {backend!r}")
    return normalized


def _quote_identifier(name: str, backend: str) -> str:
    quote = "`" if backend in ("mysql", "mariadb", "clickhouse") else '"'
    escaped = name.replace(quote, quote + quote)
    return f"{quote}{escaped}{quote}"


def _bound_parameter_values(
    items: Any, expected: Mapping[str, str | None] | None = None
) -> dict[str, Any]:
    if not isinstance(items, list):
        raise ExpressionError("bound_parameters must be a list")
    result: dict[str, Any] = {}
    for item in items:
        if not isinstance(item, Mapping) or set(item) != {"name", "type", "value"}:
            raise ExpressionError("Invalid bound parameter entry")
        name = _required_string(item, "name")
        if name in result:
            raise ExpressionError(f"Duplicate bound parameter: {name}")
        type_name = item["type"]
        if type_name is not None:
            type_name = _normalize_type(type_name)
        if expected is not None and expected.get(name, object()) != type_name:
            raise ExpressionError(f"Bound parameter type does not match AST: {name}")
        decoded = _decode_literal(item["value"])
        _encode_parameter(type_name, decoded)
        result[name] = decoded
    if expected is not None:
        extra = sorted(set(result) - set(expected))
        if extra:
            raise ExpressionError(f"Unknown bound parameters: {', '.join(extra)}")
    return result


def _expression_parameter_names(value: Any) -> set[str]:
    if isinstance(value, Mapping):
        expression = _from_ast(value.get("canonical_ast", value))
    else:
        expression = _coerce(value)
    return set(_inspect(expression)[2])


def _validate_pinned_settings(settings: Mapping[str, Any]) -> None:
    required = {"backend_version", "timezone", "collation", "search_path", "encoding"}
    missing = sorted(
        key
        for key in required
        if not isinstance(settings.get(key), str) or not settings[key]
    )
    if missing:
        raise ExpressionError(
            "backend_pinned functions require recorded backend settings: "
            + ", ".join(missing)
        )


def _encode_settings(settings: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(settings, Mapping):
        raise ExpressionError("settings must be a mapping")

    def encode(value: Any) -> Any:
        if isinstance(value, Mapping):
            if not all(isinstance(key, str) for key in value):
                raise ExpressionError("Backend setting keys must be strings")
            return {key: encode(value[key]) for key in sorted(value)}
        if isinstance(value, (list, tuple)):
            return [encode(item) for item in value]
        return _encode_literal(value)

    return {key: encode(settings[key]) for key in sorted(settings)}


def _decimal_text(value: Decimal) -> str:
    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return "0" if text in ("-0", "") else text


def _required_string(value: Mapping[str, Any], key: str) -> str:
    result = value.get(key)
    if not isinstance(result, str) or not result or "\x00" in result:
        raise ExpressionError(f"{key} must be a non-empty string without NUL")
    return _text(result)


def _text(value: str) -> str:
    return unicodedata.normalize("NFC", value)


def _stable_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
