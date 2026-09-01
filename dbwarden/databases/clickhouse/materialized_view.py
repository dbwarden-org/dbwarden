from __future__ import annotations

import re
import warnings
from dataclasses import dataclass, field as dc_field
from typing import Any


@dataclass
class MaterializedViewSpec:
    """Typed spec for a ClickHouse materialized view.

    Expression fields (select, order_by, partition_by, ttl) accept
    :class:`~sqlalchemy.sql.ColumnElement`, :class:`ChRaw`, or plain ``str``.
    When a ``ColumnElement`` is provided, it is rendered to ClickHouse SQL via
    :func:`render_expr`.

    Attributes:
        name: View name (set by ``_validate_view_class`` from ``__tablename__``).
        select: The SELECT query.
        to: Explicit target table name.  ``None`` is allowed for the deprecated
            module-level form only; the class API requires ``to``.
        refresh: Refresh schedule (e.g. ``"EVERY 1 HOUR"``).
        populate: If True, emit ``POPULATE`` for one-time backfill.
        engine: Engine spec for implicit-storage MVs (module-level form only).
        order_by: ORDER BY for implicit-storage MVs.
        partition_by: Optional PARTITION BY.
        ttl: Optional TTL expression(s).
        settings: Optional engine SETTINGS.
    """
    name: str | None = None
    select: Any = None
    to: str | None = None
    refresh: str | None = None
    populate: bool = False
    engine: Any = None
    order_by: Any = None
    partition_by: Any = None
    ttl: Any = None
    settings: dict[str, str] | None = None

    def to_dict(self) -> dict[str, Any]:
        from dbwarden.databases.clickhouse.compiler import render_expr, render_expr_list

        d: dict[str, Any] = {
            "ch_select_statement": (
                ", ".join(render_expr(item) for item in self.select)
                if isinstance(self.select, (list, tuple))
                else render_expr(self.select)
            ) if self.select is not None else None,
        }
        if self.to is not None:
            to_value = self.to
            if isinstance(to_value, type) and hasattr(to_value, "__tablename__"):
                to_value = to_value.__tablename__
            d["ch_to_table"] = to_value
        if self.refresh is not None:
            d["ch_refresh"] = self.refresh
        if self.populate:
            d["ch_populate"] = True
        if self.engine is not None:
            from dbwarden.databases.clickhouse.engine import ChEngineSpec
            if isinstance(self.engine, ChEngineSpec):
                d["ch_engine_raw"] = self.engine.to_dict()
                d["ch_engine"] = self.engine.name
                if self.settings is None and self.engine.settings is not None:
                    d["ch_settings"] = dict(self.engine.settings)
            else:
                d["ch_engine"] = self.engine
        if self.order_by is not None:
            d["ch_order_by"] = (
                render_expr_list(self.order_by)
                if isinstance(self.order_by, list)
                else render_expr(self.order_by)
            )
        if self.partition_by is not None:
            d["ch_partition_by"] = render_expr(self.partition_by)
        if self.ttl is not None:
            d["ch_ttl"] = (
                render_expr_list(self.ttl)
                if isinstance(self.ttl, list)
                else [render_expr(self.ttl)]
            )
        if self.settings is not None:
            d["ch_settings"] = dict(self.settings)
        return d


def _select_items_have_aggregates(items: list) -> bool:
    """Check if a structured select list contains aggregate functions."""
    from sqlalchemy.sql.elements import Label
    from sqlalchemy.sql.functions import FunctionElement

    _AGGREGATE_NAMES = frozenset({
        "sum", "count", "avg", "min", "max", "any", "uniq",
        "groupArray", "groupUniqArray",
    })

    for item in items:
        inner = item
        # Unwrap Label to get the underlying expression
        if isinstance(inner, Label):
            inner = inner.element
        if isinstance(inner, FunctionElement):
            name = getattr(inner, "name", None) or getattr(inner, "__class__", None)
            func_name = str(name).lower().split(".")[-1] if name else ""
            if func_name in _AGGREGATE_NAMES:
                return True
            if func_name.endswith("state") or func_name.endswith("merge"):
                return True
    return False


