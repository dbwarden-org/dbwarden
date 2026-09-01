from __future__ import annotations

import warnings
from typing import Any

from dbwarden.exceptions import DBWardenConfigError
from dbwarden.schema.table_meta import CHViewMeta
from dbwarden.engine.core.models import ModelColumn, ModelTable
from dbwarden.databases.clickhouse import AggregatingViewSpec, MaterializedViewSpec


class ChView:
    """Base for ClickHouse view declarations. NOT a SQLAlchemy mapped class.

    Subclasses are collected into ``_ch_view_registry`` at class-creation time
    and discovered alongside SQLAlchemy models by ``get_all_model_tables()``.

    Required in every non-abstract subclass:
        ``__tablename__``: see the per-subclass meaning in MaterializedView
            and AggregatingView; it is NOT uniformly "the MV".
        ``Meta.ch``: a MaterializedViewSpec or AggregatingViewSpec.

    Example::

        from dbwarden.databases.clickhouse import (
            AggregatingView, CHViewMeta, aggregating_view, agg,
        )

        class EventDaily(AggregatingView):
            __tablename__ = "event_daily"

            class Meta(CHViewMeta):
                ch = aggregating_view(
                    source=Event,
                    group_by=[func.toDate(Event.event_time).label("day")],
                    aggregates=[agg.sum(Event.amount).as_("total")],
                    order_by=["day"],
                )
    """

    __tablename__: str
    _ch_view_registry: list[type[ChView]] = []

    def __init_subclass__(cls, **kw: Any) -> None:
        super().__init_subclass__(**kw)
        # Only skip if __abstract__ is set on THIS class, not inherited.
        # MaterializedView/AggregatingView are abstract bases; their concrete
        # subclasses must NOT be skipped.
        if cls.__dict__.get("__abstract__", False):
            return
        _validate_view_class(cls)
        ChView._ch_view_registry.append(cls)


class MaterializedView(ChView):
    """A ClickHouse materialized view. Two modes; see materialized_view().

    Mode A (to= omitted):  the class IS the target table; the MV is generated
        with the derived name f"{__tablename__}_mv". Columns are declared on
        the class. Engine is required.
    Mode B (to= given):    the class IS the MV itself (an edge with no new
        node); __tablename__ is the MV's own name. No columns, no engine.
    """
    __abstract__ = True


class AggregatingView(ChView):
    """An aggregate rollup: AggregatingMergeTree target + the MV that fills it.

    The class IS the target table. __tablename__ is the target's name; the MV
    is generated as f"{__tablename__}_mv".

    Columns are NOT declared; they are DERIVED from group_by + aggregates.
    Declaring them would be two shapes for one fact and would reintroduce
    exactly the drift the derived correspondence exists to prevent.
    """
    __abstract__ = True


def derive_agg_target_columns(agg_result: AggregatingViewSpec | dict) -> list[str]:
    """Extract target table column names from an :func:`aggregating_view` result.

    The target columns are: group-by keys (by name) followed by aggregate
    aliases, in the same order as produced by :func:`aggregating_view`.

    Args:
        agg_result: The :class:`AggregatingViewSpec` or dict returned by
            :func:`aggregating_view`.

    Returns:
        List of column names for the ``AggregatingMergeTree`` target table.
    """
    if isinstance(agg_result, AggregatingViewSpec):
        return list(agg_result.target_columns)
    target_info = agg_result.get("ch_agg_target", {})
    return list(target_info.get("columns", []))


_COLLAPSING_ENGINES = frozenset({
    "SummingMergeTree", "ReplicatedSummingMergeTree",
    "AggregatingMergeTree", "ReplicatedAggregatingMergeTree",
    "ReplacingMergeTree", "ReplicatedReplacingMergeTree",
    "CollapsingMergeTree", "ReplicatedCollapsingMergeTree",
    "VersionedCollapsingMergeTree", "ReplicatedVersionedCollapsingMergeTree",
    "GraphiteMergeTree", "ReplicatedGraphiteMergeTree",
})

_MERGETREE_FAMILY = frozenset({
    "MergeTree", "ReplicatedMergeTree",
    "SummingMergeTree", "ReplicatedSummingMergeTree",
    "AggregatingMergeTree", "ReplicatedAggregatingMergeTree",
    "ReplacingMergeTree", "ReplicatedReplacingMergeTree",
    "CollapsingMergeTree", "ReplicatedCollapsingMergeTree",
    "VersionedCollapsingMergeTree", "ReplicatedVersionedCollapsingMergeTree",
    "GraphiteMergeTree", "ReplicatedGraphiteMergeTree",
})


