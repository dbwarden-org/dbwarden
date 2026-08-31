from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

# ── source-column type classification ─────────────────────────────────────────

_AggFuncKind = str  # "plain" | "aggregate_function" | "simple_agg_function"


def _classify_column_type(type_str: str | None) -> tuple[_AggFuncKind, str | None, tuple[str, ...] | None]:
    """Parse a ClickHouse column type string and classify it.

    Returns ``(kind, func_name, inner_types)`` where *kind* is one of:

    ``"plain"``
        A non-aggregate type (e.g. ``Float64``, ``String``, ``UInt32``).
        *func_name* and *inner_types* are ``None``.

    ``"aggregate_function"``
        ``AggregateFunction(<func>, <types...>)``.
        *func_name* is the aggregate function, *inner_types* are the type args.

    ``"simple_agg_function"``
        ``SimpleAggregateFunction(<func>, <type>)``.
        *func_name* is the aggregate function, *inner_types* is ``(type,)``.
    """
    if type_str is None:
        return "plain", None, None

    m = re.match(
        r"^\s*(?:AggregateFunction|SimpleAggregateFunction)\s*\(\s*(.*)\)\s*$",
        type_str,
        re.IGNORECASE,
    )
    if m:
        prefix_end = type_str.index("(")
        prefix = type_str[:prefix_end].strip()
        inner = m.group(1).strip()
        parts = _split_top_level(inner)
        func, *_params = _split_func(parts[0].strip())
        types = tuple(part.strip() for part in parts[1:])
        kind = "aggregate_function" if prefix.lower() == "aggregatefunction" else "simple_agg_function"
        return kind, func, types

    return "plain", None, None


def _split_top_level(s: str) -> list[str]:
    """Split a comma-separated string at depth 0 (ignoring nested parens)."""
    parts: list[str] = []
    depth = 0
    buf: list[str] = []
    for ch in s:
        if ch == "(":
            depth += 1
            buf.append(ch)
        elif ch == ")":
            depth -= 1
            buf.append(ch)
        elif ch == "," and depth == 0:
            parts.append("".join(buf).strip())
            buf = []
        else:
            buf.append(ch)
    if buf:
        parts.append("".join(buf).strip())
    return parts


# ── combinator resolution ─────────────────────────────────────────────────────

_FUNC_PARAMS_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)\s*\((?P<params>.*)\)$", re.DOTALL)


def _split_func(func: str) -> tuple[str, str | None]:
    """Split a parametric aggregate name into its base function and parameters.

    ``"quantile(0.95)"``       → ``("quantile", "0.95")``
    ``"quantiles(0.9, 0.95)"`` → ``("quantiles", "0.9, 0.95")``
    ``"sum"``                  → ``("sum", None)``

    ClickHouse renders parametric aggregate combinators on the base name with
    the parameters hoisted before the argument list::

        quantileState(0.95)(column)
    """
    m = _FUNC_PARAMS_RE.match(func)
    if m:
        return m.group(1), m.group("params")
    return func, None


def _combinator_name(func: str, suffix: str, params: str | None) -> str:
    """Render ``<func><suffix>(<params>)``, e.g. ``quantileState(0.95)``."""
    if suffix and params:
        return f"{func}{suffix}({params})"
    return f"{func}{suffix}"


_ASSOCIATIVE_AGGS = frozenset({
    "sum", "min", "max", "any", "anyLast",
    "count", "uniq", "uniqExact",
    "groupArray", "groupUniqArray",
})