def materialized_view(
    *,
    name: str | None = None,
    select: Any = None,
    to: str | None = None,
    refresh: str | None = None,
    populate: bool = False,
    engine: Any = None,
    order_by: Any = None,
    partition_by: Any = None,
    ttl: Any = None,
    settings: dict[str, str] | None = None,
    **deprecated: Any,
) -> MaterializedViewSpec:
    """Declare a ClickHouse materialized view.

    TWO MODES, because an MV sometimes creates a node and sometimes does not::

    MODE A: ``to`` omitted.  The class IS the target table.
        Emits: CREATE TABLE <__tablename__> (declared columns) ENGINE = <engine> ...
               CREATE MATERIALIZED VIEW <__tablename__>_mv TO <__tablename__> AS ...
        Requires: engine, order_by, and column declarations on the class.

    MODE B: ``to`` given.  The class IS the MV; the target already exists.
        Emits: CREATE MATERIALIZED VIEW <__tablename__> TO <to> AS ...
        Forbids: engine, order_by, column declarations.

    Implicit ``.inner.`` storage (``to=None`` without engine) is NOT supported
    in the class API.  The module-level form (deprecated) still accepts it.

    Returns a ``MaterializedViewSpec``; call ``.to_dict()`` at the model boundary.

    Expression fields (select, order_by, partition_by, ttl) accept
    :class:`~sqlalchemy.sql.ColumnElement`, :class:`ChRaw`, or plain ``str``.
    When a ``ColumnElement`` is provided, it is rendered to ClickHouse SQL via
    :func:`render_expr`.

    Args:
        name: Deprecated module-level form only.  In class body the view name
            is ``__tablename__``.
        select: The SELECT.  A :class:`~sqlalchemy.sql.ColumnElement`,
            ``ch_raw()``, or plain string.
        to: Target table name (Mode B).  Omit for Mode A.
        refresh: Refresh schedule for refreshable MVs, e.g. ``"EVERY 1 HOUR"``.
        populate: If True, emit ``POPULATE`` for one-time backfill.
        engine: Mode A only.  MUST be a collapsing engine if ``select`` aggregates.
        order_by: Mode A only.  ORDER BY for the target table.
        partition_by: Optional PARTITION BY.
        ttl: Optional TTL expression(s).
        settings: Optional engine SETTINGS.

    Raises:
        ValueError: if ``to`` is None and ``engine`` is not provided.
        ValueError: if aggregating select on non-collapsing engine.
        ValueError: if ``populate`` and ``refresh`` are both set.
    """
    # Backward compat for deprecated to_table kwarg
    if "to_table" in deprecated:
        warnings.warn(
            "materialized_view(to_table=...) is deprecated. Use to= instead.",
            DeprecationWarning,
            stacklevel=2,
        )
        to = deprecated.pop("to_table", to)
    if deprecated:
        raise TypeError(f"materialized_view() got unexpected keyword arguments: {list(deprecated)}")

    if to is None and engine is None:
        raise ValueError(
            "materialized_view: engine is required when to is None "
            "(implicit .inner. storage). "
            "In class body, provide a target table with to=."
        )
    if to is None and engine is not None:
        from dbwarden.databases.clickhouse.views import _validate_mv_engine
        from dbwarden.databases.clickhouse.raw import ChRaw
        engine_name = engine.name if hasattr(engine, "name") else str(engine)
        select_is_raw = isinstance(select, (str, ChRaw))
        has_aggregates = False
        if not select_is_raw and isinstance(select, (list, tuple)):
            has_aggregates = _select_items_have_aggregates(select)
        _validate_mv_engine(
            engine_name,
            has_aggregates=has_aggregates,
            select_is_raw=select_is_raw,
        )
    if populate and refresh:
        raise ValueError(
            "materialized_view: populate and refresh are mutually exclusive"
        )

    return MaterializedViewSpec(
        name=name,
        select=select,
        to=to,
        refresh=refresh,
        populate=populate,
        engine=engine,
        order_by=order_by,
        partition_by=partition_by,
        ttl=ttl,
        settings=settings,
    )


