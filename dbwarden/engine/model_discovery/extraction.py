import os
import time
import enum as _enum
import logging
from typing import Any, Optional

from dbwarden.config import get_database, is_strict_translation
from dbwarden.engine.backends.mysql.render import (
    append_mysql_column_attrs as _append_mysql_column_attrs,
    render_mysql_column_type as _render_mysql_column_type,
)
from dbwarden.engine.backends.postgresql.render import (
    _build_create_policy_sql,
    _build_grant_sql,
    _quote_pg,
    _render_postgres_column_type,
    generate_create_view_sql,
)
from dbwarden.engine.backends.clickhouse.extract import (
    _ch_options_from_meta,
    _detect_ch_object_type,
    _map_sa_type_to_clickhouse,
)
from dbwarden.engine.backends.clickhouse.render import (
    _render_clickhouse_projections,
    _generate_clickhouse_materialized_view_sql,
    _render_clickhouse_table_suffix,
    generate_create_dictionary_sql,
)
from dbwarden.engine.core.models import IndexInfo, ModelColumn, ModelTable
from dbwarden.logging import get_logger
from . import type_mapping as _type_mapping

_get_backend_name = lambda db_name=None: _type_mapping._get_backend_name(db_name)
_map_sqlalchemy_type_to_backend = lambda *a, **kw: _type_mapping._map_sqlalchemy_type_to_backend(*a, **kw)
_validate_identifier = lambda *a, **kw: _type_mapping._validate_identifier(*a, **kw)
from dbwarden.engine.sqlite_translation import translate_default_to_sqlite
from dbwarden.exceptions import DBWardenConfigError
from dbwarden.logging import get_logger
from dbwarden.schema._base import attach_meta, read_meta
from dbwarden.schema._meta_reader import (
    _build_dbwarden_meta,
    _collect_meta_chain,
    _get_backend_for_class,
    _merge_meta_class,
    _type_check_field_attrs,
    _write_column_info,
)


def _apply_meta_fast(cls: type) -> None:
    if getattr(cls, "__dbwarden_meta_applied__", False):
        return

    meta_chain = _collect_meta_chain(cls)
    if not meta_chain:
        return

    backend = _get_backend_for_class(cls)
    merged_table: dict[str, Any] = {}
    merged_fields: dict[str, dict[str, Any]] = {}

    for meta_cls in reversed(meta_chain):
        _merge_meta_class(meta_cls, merged_table, merged_fields, backend=backend)

    table_obj = getattr(cls, "__table__", None)
    if table_obj is not None:
        column_names = {c.name for c in table_obj.columns}
        for field_name, attrs in merged_fields.items():
            if field_name not in column_names:
                continue
            _type_check_field_attrs(field_name, attrs, cls)
            _write_column_info(table_obj.c[field_name], attrs)

    attach_meta(cls, _build_dbwarden_meta(merged_table))
    cls.__dbwarden_meta_applied__ = True