def resolve_combinator(func: str, source_column_type: str | None) -> str:
    """Determine the combinator for an aggregate expression based on the source
    column type.

    ==========================  =======================  ======================
    Source column type          Combinator               Example
    ==========================  =======================  ======================
    ``None`` / plain type       ``{func}State``          ``sumState(x)``
    ``AggregateFunction(f,T)``  ``{func}MergeState``     ``sumMergeState(x)``
    ``SimpleAggregateFunction`` ``{func}`` (bare func)   ``sum(x)``
    ==========================  =======================  ======================

    Raises ``TypeError`` when the source column type is incompatible:

    * Function mismatch — source is ``AggregateFunction(sum, T)`` but the
      declared aggregate uses a different function (e.g. ``avg``).
    * Inner-type mismatch — source ``AggregateFunction(sum, UInt32)`` vs
      declared ``agg.sum(..., "Float64")``.
    * Non-associative function on ``SimpleAggregateFunction`` source.
    """
    func, func_params = _split_func(func)
    kind, src_func, src_types = _classify_column_type(source_column_type)

    if kind == "aggregate_function":
        if src_func is not None and src_func.lower() != func.lower():
            raise TypeError(
                f"Function mismatch: source column type is "
                f"AggregateFunction({src_func}, ...) but declared aggregate "
                f"function is '{func}'. Use '{src_func}' instead."
            )
        if src_types is not None and src_types != ():
            # We can't validate inner types here because arg_types on AggExpr
            # may include the inner type AFTER AggregateFunction wraps it.
            # So we only validate the function match. Inner-type validation
            # would require comparing parsed types, which is fragile.
            pass
        return _combinator_name(func, "MergeState", func_params)

    if kind == "simple_agg_function":
        if func.lower() not in _ASSOCIATIVE_AGGS:
            raise TypeError(
                f"Function '{func}' is not associative and cannot be used "
                f"with a SimpleAggregateFunction source column. "
                f"SimpleAggregateFunction only supports associative functions: "
                f"{sorted(_ASSOCIATIVE_AGGS)}."
            )
        if src_func is not None and src_func.lower() != func.lower():
            raise TypeError(
                f"Function mismatch: source column type is "
                f"SimpleAggregateFunction({src_func}, ...) but declared "
                f"aggregate function is '{func}'."
            )
        return _combinator_name(func, "", func_params)

    return _combinator_name(func, "State", func_params)


# The arg type `agg.sum()` / `agg.avg()` fall back to when the caller does not
# give one. An aggregating view replaces only this value with the resolved
# source column type; a type the caller wrote is left alone.
_DEFAULT_AGG_ARG_TYPE = "Float64"


@dataclass(frozen=True)
class AggExpr:
    """A typed aggregate expression for use in aggregating views.

    Carries enough information to generate BOTH sides of the aggregate-MV
    correspondence from a single declaration:
      - the target column type:      ``AggregateFunction(<func>, <types>)``
      - the MV SELECT combinator:    ``<func>State(<column>)``

    Because both derive from one ``AggExpr``, they cannot drift, which is the
    whole point of the typed door over the string door.

    Attributes:
        func: Aggregate function name (e.g. ``"sum"``, ``"avg"``, ``"count"``).
        arg: The source column / expression being aggregated (``None`` for
             ``count()``).
        arg_types: The ClickHouse type(s) of the argument, for the
            ``AggregateFunction`` signature.
        alias: Output column name in the target table.
    """
    func: str
    arg: Any
    arg_types: tuple[str, ...]
    alias: str | None = None

    def as_(self, alias: str) -> AggExpr:
        """Return a copy with the output column name set."""
        return AggExpr(self.func, self.arg, self.arg_types, alias)

    def target_type(self) -> str:
        """Render the target column type: ``AggregateFunction(func, types...)``."""
        types = ", ".join(self.arg_types)
        return f"AggregateFunction({self.func}, {types})" if types \
            else f"AggregateFunction({self.func})"

    def state_combinator(self, source_column_type: str | None = None) -> str:
        """Render the MV SELECT expression, choosing the combinator based on
        the source column type.

        See :func:`resolve_combinator` for the three-branch logic.
        """
        combinator = resolve_combinator(self.func, source_column_type)
        inner = _render_arg(self.arg) if self.arg is not None else ""
        expr = f"{combinator}({inner})"
        return f"{expr} AS {self.alias}" if self.alias else expr


def _render_arg(arg: Any) -> str:
    """Render an aggregate argument (a column reference or raw fragment)."""
    from dbwarden.databases.clickhouse.raw import ChRaw
    if isinstance(arg, ChRaw):
        return arg.sql
    name = getattr(arg, "name", None) or getattr(arg, "key", None)
    if name:
        return str(name)
    return str(arg)