@dataclass(frozen=True)
class AggregatingViewSpec:
    """An aggregate rollup: AggregatingMergeTree target + the MV that fills it.

    Returned by :func:`aggregating_view`.  Call ``.to_dict()`` at the model
    serialization boundary to produce the dict format consumed by the
    discovery and expansion pipeline.

    Expression fields (group_by, order_by, partition_by, ttl) accept
    :class:`~sqlalchemy.sql.ColumnElement`, :class:`ChRaw`, or plain ``str``.

    ``target_name`` is ``__tablename__`` (set by ``_validate_view_class``).
    ``mv_name`` is ``f"{target_name}_mv"``.

    The object KIND is this class. Nothing stringly-typed carries it.
    ``get_all_ch_views`` and ``_validate_view_class`` dispatch on
    ``isinstance(spec, AggregatingViewSpec)``, not on a field value or dict key.
    """
    source: Any = ""
    group_by: tuple[Any, ...] = ()
    aggregates: tuple[Any, ...] = ()
    order_by: tuple[Any, ...] = ()
    partition_by: Any | None = None
    ttl: tuple[Any, ...] | None = None
    settings: dict[str, str] | None = None
    target_name: str | None = None

    @property
    def mv_name(self) -> str:
        """The generated MV name. Derived, never stored."""
        if self.target_name is None:
            raise ValueError(
                "target_name unset; spec not yet bound to a class"
            )
        return f"{self.target_name}_mv"

    @property
    def target_columns(self) -> list[str]:
        from dbwarden.databases.clickhouse.compiler import column_name_from_expr
        cols: list[str] = []
        for g in self.group_by:
            cols.append(column_name_from_expr(g))
        for a in self.aggregates:
            if not a.alias:
                raise ValueError("aggregating_view aggregates require an alias")
            cols.append(a.alias)
        return cols

    def to_dict(self) -> dict[str, Any]:
        from dbwarden.databases.clickhouse.compiler import (
            render_expr,
            render_expr_list,
            column_name_from_expr,
        )

        group_by = list(self.group_by)
        aggregates = list(self.aggregates)
        order_by = list(self.order_by)
        target = self.target_name or ""
        mv_name = self.mv_name if self.target_name else ""
        source_name = _resolve_source(self.source)

        # Resolve source column types for cascade combinator detection and for
        # deriving the target AggregateFunction signature from plain source columns.
        source_col_types = _resolve_source_column_types(self.source)

        # Build effective aggregate expressions.  When the source is a plain
        # table, the default Float64 arg type is replaced by the resolved CH
        # type of the source column so the target column matches the state
        # combinator (e.g. sum over an Int32 column -> AggregateFunction(sum, Int32)).
        from dbwarden.databases.clickhouse.agg import (
            _DEFAULT_AGG_ARG_TYPE,
            AggExpr,
            _classify_column_type,
        )
        from dbwarden.engine.backends.clickhouse.extract import _render_ch_type_from_sa

        effective_aggregates: list[AggExpr] = []
        aggregate_source_types: list[str | None] = []
        for a in aggregates:
            src_type: str | None = None
            if a.arg is not None:
                arg_name = getattr(a.arg, "name", None) or getattr(a.arg, "key", None)
                if isinstance(a.arg, str):
                    arg_name = a.arg
                if arg_name:
                    src_type = source_col_types.get(arg_name)
            aggregate_source_types.append(src_type)

            effective = a
            if (
                src_type is not None
                # Only the defaulted type is replaced. `agg.sum()` and
                # `agg.avg()` fall back to Float64 when the caller says
                # nothing; every other helper - and `agg.raw()` - takes the
                # type explicitly, and substituting there would silently
                # rewrite a declared `UInt32` state as the source column's
                # `Int32`, changing the AggregateFunction signature.
                and tuple(a.arg_types) == (_DEFAULT_AGG_ARG_TYPE,)
                and _classify_column_type(src_type)[0] == "plain"
            ):
                ch_type = _render_ch_type_from_sa(None, src_type.upper().strip())
                effective = AggExpr(a.func, a.arg, (ch_type,), a.alias)
            effective_aggregates.append(effective)

        group_parts: list[str] = []
        for g in group_by:
            rendered = render_expr(g)
            alias = column_name_from_expr(g)
            # Labels and plain column references compile without the AS alias;
            # add it so the select item matches the target column and GROUP BY.
            if not re.search(r"\s+AS\s+", rendered, re.IGNORECASE):
                rendered = f"{rendered} AS {alias}"
            group_parts.append(rendered)
        select_items = list(group_parts) + [
            a.state_combinator(source_column_type=src_type)
            for a, src_type in zip(effective_aggregates, aggregate_source_types)
        ]

        select_sql = (
            "SELECT " + ", ".join(select_items) +
            " FROM " + source_name +
            " GROUP BY " + ", ".join(
                column_name_from_expr(g) for g in group_by
            )
        )

        target_columns = list(self.target_columns)

        target_opts: dict[str, Any] = {
            "name": target,
            "columns": target_columns,
            "aggregates": effective_aggregates,
            "order_by": render_expr_list(order_by if isinstance(order_by, list) else [order_by]),
        }
        if self.partition_by is not None:
            target_opts["partition_by"] = render_expr(self.partition_by)
        if self.ttl is not None:
            target_opts["ttl"] = (
                render_expr_list(list(self.ttl))
                if isinstance(self.ttl, (list, tuple))
                else [render_expr(self.ttl)]
            )
        if self.settings is not None:
            target_opts["settings"] = dict(self.settings)

        mv_spec = MaterializedViewSpec(
            select=select_sql,
            to=target,
        )

        return {
            "ch_agg_target": target_opts,
            "ch_agg_mv": mv_spec.to_dict(),
        }