def extract_table_from_model(
    model_class: type, db_name: str | None = None
) -> Optional[ModelTable]:
    try:
        debug_timing = os.environ.get("DBWARDEN_DEBUG_TIMING")
        start = time.time()
        _apply_meta_fast(model_class)
        if debug_timing:
            get_logger(debug_level=logging.DEBUG).debug(
                "TIMING %s apply_meta %.3fs",
                model_class.__name__,
                time.time() - start,
            )
            start = time.time()
        dw_meta = read_meta(model_class)
        if debug_timing:
            get_logger(debug_level=logging.DEBUG).debug(
                "TIMING %s read_meta %.3fs",
                model_class.__name__,
                time.time() - start,
            )
            start = time.time()
        backend = _get_backend_name(db_name)
        table_name = model_class.__tablename__
        columns = []
        foreign_keys: list[dict[str, Any]] = []

        for column in model_class.__table__.columns:
            col = extract_column_info(column, db_name=db_name, backend=backend)
            if col:
                columns.append(col)
            if column.foreign_keys:
                fk = list(column.foreign_keys)[0]
                colspec = fk._colspec
                if "(" in colspec and colspec.endswith(")"):
                    ref_table, ref_col = colspec[:-1].split("(", 1)
                elif "." in colspec:
                    ref_table, ref_col = colspec.rsplit(".", 1)
                else:
                    ref_table, ref_col = colspec, "id"
                foreign_keys.append({
                    "columns": [column.name],
                    "referred_table": ref_table,
                    "referred_columns": [ref_col],
                    "on_delete": fk.ondelete or "NO ACTION",
                    "on_update": fk.onupdate or "NO ACTION",
                    "deferrable": bool(fk.deferrable),
                    "initially_deferred": str(fk.initially or "").upper() == "DEFERRED",
                    "match": fk.match,
                })
        if debug_timing:
            get_logger(debug_level=logging.DEBUG).debug(
                "TIMING %s columns %s %.3fs",
                model_class.__name__,
                len(columns),
                time.time() - start,
            )
            start = time.time()

        indexes: list[IndexInfo] = []
        clickhouse_options = {}
        object_type = "table"
        comment = getattr(dw_meta, "comment", None) if dw_meta else None
        checks = []
        uniques = []
        excludes = []
        pg_table = {}
        my_table = {}
        schema = None
        pg_view_definition = None
        pg_view_materialized = False
        pg_view_auto_refresh = False
        pg_policies: list[dict[str, Any]] | None = None
        pg_grants: list[dict[str, Any]] | None = None

        if dw_meta:
            checks = list(getattr(dw_meta, "pg_checks", []) or getattr(dw_meta, "checks", []) or getattr(dw_meta, "my_checks", []))
            uniques = list(getattr(dw_meta, "pg_uniques", []) or getattr(dw_meta, "uniques", []) or getattr(dw_meta, "my_uniques", []))
            for column in model_class.__table__.columns:
                if getattr(column, "unique", False):
                    col_name = column.name
                    if not any(col_name in u.get("columns", []) for u in uniques):
                        uniques.append({
                            "columns": [col_name],
                            "deferrable": False,
                            "initially_deferred": False,
                        })
            excludes = list(getattr(dw_meta, "pg_excludes", []))
            indexes_meta = getattr(dw_meta, "indexes", []) or []
            pg_indexes_meta = getattr(dw_meta, "pg_indexes", []) or []
            ch_indexes_meta = getattr(dw_meta, "ch_indexes", []) or []
            my_indexes_meta = getattr(dw_meta, "my_indexes", []) or []
            for idx_entry in list(indexes_meta) + list(pg_indexes_meta) + list(ch_indexes_meta) + list(my_indexes_meta):
                    if hasattr(idx_entry, "to_dict"):
                        idx_entry = idx_entry.to_dict()
                    if not isinstance(idx_entry, dict):
                        raise TypeError(
                            f"Index entries must be dicts or typed spec objects, "
                            f"got {type(idx_entry).__name__}"
                        )
                    raw_cols = list(idx_entry.get("columns", []))
                    spec_expr = idx_entry.get("expression")
                    if spec_expr:
                        clean_cols = [c for c in raw_cols if c != spec_expr]
                        expr_val = spec_expr
                    else:
                        expr_val = None
                        clean_cols = raw_cols
                    idx_info = IndexInfo(
                        name=idx_entry.get("name"),
                        columns=clean_cols or raw_cols,
                        unique=bool(idx_entry.get("unique", False)),
                        using=idx_entry.get("using"),
                        where=idx_entry.get("where"),
                        include=idx_entry.get("include"),
                        nulls_not_distinct=bool(idx_entry.get("nulls_not_distinct", False)),
                        column_sorting=dict(idx_entry.get("column_sorting", {})) if idx_entry.get("column_sorting") else None,
                        postgresql_ops=dict(idx_entry.get("postgresql_ops", {})) if idx_entry.get("postgresql_ops") else None,
                        clickhouse_type=idx_entry.get("clickhouse_type"),
                        clickhouse_granularity=idx_entry.get("clickhouse_granularity"),
                        expression=expr_val,
                    )
                    indexes.append(idx_info)
            if isinstance(dw_meta.backend_table, dict):
                excluded_pg_keys = {"pg_indexes", "pg_checks", "pg_uniques", "pg_policies", "pg_grants", "pg_storage_params"}
                pg_table = {k: v for k, v in dw_meta.backend_table.items() if k.startswith("pg_") and k not in excluded_pg_keys}
                my_table = {k: v for k, v in dw_meta.backend_table.items() if k.startswith("my_") and k not in ("my_indexes", "my_checks", "my_uniques")}
                schema = pg_table.pop("pg_schema", None)
                pg_policies = list(dw_meta.backend_table.get("pg_policies", [])) or None
                pg_grants = list(dw_meta.backend_table.get("pg_grants", [])) or None
                storage_params: dict[str, Any] = {}
                model_storage = dw_meta.backend_table.get("pg_storage_params") or {}
                if isinstance(model_storage, dict):
                    storage_params.update(model_storage)
                if "pg_fillfactor" in pg_table and "fillfactor" not in storage_params:
                    storage_params["fillfactor"] = pg_table["pg_fillfactor"]
                pg_table["pg_storage_params"] = storage_params or None
            if type(dw_meta.backend_table).__name__ == "PgViewSpec":
                object_type = "materialized_view" if dw_meta.backend_table.materialized else "view"
                pg_view_definition = dw_meta.backend_table.query
                pg_view_materialized = dw_meta.backend_table.materialized
                pg_view_auto_refresh = dw_meta.backend_table.auto_refresh
                if dw_meta.backend_table.schema:
                    schema = dw_meta.backend_table.schema
                indexes = []
                foreign_keys = []
                uniques = []
                checks = []
                excludes = []
            if not pg_table and any(k.startswith("pg_") for k in getattr(dw_meta, "table_attrs", {})):
                excluded_pg_keys = {"pg_indexes", "pg_checks", "pg_uniques", "pg_view_query", "pg_view_materialized", "pg_schema", "pg_policies", "pg_grants", "pg_storage_params"}
                pg_table = {
                    k: v for k, v in dw_meta.table_attrs.items()
                    if k.startswith("pg_") and k not in excluded_pg_keys and v is not None
                }
                if schema is None:
                    schema = pg_table.pop("pg_schema", None)
                storage_params: dict[str, Any] = {}
                attrs_storage = dw_meta.table_attrs.get("pg_storage_params") or {}
                if isinstance(attrs_storage, dict):
                    storage_params.update(attrs_storage)
                if "pg_fillfactor" in pg_table and "fillfactor" not in storage_params:
                    storage_params["fillfactor"] = pg_table["pg_fillfactor"]
                pg_table["pg_storage_params"] = storage_params or None
            if schema is None and type(dw_meta.backend_table).__name__ == "PgTableSpec":
                schema = dw_meta.backend_table.schema
            if not my_table and any(k.startswith("my_") for k in getattr(dw_meta, "table_attrs", {})):
                my_table = {
                    k: v for k, v in dw_meta.table_attrs.items()
                    if k.startswith("my_") and k not in ("my_indexes", "my_checks", "my_uniques") and v is not None
                }

            if "pg_partition" in pg_table:
                part = pg_table["pg_partition"]
                if isinstance(part, dict) and "strategy" in part:
                    part["strategy"] = part["strategy"].upper()

            if pg_policies is None:
                pg_policies = list(dw_meta.table_attrs.get("pg_policies", [])) or None
            if pg_grants is None:
                pg_grants = list(dw_meta.table_attrs.get("pg_grants", [])) or None

        if backend == "clickhouse":
            clickhouse_options = _ch_options_from_meta(model_class)
            object_type = _detect_ch_object_type(clickhouse_options)
        if debug_timing:
            get_logger(debug_level=logging.DEBUG).debug(
                "TIMING %s finalize %.3fs",
                model_class.__name__,
                time.time() - start,
            )

        return ModelTable(
            name=table_name,
            columns=columns,
            clickhouse_options=clickhouse_options,
            object_type=object_type,
            foreign_keys=foreign_keys,
            indexes=indexes,
            comment=comment,
            checks=checks,
            uniques=uniques,
            excludes=excludes,
            pg_table=pg_table,
            my_table=my_table,
            schema=schema,
            pg_view_definition=pg_view_definition,
            pg_view_materialized=pg_view_materialized,
            pg_view_auto_refresh=pg_view_auto_refresh,
            pg_policies=pg_policies,
            pg_grants=pg_grants,
        )
    except DBWardenConfigError:
        raise
    except Exception:
        return None