def _validate_mv_engine(
    engine_name: str,
    *,
    has_aggregates: bool = False,
    select_is_raw: bool = False,
) -> None:
    """Validate engine choice for a materialized view.

    An MV's SELECT runs per INSERTED BLOCK, not over the full table.  A
    non-collapsing engine therefore ACCUMULATES one partial-aggregate row per
    insert forever, and reads return an arbitrary partial.  This is silent: no
    error, wrong numbers.

    Two verification paths, because a raw-SQL select is opaque:

    *Structured select* (``select_is_raw=False``, ``has_aggregates`` set by the
    caller after inspecting the ``ColumnElement`` list).  When the select
    aggregates and the engine can't collapse rows, this raises ``ValueError``.

    *Raw select* (``select_is_raw=True``).  The validator cannot inspect the
    SQL, so it emits an advisory ``UserWarning`` instead of a hard error.  The
    user owns collapse-correctness.

    Raises:
        ValueError: if engine is not MergeTree-family (implicit-storage MVs).
        ValueError: if ``has_aggregates`` is True and engine is not in
            ``_COLLAPSING_ENGINES``.
        UserWarning: if ``select_is_raw`` and engine is not collapsing.
    """
    if engine_name not in _MERGETREE_FAMILY:
        raise ValueError(
            f"Invalid engine for materialized view: "
            f"{engine_name!r}. Must be one of MergeTree family."
        )

    if has_aggregates and engine_name not in _COLLAPSING_ENGINES:
        raise ValueError(
            f"Engine {engine_name!r} cannot collapse partial-aggregate rows. "
            f"Aggregating SELECTs on a non-collapsing engine accumulate one "
            f"partial row per insert block forever; reads silently return an "
            f"arbitrary partial. "
            f"Use aggregating_view() for AggregateFunction columns with -State "
            f"combinators, or an explicit collapsing engine "
            f"(summing_merge_tree(), aggregating_merge_tree(), etc.) if "
            f"partial-sum merging on ORDER BY is what is wanted."
        )

    if select_is_raw and engine_name not in _COLLAPSING_ENGINES:
        warnings.warn(
            f"Engine {engine_name!r} is not in the collapsing family "
            f"(SummingMergeTree, AggregatingMergeTree, etc.) and the SELECT "
            f"was provided as raw SQL, so the validator cannot verify "
            f"collapse-safety. If the SELECT uses aggregate functions, this "
            f"will silently accumulate partial rows per insert. "
            f"Prefer structured select items (ColumnElement list) or a "
            f"collapsing engine.",
            UserWarning,
            stacklevel=2,
        )


def get_all_ch_views(
    model_paths: list[str] | None = None,
    db_name: str | None = None,
) -> list[dict[str, Any]]:
    """Return registered ``ChView`` subclasses from the class registry.

    Populated by ``ChView.__init_subclass__`` at import time.  Because
    ``discover_models_in_directory`` already imports every model module,
    importing is sufficient, with no new scanning mechanism, which was the
    strongest argument for the class form.

    The ``model_paths`` and ``db_name`` parameters are accepted for backward
    compatibility but ignored: discovery is purely registry-based.

    Returns a list of dicts, each with:
      - ``model_class``: the ``ChView`` subclass
      - ``spec``: the view spec (``MaterializedViewSpec`` or
        ``AggregatingViewSpec``)
      - ``view_type``: ``"materialized_view"`` or ``"aggregating_view"``
    """
    results: list[dict[str, Any]] = []
    for cls in ChView._ch_view_registry:
        meta_cls = cls.__dict__.get("Meta")
        if meta_cls is None:
            continue
        ch_spec = getattr(meta_cls, "ch", None)
        if ch_spec is None:
            continue
        if isinstance(ch_spec, MaterializedViewSpec):
            results.append({
                "model_class": cls,
                "spec": ch_spec,
                "view_type": "materialized_view",
            })
        elif isinstance(ch_spec, AggregatingViewSpec):
            results.append({
                "model_class": cls,
                "spec": ch_spec,
                "view_type": "aggregating_view",
            })
    return results