def aggregating_view(
    *,
    source: Any,
    group_by: Any = None,
    aggregates: Any = None,
    order_by: Any = None,
    partition_by: Any = None,
    ttl: Any = None,
    settings: dict[str, str] | None = None,
    name: str | None = None,
    **deprecated: Any,
) -> AggregatingViewSpec:
    """Declare an aggregate rollup as a coherent source->target->MV triad.

    Generates THREE DDL objects from one declaration:
      1. An ``AggregatingMergeTree`` target table whose columns are the
         ``AggregateFunction(...)`` types derived from ``aggregates``.
      2. A materialized view whose SELECT uses the matching
         ``<func>State(...)`` combinators, ``TO`` the target.
      3. The source table (referenced, not created; it must already exist).

    Because both the target column types and the MV combinators derive from the
    same list of ``AggExpr``, they are guaranteed consistent; the correspondence
    that is manual and drift-prone in the string-SELECT form is here derived and
    safe.

    Expression fields (group_by, order_by, partition_by, ttl) accept
    :class:`~sqlalchemy.sql.ColumnElement`, :class:`ChRaw`, or plain ``str``.
    When a ``ColumnElement`` is provided, it is rendered to ClickHouse SQL via
    :func:`render_expr`.

    **Class API (preferred):** use inside ``class Meta(CHViewMeta)``.  The
    target name is ``__tablename__``; the MV is ``<__tablename__>_mv``.
    ``name`` and ``source`` are not passed: ``source`` is the model class::

        class EventDaily(AggregatingView):
            __tablename__ = "event_daily"

            class Meta(CHViewMeta):
                ch = aggregating_view(
                    source=Event,
                    group_by=[func.toDate(Event.event_time).label("day")],
                    aggregates=[agg.sum(Event.amount).as_("total")],
                    order_by=["day"],
                )

    Args:
        source: The source model class (preferred) or table name string.
        group_by: ``GROUP BY`` keys: :class:`~sqlalchemy.sql.ColumnElement`,
            ``ch_raw()``, or strings.
        aggregates: The aggregate columns, as ``AggExpr`` (from ``agg.*``), each
            with ``.as_(alias)`` set.
        order_by: ``ORDER BY`` for the ``AggregatingMergeTree`` target.
        partition_by: Optional ``PARTITION BY`` for the target.
        ttl: Optional TTL for the target.
        settings: Optional engine ``SETTINGS`` for the target.
        name: Deprecated.  Target name comes from ``__tablename__`` in
            the class API.  Only needed for the module-level form.

    Returns:
        An :class:`AggregatingViewSpec`; call ``.to_dict()`` at the model boundary.
    """
    # Backward compat for deprecated target_name kwarg
    if "target_name" in deprecated:
        warnings.warn(
            "aggregating_view(target_name=...) is deprecated. "
            "The target name is always __tablename__ in the class API.",
            DeprecationWarning,
            stacklevel=2,
        )
        deprecated.pop("target_name", None)
    if deprecated:
        raise TypeError(f"aggregating_view() got unexpected keyword arguments: {list(deprecated)}")

    group_by = group_by or []
    aggregates = aggregates or []

    if not aggregates:
        raise ValueError("aggregating_view: at least one aggregate is required")
    if not all(hasattr(a, "alias") and a.alias for a in aggregates):
        raise ValueError(
            "aggregating_view: every aggregate must have .as_(alias) set"
        )

    # ── Import-time validation for cascade sources ──────────────────────────
    source_spec = getattr(getattr(source, "Meta", None), "ch", None)
    from dbwarden.databases.clickhouse.materialized_view import (
        AggregatingViewSpec as _AggregatingViewSpec,
    )
    if isinstance(source_spec, _AggregatingViewSpec):
        # Build alias -> target_type map from the source spec
        source_col_types: dict[str, str] = {}
        for src_agg in source_spec.aggregates:
            if src_agg.alias:
                source_col_types[src_agg.alias] = src_agg.target_type()

        from dbwarden.databases.clickhouse.agg import (
            resolve_combinator, _classify_column_type,
        )
        for agg_expr in aggregates:
            alias = agg_expr.alias
            if alias is None:
                continue
            src_type = source_col_types.get(alias)
            if src_type is None:
                # Aggregate alias not found in source; could be a group-by
                # key rather than a state column.  Skipping validation because
                # the combinator will fall back to *State for plain types.
                continue
            # This call raises TypeError on mismatch
            resolve_combinator(agg_expr.func, src_type)

    return AggregatingViewSpec(
        source=source,
        group_by=tuple(group_by),
        aggregates=tuple(aggregates),
        order_by=tuple(order_by) if order_by else (),
        partition_by=partition_by,
        ttl=tuple(ttl) if ttl else None,
        settings=settings,
        target_name=name,
    )