def extract_column_info(
    column,
    db_name: str | None = None,
    backend: str | None = None,
) -> Optional[ModelColumn]:
    try:
        name = column.name
        type_str = str(column.type)
        backend = backend or _get_backend_name(db_name)
        if backend == "postgresql":
            item_type = getattr(column.type, "item_type", None)
            if item_type is not None:
                type_str = "array"
            elif getattr(column.type, "enums", None) and getattr(column.type, "name", None):
                type_str = "enum"
            elif type_str.upper() == "JSONB":
                type_str = "jsonb"
            elif getattr(column.type, "timezone", False):
                type_str = "timestamptz"
        if backend in ("mysql", "mariadb"):
            enums = getattr(column.type, "enums", None)
            if enums:
                type_str = f"Enum({', '.join(repr(v) for v in enums)})"
        if backend == "clickhouse":
            clickhouse_type_override = column.info.get("clickhouse_type")
            if isinstance(clickhouse_type_override, str) and clickhouse_type_override.strip():
                type_str = clickhouse_type_override.strip()
        nullable = column.nullable
        primary_key = column.primary_key
        unique = column.unique
        autoincrement = column.autoincrement
        default = None
        default_str = None
        if column.default:
            default_arg = getattr(column.default, "arg", None)
            if default_arg is not None and isinstance(default_arg, _enum.Enum):
                enum_member = default_arg
                member_name = enum_member.name
                if backend == "postgresql":
                    type_name = getattr(column.type, "name", None)
                    if type_name:
                        default = f"'{member_name}'::{type_name}"
                    else:
                        default = f"'{member_name}'"
                else:
                    default = f"'{member_name}'"
            else:
                default_str = str(column.default)
        if default_str is not None:
            if default_str.startswith("ScalarElementColumnDefault"):
                import re

                match = re.search(r"ScalarElementColumnDefault\((.+)\)", default_str)
                if match:
                    value = match.group(1)
                    if value.lower() == "true":
                        default = "TRUE"
                    elif value.lower() == "false":
                        default = "FALSE"
                    elif value.isdigit():
                        default = value
                    else:
                        default = value
            elif default_str.startswith("ColumnDefault"):
                import re

                match = re.search(r"ColumnDefault\((.+)\)", default_str)
                if match:
                    default = match.group(1)
            elif default_str.startswith("CallableColumnDefault"):
                import re

                match = re.search(
                    r"CallableColumnDefault\(<function (\w+) at 0x[0-9a-f]+>\)",
                    default_str,
                )
                if match:
                    default = None
                else:
                    match = re.search(r"CallableColumnDefault\((.+)\)", default_str)
                    if match:
                        default = None
            elif default_str.startswith("ColumnElementColumnDefault"):
                import re

                match = re.search(r";\s*(\w+)\s*>", default_str)
                if match:
                    extracted = match.group(1)
                    if extracted.upper() in ("CURRENT_TIMESTAMP", "CURRENT_DATE", "CURRENT_TIME"):
                        default = extracted.upper()
                    else:
                        default = f"{extracted}()"
                else:
                    default = default_str
            else:
                default = default_str
        if column.server_default is not None:
            default = str(column.server_default.arg)

        type_str = _map_sqlalchemy_type_to_backend(
            type_str,
            is_primary_key=primary_key,
            db_name=db_name,
            autoincrement=autoincrement,
            backend=backend,
        )

        if _get_backend_name(db_name) == "sqlite":
            strict = is_strict_translation()
            translated_default, default_warning = translate_default_to_sqlite(
                default,
                strict=strict,
            )
            if default_warning:
                get_logger().warning(default_warning)
            default = translated_default

        foreign_key = None
        fk_on_delete = None
        fk_on_update = None
        if column.foreign_keys:
            fk = list(column.foreign_keys)[0]
            colspec = fk._colspec
            fk_on_delete = fk.ondelete
            fk_on_update = fk.onupdate
            if "." in colspec:
                table, col = colspec.rsplit(".", 1)
                foreign_key = f"{table}({col})"
            else:
                foreign_key = colspec

        if foreign_key and backend == "clickhouse":
            raise DBWardenConfigError(
                f"Column '{column.table.name}.{column.name}' uses ForeignKey "
                f"constraint referencing '{foreign_key}', but ClickHouse does "
                f"not support foreign key constraints. Remove ForeignKey() and "
                f"use a plain mapped_column instead; the relationship is "
                f"logical only. If this model is shared across multiple "
                f"databases, move it to its own module and configure separate "
                f"model_paths per database."
            )

        codec = None
        comment = None
        pg_meta: dict[str, Any] = {}
        ch_meta: dict[str, Any] = {}
        my_meta: dict[str, Any] = {}
        if backend == "clickhouse":
            ch_codec = column.info.get("ch_codec")
            if isinstance(ch_codec, str) and ch_codec.strip():
                codec = ch_codec.strip()
            ch_key_map = {
                "ch_codec": "ch_codec",
                "ch_default_expression": "ch_default_expression",
                "ch_materialized": "ch_materialized",
                "ch_alias": "ch_alias",
                "ch_ephemeral": "ch_ephemeral",
                "ch_ttl": "ch_ttl",
                "ch_low_cardinality": "ch_low_cardinality",
                "ch_nullable": "ch_nullable",
                "ch_type": "ch_type",
            }
            for info_key, meta_key in ch_key_map.items():
                val = column.info.get(info_key)
                if val is not None:
                    ch_meta[meta_key] = val

            if "ch_type" not in ch_meta:
                clickhouse_type_override = column.info.get("clickhouse_type")
                if isinstance(clickhouse_type_override, str) and clickhouse_type_override.strip():
                    ch_type = clickhouse_type_override.strip()
                else:
                    ch_type = _map_sa_type_to_clickhouse(column)
                ch_meta["ch_type"] = ch_type
        elif "ch_type" not in ch_meta:
            ch_meta["ch_type"] = _map_sa_type_to_clickhouse(column)

        if column.comment:
            comment = column.comment
        elif "dw_comment" in column.info:
            comment = column.info["dw_comment"]

        for key in (
            "pg_collation",
            "pg_storage",
            "pg_compression",
            "pg_generated",
            "pg_identity",
            "pg_identity_start",
            "pg_identity_increment",
            "pg_identity_min",
            "pg_identity_max",
        ):
            if key in column.info:
                val = column.info[key]
                if key == "pg_storage" and val in ("PLAIN", "EXTENDED"):
                    continue
                pg_meta[key] = val
        if backend == "postgresql":
            if item_type is not None:
                pg_meta["pg_type"] = {
                    "kind": "array",
                    "inner": item_type.__class__.__name__.lower().replace("varchar", "varchar").replace("text", "text"),
                    "dimensions": 1,
                }
            elif getattr(column.type, "enums", None) and getattr(column.type, "name", None):
                pg_meta["pg_enum_name"] = column.type.name
                pg_meta["pg_type"] = {
                    "kind": "enum",
                    "type_name": column.type.name,
                    "values": list(column.type.enums),
                }
            else:
                type_str_upper = type_str.upper()
                if type_str_upper == "TSVECTOR":
                    regconfig = getattr(column.type, "regconfig", None)
                    pg_type_entry: dict[str, Any] = {"kind": "tsvector"}
                    if regconfig:
                        pg_type_entry["config"] = str(regconfig)
                    pg_meta["pg_type"] = pg_type_entry
                elif type_str_upper == "JSONB":
                    pg_meta["pg_type"] = {"kind": "jsonb"}

        for key in (
            "my_charset",
            "my_collate",
            "my_unsigned",
            "my_on_update",
        ):
            if key in column.info:
                my_meta[key] = column.info[key]

        return ModelColumn(
            name=name,
            type=type_str,
            nullable=nullable,
            primary_key=primary_key,
            unique=unique,
            default=default,
            foreign_key=foreign_key,
            codec=codec,
            comment=comment,
            pg_meta=pg_meta,
            ch_meta=ch_meta,
            my_meta=my_meta,
            autoincrement=autoincrement,
            fk_on_delete=fk_on_delete,
            fk_on_update=fk_on_update,
        )
    except Exception:
        return None