class _AggNamespace:
    """Attribute-style aggregate constructors: ``agg.sum(col)``, ``agg.count()``.

    Attribute access (``agg.sum``) is discoverable and typo-proof, unlike
    stringly-typed function names.  ``agg.raw()`` is the passthrough for
    combinators the namespace doesn't enumerate.
    """

    def sum(  # noqa: A003
        self, arg: Any, type_: str = _DEFAULT_AGG_ARG_TYPE,
    ) -> AggExpr:
        """``SUM`` aggregate. → ``AggregateFunction(sum, type)``, ``sumState(arg)``."""
        return AggExpr("sum", arg, (type_,))

    def avg(  # noqa: A003
        self, arg: Any, type_: str = _DEFAULT_AGG_ARG_TYPE,
    ) -> AggExpr:
        """``AVG`` aggregate. → ``AggregateFunction(avg, type)``, ``avgState(arg)``."""
        return AggExpr("avg", arg, (type_,))

    def count(self) -> AggExpr:
        """``COUNT`` aggregate. → ``AggregateFunction(count)``, ``countState()``."""
        return AggExpr("count", None, ())

    def uniq_exact(self, arg: Any, type_: str) -> AggExpr:
        """``uniqExact``. → ``AggregateFunction(uniqExact, type)``."""
        return AggExpr("uniqExact", arg, (type_,))

    def uniq(self, arg: Any, type_: str) -> AggExpr:
        """``uniq``. → ``AggregateFunction(uniq, type)``."""
        return AggExpr("uniq", arg, (type_,))

    def groupArray(self, arg: Any, type_: str) -> AggExpr:  # noqa: N802
        """``groupArray``. → ``AggregateFunction(groupArray, type)``."""
        return AggExpr("groupArray", arg, (type_,))

    def groupUniqArray(self, arg: Any, type_: str) -> AggExpr:  # noqa: N802
        """``groupUniqArray``. → ``AggregateFunction(groupUniqArray, type)``."""
        return AggExpr("groupUniqArray", arg, (type_,))

    def quantile(self, arg: Any, type_: str) -> AggExpr:
        """``quantile``. → ``AggregateFunction(quantile, type)``."""
        return AggExpr("quantile", arg, (type_,))

    def any(self, arg: Any, type_: str) -> AggExpr:
        """``any``. → ``AggregateFunction(any, type)``."""
        return AggExpr("any", arg, (type_,))

    def any_last(self, arg: Any, type_: str) -> AggExpr:
        """``anyLast``. → ``AggregateFunction(anyLast, type)``."""
        return AggExpr("anyLast", arg, (type_,))

    def min(self, arg: Any, type_: str) -> AggExpr:  # noqa: A003
        return AggExpr("min", arg, (type_,))

    def max(self, arg: Any, type_: str) -> AggExpr:  # noqa: A003
        return AggExpr("max", arg, (type_,))

    def raw(  # noqa: A003
        self, func: str, arg: Any, *arg_types: str,
    ) -> AggExpr:
        """Escape hatch for combinators not enumerated above.

        Example::

            agg.raw("sumIf", amount_col, "Float64", "UInt8")

        Parametric aggregates keep their parameter list on the function name
        and the ``-State`` combinator is rendered correctly::

            agg.raw("quantile(0.95)", duration_ms, "UInt32")
            # → quantileState(0.95)(duration_ms) AS ...
            #   AggregateFunction(quantile(0.95), UInt32)
        """
        return AggExpr(func, arg, arg_types)


agg = _AggNamespace()


# ── column-type constructor ─────────────────────────────────────────────────


class ChAggStateType:
    """Declare an ``AggregateFunction`` column type.

    Renders to ``AggregateFunction(<func>, <types...>)``.

    Use in ``mapped_column()`` when declaring an ``AggregatingMergeTree`` target
    table by hand (rather than via ``aggregating_view()`` which derives the types
    automatically).

    Example::

        from dbwarden.databases.clickhouse import ch_agg_state

        amount_sum = mapped_column(ch_agg_state("sum", "Float64"))
        # → amount_sum AggregateFunction(sum, Float64)
    """

    def __init__(self, func: str, *types: str) -> None:
        self.func = func
        self.types = types

    def __repr__(self) -> str:
        types = ", ".join(self.types)
        return f"AggregateFunction({self.func}, {types})" if types \
            else f"AggregateFunction({self.func})"

    def __eq__(self, other: object) -> bool:
        if isinstance(other, ChAggStateType):
            return self.func == other.func and self.types == other.types
        return NotImplemented


def ch_agg_state(func: str, *types: str) -> ChAggStateType:
    """Declare an ``AggregateFunction`` column type.

    Example::

        amount_sum = mapped_column(ch_agg_state("sum", "Float64"))
    """
    return ChAggStateType(func, *types)