def _resolve_source(source: Any) -> str:
    """Get the table name from a source reference.

    ``source`` may be:
    * A model class with ``__tablename__``, returns the tablename directly.
    * A string class name (forward reference), scans loaded modules and the
      ``ChView._ch_view_registry`` for a class with that name and returns
      its ``__tablename__``.
    * A bare table name string, returned as-is.

    Resolution happens lazily at ``to_dict()`` time, by which point all
    model classes should be loaded.
    """
    if isinstance(source, str):
        import sys
        for mod_name, mod in sys.modules.items():
            if mod is None:
                continue
            cls = getattr(mod, source, None)
            if cls is not None and isinstance(cls, type):
                tablename = getattr(cls, "__tablename__", None)
                if tablename:
                    return tablename
        from dbwarden.databases.clickhouse.views import ChView
        for cls in ChView._ch_view_registry:
            if cls.__name__ == source:
                tablename = getattr(cls, "__tablename__", None)
                if tablename:
                    return tablename
        return source
    tablename = getattr(source, "__tablename__", None)
    if tablename:
        return tablename
    raise TypeError(
        f"_resolve_source: source must be a model class with __tablename__, "
        f"got {type(source).__name__}"
    )


def _resolve_source_column_types(source: Any) -> dict[str, str]:
    """Resolve source column types for cascade combinator detection.

    Returns a mapping ``{column_alias: clickhouse_type_string}``.

    * If *source* is an ``AggregatingView`` subclass (has ``Meta.ch`` of type
      ``AggregatingViewSpec``): the column types are derived from the source
      spec's aggregates: each aggregate's alias maps to its target type
      (e.g. ``"amount_sum" → "AggregateFunction(sum, Float64)"``).
    * If *source* is a plain model class: column types come from the
      SQLAlchemy ``__table__`` columns.
    * If *source* is a string: attempt to resolve it to a class; if that fails,
      return an empty dict (bare table name; no type info available).

    An empty dict means "no cascade detection possible", the caller falls back
    to the default ``*State`` combinator.
    """
    # Resolve string → class if possible
    if isinstance(source, str):
        import sys
        for mod_name, mod in sys.modules.items():
            if mod is None:
                continue
            cls = getattr(mod, source, None)
            if cls is not None and isinstance(cls, type):
                source = cls
                break
        else:
            from dbwarden.databases.clickhouse.views import ChView
            for cls in ChView._ch_view_registry:
                if cls.__name__ == source:
                    source = cls
                    break
            else:
                return {}  # bare table name; no type info

    # If source is an aggregating view, derive types from its spec
    source_spec = getattr(getattr(source, "Meta", None), "ch", None)
    from dbwarden.databases.clickhouse.materialized_view import (
        AggregatingViewSpec as _AggregatingViewSpec,
    )
    if isinstance(source_spec, _AggregatingViewSpec):
        result: dict[str, str] = {}
        for agg in source_spec.aggregates:
            if agg.alias:
                result[agg.alias] = agg.target_type()
        # Include group-by column types by resolving through the cascade chain
        gb_types = _resolve_aggregating_view_group_by_types(source_spec)
        result.update(gb_types)
        return result

    # Plain model class; read column types from __table__
    table = getattr(source, "__table__", None)
    if table is not None:
        return {col.name: str(col.type) for col in table.columns}

    return {}