def ch_view_tables_from_models(
    model_paths: list[str] | None = None,
    db_name: str | None = None,
) -> list[ModelTable]:
    """Extract ModelTables from CH view model classes, expanding aggregating
    views into their constituent MV + target table.

    ChView subclasses are NOT SQLAlchemy-mapped, so this function builds
    ModelTable objects directly from the class annotations and the spec.

    Aggregating view targets are emitted BEFORE their MVs (the target must
    exist at MV creation time).  Cascading MV chains are topologically sorted
    so that inner cascade levels are created first.

    Returns a list of ``ModelTable`` instances that can be appended to the
    output of ``get_all_model_tables()``.
    """
    from dbwarden.engine.model_discovery.extraction import (
        extract_column_info, _map_sa_type_to_clickhouse,
    )

    views = get_all_ch_views(model_paths=model_paths, db_name=db_name)

    # Build a map: tablename -> (model_class, view_type, spec, _expand_agg_target(...))
    # and a DAG of aggregating view dependencies for topological sort.
    view_info: dict[str, tuple] = {}
    dep_graph: dict[str, set[str]] = {}  # viewer -> {source_tablename}

    for entry in views:
        model_class = entry["model_class"]
        view_type = entry["view_type"]
        spec = entry["spec"]
        tablename = model_class.__tablename__
        view_info[tablename] = (model_class, view_type, spec)

        if view_type == "aggregating_view":
            target = getattr(spec, "source", None)
            # Determine the source tablename for ordering
            if isinstance(target, type):
                src_tablename = getattr(target, "__tablename__", None)
            elif isinstance(target, str):
                # Try to resolve the string to a class
                import sys
                src_tablename = None
                for mod_name, mod in sys.modules.items():
                    if mod is None:
                        continue
                    cls = getattr(mod, target, None)
                    if cls is not None and isinstance(cls, type):
                        src_tablename = getattr(cls, "__tablename__", None)
                        if src_tablename:
                            break
                if src_tablename is None:
                    # Fallback: check the ChView registry for models loaded via
                    # load_model_from_path (not in sys.modules as module-level attrs).
                    for cls in ChView._ch_view_registry:
                        if cls.__name__ == target:
                            src_tablename = getattr(cls, "__tablename__", None)
                            break
                if src_tablename is None:
                    src_tablename = target  # bare table name; not a CH view, so no dep edge
            else:
                src_tablename = None

            if src_tablename and src_tablename != tablename:
                deps = dep_graph.setdefault(tablename, set())
                deps.add(src_tablename)

    # Topological sort (Kahn's algorithm), leaf/innermost first
    sorted_names: list[str] = []
    visited: set[str] = set()

    def _visit(name: str) -> None:
        if name in visited:
            return
        visited.add(name)
        for dep in dep_graph.get(name, set()):
            if dep in view_info:
                _visit(dep)
        sorted_names.append(name)

    for name in view_info:
        _visit(name)

    # Build ModelTables in dependency order
    tables: list[ModelTable] = []

    for tablename in sorted_names:
        model_class, view_type, spec = view_info[tablename]

        # Build columns from Column / MappedColumn descriptors on the class (Mode A).
        columns: list[ModelColumn] = []
        from sqlalchemy import Column as _SAColumn
        from sqlalchemy.orm import MappedColumn as _MappedColumn
        for attr_name, val in model_class.__dict__.items():
            if attr_name.startswith("_"):
                continue
            sa_col = None
            if isinstance(val, _MappedColumn):
                col_pairs = getattr(val, "columns_to_assign", None)
                if col_pairs:
                    sa_col = col_pairs[0][0]
            elif isinstance(val, _SAColumn):
                sa_col = val
            if sa_col is not None:
                ch_type = _map_sa_type_to_clickhouse(sa_col) if sa_col is not None else "String"
                columns.append(ModelColumn(
                    name=attr_name,
                    type=ch_type,
                    nullable=getattr(sa_col, "nullable", True),
                    primary_key=getattr(sa_col, "primary_key", False),
                    unique=False, default=None, foreign_key=None,
                    ch_meta={"ch_type": ch_type},
                ))

        # Build clickhouse_options from spec
        clickhouse_options = spec.to_dict()

        # Emit target table FIRST (must exist before MV)
        if view_type == "aggregating_view":
            target_table = _expand_agg_target(model_class, spec)
            if target_table:
                tables.append(target_table)
            # Use the full materialized-view spec so handlers see ch_object_type
            # and any other options; only the aggregating target is separate.
            agg_mv = clickhouse_options.get("ch_agg_mv", {})
            mv_opts: dict[str, Any] = dict(agg_mv)
            mv_name = spec.mv_name
        else:
            mv_opts = clickhouse_options
            mv_name = tablename
        mv_opts.setdefault("ch_object_type", "materialized_view")
        mv_table = ModelTable(
            name=mv_name,
            columns=columns,
            clickhouse_options=mv_opts,
            object_type="materialized_view",
        )
        tables.append(mv_table)

    return tables


