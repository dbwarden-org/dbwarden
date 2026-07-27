import sys
from pathlib import Path
from typing import Any, List, Optional

from sqlalchemy.orm import declarative_base

from .path_discovery import (
    _AUTO_DISCOVER_CACHE_TTL,
    _auto_discover_cache,
    _collect_model_files,
    _find_project_root,
    _model_files_signature,
    auto_discover_model_paths,
    discover_models_in_directory,
    load_model_from_path,
)
from .extraction import (
    _apply_meta_fast,
    extract_column_info,
    extract_table_from_model,
)
from .helpers import (
    _extract_create_table_columns,
    compare_model_to_database,
    extract_tables_from_database,
    extract_tables_from_migrations,
    filter_model_tables_by_name,
    validate_model_tables_exist,
)
from .sql_generation import (
    _qualified_name,
    generate_add_column_sql,
    generate_create_table_sql,
    generate_drop_object_sql,
    generate_drop_table_sql,
)
from .type_mapping import (
    VALID_IDENTIFIER_RE,
    _get_backend_name,
    _map_sqlalchemy_type_to_backend,
    _validate_identifier,
)
from dbwarden.engine.core.models import IndexInfo, ModelColumn, ModelTable
from dbwarden.exceptions import DBWardenConfigError

# Backward-compat re-exports (formerly available on model_discovery module)
from dbwarden.engine.backends.postgresql.render import (
    _build_alter_policy_sql,
    _build_create_policy_sql,
    _build_grant_sql,
    _build_revoke_sql,
    _quote_pg,
)
from dbwarden.engine.backends.clickhouse.extract import (
    _map_sa_type_to_clickhouse,
    _render_ch_type_from_sa,
)
from dbwarden.engine.backends.clickhouse.render import (
    _format_clickhouse_expression,
)


Base = declarative_base()

_get_all_model_tables_cache: dict[
    tuple[str, tuple[str, ...], str | None],
    tuple[tuple[tuple[str, int, int], ...], list[ModelTable]],
] = {}


def get_all_model_tables(
    model_paths: Optional[List[str]] = None,
    db_name: str | None = None,
) -> List[ModelTable]:
    if model_paths is None:
        model_paths = auto_discover_model_paths()
    cwd = str(Path.cwd().resolve())
    model_files = _collect_model_files(model_paths)
    cache_key = (cwd, tuple(sorted(model_paths)), db_name)
    signature = _model_files_signature(model_files)
    cached = _get_all_model_tables_cache.get(cache_key)
    if cached is not None and cached[0] == signature:
        return list(cached[1])

    tables = []
    seen_tables = set()

    cwd = str(Path.cwd().resolve())
    if cwd not in sys.path:
        sys.path.insert(0, cwd)

    for potential_root in [cwd, str(Path(cwd).parent)]:
        if potential_root not in sys.path:
            sys.path.insert(0, potential_root)

    for model_file in model_files:
        module = load_model_from_path(model_file)
        if module is None:
            continue

        for attr in module.__dict__.values():
            if not isinstance(attr, type):
                continue
            if getattr(attr, "__module__", None) != getattr(module, "__name__", None):
                continue
            tablename = getattr(attr, "__tablename__", None)
            table_obj = getattr(attr, "__table__", None)
            if tablename is None or table_obj is None:
                continue
            if tablename in seen_tables:
                continue
            seen_tables.add(tablename)
            table = extract_table_from_model(attr, db_name=db_name)
            if table:
                tables.append(table)
                # Expand aggregating views: the model class is the MV;
                # also add the synthetic target table ModelTable.
                from dbwarden.databases.clickhouse.views import _expand_agg_target
                from dbwarden.databases.clickhouse.materialized_view import AggregatingViewSpec
                from dbwarden.schema._base import read_meta
                dw_meta = read_meta(attr)
                bt = dw_meta.backend_table if dw_meta else None
                if isinstance(bt, AggregatingViewSpec):
                    target = _expand_agg_target(attr, bt)
                    if target and target.name not in seen_tables:
                        seen_tables.add(target.name)
                        tables.append(target)
                elif isinstance(bt, dict) and "ch_agg_target" in bt and "ch_agg_mv" in bt:
                    target = _expand_agg_target(attr, bt)
                    if target and target.name not in seen_tables:
                        seen_tables.add(target.name)
                        tables.append(target)

    # Include ClickHouse views (AggregatingView, MaterializedView)
    from dbwarden.databases.clickhouse.views import ch_view_tables_from_models
    ch_tables = ch_view_tables_from_models(model_paths=model_paths, db_name=db_name)
    for t in ch_tables:
        if t.name not in seen_tables:
            seen_tables.add(t.name)
            tables.append(t)

    _get_all_model_tables_cache[cache_key] = (signature, tables)
    return tables


def get_model_table_by_name(
    table_name: str,
    model_paths: list[str] | None = None,
    db_name: str | None = None,
) -> ModelTable | None:
    if model_paths is None:
        model_paths = auto_discover_model_paths()
    cwd = str(Path.cwd().resolve())
    if cwd not in sys.path:
        sys.path.insert(0, cwd)
    for p in [cwd, str(Path(cwd).parent)]:
        if p not in sys.path:
            sys.path.insert(0, p)

    tables = get_all_model_tables(model_paths, db_name=db_name)
    for table in tables:
        if table.name == table_name:
            return table
    return None


__all__ = [
    "IndexInfo",
    "ModelColumn",
    "ModelTable",
    "DBWardenConfigError",
    "auto_discover_model_paths",
    "discover_models_in_directory",
    "load_model_from_path",
    "extract_column_info",
    "extract_table_from_model",
    "extract_tables_from_database",
    "extract_tables_from_migrations",
    "filter_model_tables_by_name",
    "validate_model_tables_exist",
    "generate_add_column_sql",
    "generate_create_table_sql",
    "generate_drop_object_sql",
    "generate_drop_table_sql",
    "get_all_model_tables",
    "get_model_table_by_name",
]