def _resolve_aggregating_view_group_by_types(
    spec: Any,
    _depth: int = 0,
) -> dict[str, str]:
    """Resolve group-by column types by traversing the cascade chain to the
    base SA table.

    For each group-by expression in *spec*:

    * If it is a plain column name string that exists in the source's resolved
      column types, the type is carried over directly.
    * If it is a ``ch_raw()`` expression, column-name-like identifiers in the
      SQL are matched against the source's resolved column types.
    * Otherwise the column is omitted from the result (caller falls back to
      ``"String"``).

    Recursion is bounded at depth 20 to guard against circular references.
    """
    if _depth > 20:
        return {}
    from dbwarden.databases.clickhouse.compiler import column_name_from_expr
    from dbwarden.databases.clickhouse.raw import ChRaw

    source_types = _resolve_source_column_types(spec.source)
    if not source_types:
        return {}

    result: dict[str, str] = {}
    for expr in spec.group_by:
        col_name = column_name_from_expr(expr)
        if not col_name:
            continue
        if col_name in source_types:
            result[col_name] = source_types[col_name]
            continue
        if isinstance(expr, ChRaw):
            matched = _match_column_type_from_raw(expr.sql, source_types)
            if matched:
                result[col_name] = matched
        elif isinstance(expr, str) and "(" in expr:
            matched = _match_column_type_from_raw(expr, source_types)
            if matched:
                result[col_name] = matched
    return result


def _match_column_type_from_raw(sql: str, source_types: dict[str, str]) -> str | None:
    """Try to infer the CH type of a ``ch_raw()`` expression by finding column
    names from *source_types* that appear as standalone identifiers in the raw
    SQL string.

    ``"toStartOfHour(toTimeZone(closed_at, 'America/Montevideo'))"``
    with ``source_types = {"closed_at": "DateTime"}`` returns ``"DateTime"``.

    ``"toDate(hour)"`` with ``source_types = {"hour": "DateTime"}``
    returns ``"DateTime"``.
    """
    import re as _re
    sql = _re.sub(r"\s+AS\s+\w+$", "", sql, flags=_re.IGNORECASE).strip()
    if not sql or not source_types:
        return None
    for col_name, col_type in source_types.items():
        if _re.search(r"\b" + _re.escape(col_name) + r"\b", sql):
            return col_type
    return None