def _ch_ddl_type_from_resolved(resolved: str) -> str:
    """Map a resolved SA-style type string to a ClickHouse DDL type.

    ``_resolve_source_column_types`` returns ``str(sa_col.type)`` (e.g.
    ``"VARCHAR"``, ``"DATETIME"``), which is not valid ClickHouse DDL.
    ``AggregateFunction(...)`` strings pass through unchanged.
    """
    from dbwarden.engine.backends.clickhouse.extract import _render_ch_type_from_sa
    return _render_ch_type_from_sa(None, resolved.upper().strip())


def _expand_agg_target(
    model_class: type,
    agg_spec: AggregatingViewSpec | dict[str, Any],
) -> ModelTable | None:
    """Create a synthetic ModelTable for the aggregating view target table.

    The target is an ``AggregatingMergeTree`` table whose columns are derived
    from the model class columns (group-by keys + aggregate columns).
    """
    if isinstance(agg_spec, AggregatingViewSpec):
        agg_dict = agg_spec.to_dict()
        target_info = agg_dict["ch_agg_target"]
    else:
        target_info = agg_spec.get("ch_agg_target", {})
    target_name = target_info.get("name")
    if not target_name:
        return None

    order_by = target_info.get("order_by")
    partition_by = target_info.get("partition_by")
    ttl = target_info.get("ttl")
    settings = target_info.get("settings")

    from dbwarden.databases.clickhouse.materialized_view import (
        _resolve_source_column_types,
        _resolve_aggregating_view_group_by_types,
    )
    source_ref = None
    if isinstance(agg_spec, AggregatingViewSpec):
        source_ref = agg_spec.source
    else:
        source_ref = agg_spec.get("source") if isinstance(agg_spec, dict) else None
    src_types: dict[str, str] = {}
    if source_ref is not None:
        src_types = _resolve_source_column_types(source_ref)
        # ch_raw() aliases produced by this view's own group-by (e.g.
        # "toDate(hour) AS day") are not columns of the source; resolve them
        # against the source's columns and merge them in.
        if isinstance(agg_spec, AggregatingViewSpec):
            own_keys = _resolve_aggregating_view_group_by_types(agg_spec)
            src_types.update(own_keys)
        target_info["_source_column_types"] = src_types

    # Build column type strings from aggregates + group-by keys
    agg_map: dict[str, str] = {}
    for a in target_info.get("aggregates", []):
        if a.alias:
            agg_map[a.alias] = a.target_type()
    col_names = target_info.get("columns", [])
    col_defs: list[str] = []
    for name in col_names:
        if name in agg_map:
            col_defs.append(f"{name} {agg_map[name]}")
        else:
            col_defs.append(f"{name} {_ch_ddl_type_from_resolved(src_types.get(name, 'String'))}")

    clickhouse_options: dict[str, Any] = {
        "ch_engine": "AggregatingMergeTree",
        "ch_object_type": "table",
    }
    if order_by is not None:
        clickhouse_options["ch_order_by"] = (
            [order_by] if isinstance(order_by, str) else list(order_by)
        )
    if partition_by is not None:
        clickhouse_options["ch_partition_by"] = partition_by
    if ttl is not None:
        clickhouse_options["ch_ttl"] = [ttl] if isinstance(ttl, str) else list(ttl)
    if settings is not None:
        clickhouse_options["ch_settings"] = dict(settings)
    if col_defs:
        clickhouse_options["ch_column_defs"] = col_defs

    columns = _make_target_columns(model_class, target_info)
    return ModelTable(
        name=target_name,
        columns=columns,
        clickhouse_options=clickhouse_options,
        object_type="table",
    )


def _make_target_columns(
    model_class: type,
    target_info: dict[str, Any],
) -> list[ModelColumn]:
    """Build ModelColumn list for the aggregating view target table.

    Copies column definitions from the model class, which has the same columns
    as the target (the target uses ``AggregateFunction`` types where the MV
    uses ``-State`` combinators).
    """
    column_names = target_info.get("columns", [])
    col_name_set = set(column_names)
    columns: list[ModelColumn] = []
    _source_column_types: dict[str, str] | None = target_info.get("_source_column_types")

    agg_map: dict[str, str] = {}
    for a in target_info.get("aggregates", []):
        if a.alias:
            agg_map[a.alias] = a.target_type()

    table = getattr(model_class, "__table__", None)
    if table is not None:
        from dbwarden.engine.backends.clickhouse.extract import _map_sa_type_to_clickhouse

        for sa_col in table.columns:
            if sa_col.name not in col_name_set:
                continue
            if sa_col.name in agg_map:
                # An aggregate column stores partial states: its type is the
                # AggregateFunction signature, never the SQLAlchemy type the
                # class happens to declare for it.
                col_type = agg_map[sa_col.name]
            else:
                # A dimension column carries a SQLAlchemy type, which is not
                # ClickHouse DDL - emitting it raw produced `VARCHAR(50)` and
                # `INTEGER` in the target table instead of `String` and `Int32`.
                resolved = (_source_column_types or {}).get(sa_col.name)
                col_type = (
                    _ch_ddl_type_from_resolved(resolved)
                    if resolved
                    else _map_sa_type_to_clickhouse(sa_col)
                )
            columns.append(ModelColumn(
                name=sa_col.name,
                type=col_type,
                nullable=sa_col.nullable,
                primary_key=sa_col.primary_key,
                unique=sa_col.unique,
                default=str(sa_col.default) if sa_col.default is not None else None,
                foreign_key=None,
                comment=sa_col.comment or None,
                ch_meta={"ch_type": col_type},
            ))

    for name in column_names:
        if name not in {c.name for c in columns}:
            src_type = _source_column_types.get(name) if _source_column_types else None
            col_type = agg_map.get(name, src_type or "String")
            columns.append(ModelColumn(
                name=name,
                type=col_type,
                nullable=True,
                primary_key=False,
                unique=False,
                default=None,
                foreign_key=None,
                ch_meta={"ch_type": col_type},
            ))

    return columns


def _has_user_columns(cls: type) -> bool:
    """Check if a class declares user-level mapped columns or column-like attrs.

    Inspects ``cls.__dict__`` for ``Column``/``MappedColumn`` instances and
    ``cls.__annotations__`` for ``Mapped[...]`` type hints.  ``__tablename__``,
    ``__abstract__``, ``Meta``, and dunder attrs are excluded.

    Works on both SQLAlchemy-DeclarativeBase subclasses (which have
    ``__table__``) and plain ``ChView`` subclasses (which don't: see Phase B),
    because it checks ``__dict__`` rather than ``__table__.c``.
    """
    from sqlalchemy import Column as SAColumn
    from sqlalchemy.orm import MappedColumn
    from sqlalchemy.orm import Mapped as MappedDescriptor

    # Check for Column or MappedColumn instances assigned in the class body
    for val in cls.__dict__.values():
        if isinstance(val, (SAColumn, MappedColumn)):
            return True

    # Check for Mapped[...] annotations
    for attr_name, attr_type in cls.__annotations__.items():
        if attr_name.startswith("_"):
            continue
        if attr_name in ("__tablename__", "__abstract__"):
            continue
        origin = getattr(attr_type, "__origin__", None)
        if origin is MappedDescriptor:
            return True

    return False


def _validate_view_class(model_class: type) -> None:
    """Validate that a ClickHouse view model class is properly configured.

    Checks:
      - ``class Meta`` inherits from :class:`CHViewMeta`.
      - ``Meta.ch`` is set to a :class:`MaterializedViewSpec` or
        :class:`AggregatingViewSpec`.
      - The spec type matches the view base class (MaterializedView →
        MaterializedViewSpec, AggregatingView → AggregatingViewSpec).
      - Mode A MaterializedView (to=None) has an engine.
      - The model has at least one primary key column.

    Called during model discovery; raises :class:`DBWardenConfigError` on
    invalid configuration.
    """
    meta = getattr(model_class, "Meta", None)
    if meta is None:
        return
    if not isinstance(meta, type) or not issubclass(meta, CHViewMeta):
        return

    ch = getattr(meta, "ch", None)
    if ch is None:
        raise DBWardenConfigError(
            f"{model_class.__name__}: CHViewMeta requires a 'ch' attribute "
            f"set to a materialized_view() or aggregating_view() spec."
        )
    if not isinstance(ch, (MaterializedViewSpec, AggregatingViewSpec)):
        raise DBWardenConfigError(
            f"{model_class.__name__}: 'ch' attribute must be a "
            f"MaterializedViewSpec or AggregatingViewSpec, got {type(ch).__name__}."
        )

    tablename = getattr(model_class, "__tablename__", None)
    if tablename is None:
        raise DBWardenConfigError(
            f"{model_class.__name__}: CHViewMeta model must have __tablename__ set."
        )

    # Spec↔class mismatch: forbid MaterializedView + AggregatingViewSpec etc.
    if issubclass(model_class, MaterializedView) and isinstance(ch, AggregatingViewSpec):
        raise TypeError(
            f"{model_class.__name__}: MaterializedView subclass must use "
            f"materialized_view(), not aggregating_view()."
        )
    if issubclass(model_class, AggregatingView) and isinstance(ch, MaterializedViewSpec):
        raise TypeError(
            f"{model_class.__name__}: AggregatingView subclass must use "
            f"aggregating_view(), not materialized_view()."
        )

    # Forbid SQLAlchemy declarative base inheritance.
    # ChView subclasses are deliberately NOT SQLAlchemy models:
    # introducing __table__ creates a dual citizen that can be queried
    # via session AND lives in the view registry, which is the exact
    # footgun the non-SA separation was designed to prevent.
    if hasattr(model_class, "__table__"):
        raise TypeError(
            f"{model_class.__name__}: ChView subclasses must NOT inherit "
            f"a SQLAlchemy declarative Base. "
            f"Columns are declared via mapped_column() on the class body, "
            f"but the class is NOT a SQLAlchemy model."
        )

    if isinstance(ch, MaterializedViewSpec):
        # Mode A guard: to=None requires engine
        if ch.to is None and ch.engine is None:
            raise TypeError(
                f"{model_class.__name__}: MaterializedView in Mode A (to= omitted) "
                f"requires engine; the class IS the target table."
            )
        if ch.to is not None:
            # Mode B: the class is the MV; no columns, no engine, no order_by
            if ch.engine is not None:
                raise TypeError(
                    f"{model_class.__name__}: MaterializedView in Mode B "
                    f"(to= given) must not declare engine; the engine belongs "
                    f"to the target table, which this class does not own."
                )
            if ch.order_by is not None:
                raise TypeError(
                    f"{model_class.__name__}: MaterializedView in Mode B "
                    f"(to= given) must not declare order_by; it belongs to "
                    f"the target table."
                )
            if _has_user_columns(model_class):
                raise TypeError(
                    f"{model_class.__name__}: MaterializedView in Mode B "
                    f"(to= given) must not declare columns, columns belong "
                    f"to the target table."
                )
        if ch.name is not None and ch.name != tablename:
            warnings.warn(
                f"materialized_view(name=...) is deprecated when used inside a class body. "
                f"The view name is derived from __tablename__ ('{tablename}'). "
                f"Passed name '{ch.name}' will be ignored.",
                DeprecationWarning,
                stacklevel=2,
            )
        ch.name = tablename
    elif isinstance(ch, AggregatingViewSpec):
        if _has_user_columns(model_class):
            raise TypeError(
                f"{model_class.__name__}: AggregatingView columns are "
                f"derived from group_by + aggregates, not declared. "
                f"Remove column declarations."
            )
        if ch.target_name is not None and ch.target_name != tablename:
            warnings.warn(
                f"aggregating_view(target_name=...) is deprecated when used inside a class body. "
                f"The target name is derived from __tablename__ ('{tablename}'). "
                f"Passed name '{ch.target_name}' will be ignored.",
                DeprecationWarning,
                stacklevel=2,
            )
        # Frozen dataclass: replace with target_name set
        from dataclasses import replace as _replace_spec
        meta.ch = _replace_spec(ch, target_name=tablename)
