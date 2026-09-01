from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

from dbwarden.config import get_database
from dbwarden.connection.connection import get_db_connection, get_engine
from dbwarden.engine.backends.clickhouse.generate_models import (
    _clean_engine_full,
    _extract_ch_meta,
    _render_ch_meta,
    _strip_codec_wrapper,
)
from dbwarden.engine.backends.mariadb.generate_models import (
    extract_mariadb_meta as _extract_mariadb_meta,
)
from dbwarden.engine.backends.mysql.generate_models import (
    _render_mysql_meta,
    extract_mysql_meta as _extract_mysql_meta,
    resolve_mysql_imports as _resolve_mysql_imports,
)
from dbwarden.engine.backends.postgresql.generate_models import (
    _extract_postgresql_meta,
    _format_pg_type,
    _render_postgresql_meta,
    _resolve_postgresql_imports,
)
from dbwarden.engine.backends.sqlite.generate_models import (
    extract_sqlite_meta as _extract_sqlite_meta,
    resolve_sqlite_imports as _resolve_sqlite_imports,
)
from dbwarden.engine.core.type_parsing import (
    _CLICKHOUSE_MAP,
    _TYPE_MAP,
    _format_default,
    _parse_type,
)
from dbwarden.engine.shared.format_utils import _format_meta_value
from dbwarden.logging import get_logger
from dbwarden.output import success, warning

from dbwarden.commands.generate_models.writer import _write_models

# Re-exports for backward compatibility
from dbwarden.commands.generate_models.imports import (  # noqa: F401
    _render_imports,
    _resolve_imports,
    _resolve_mysql_imports,
    _resolve_postgresql_imports,
)
from dbwarden.commands.generate_models.renderers import (  # noqa: F401
    _format_column,
    _generate_table_code,
)
from dbwarden.engine.core.type_parsing import (  # noqa: F401
    _CLICKHOUSE_MAP,
    _TYPE_MAP,
    _format_default,
    _parse_type,
)
from dbwarden.engine.backends.postgresql.generate_models import (  # noqa: F401
    _format_pg_type,
)


def _resolve_base(base: str | None) -> tuple[str | None, str]:
    if base is None:
        return None, "Base"
    if ":" in base:
        mod_path, class_name = base.rsplit(":", 1)
        return mod_path, class_name
    return base, "Base"


def _infer_primary_key(
    pk_columns: set[str],
    unique_constraints: list[dict],
    raw_columns: list[dict],
) -> set[str]:
    if pk_columns:
        return pk_columns

    inferred: list[str] | None = None

    for uc in unique_constraints:
        cols = uc.get("column_names", [])
        if len(cols) == 1:
            col_info = next((c for c in raw_columns if c["name"] == cols[0]), None)
            if col_info and col_info.get("nullable") is False:
                inferred = cols
                break

    if not inferred:
        for uc in unique_constraints:
            cols = uc.get("column_names", [])
            if len(cols) == 1:
                inferred = cols
                break

    if not inferred and unique_constraints:
        inferred = list(unique_constraints[0].get("column_names", []))

    if not inferred:
        for col in raw_columns:
            if col["name"].lower() == "id":
                inferred = [col["name"]]
                break

    if not inferred:
        for col in raw_columns:
            if col["name"].lower().endswith("_id"):
                inferred = [col["name"]]
                break

    if not inferred:
        for col in raw_columns:
            if col.get("nullable") is False:
                inferred = [col["name"]]
                break

    if not inferred and raw_columns:
        inferred = [raw_columns[0]["name"]]

    return set(inferred) if inferred else set()


def generate_models_cmd(
    output: str = "models",
    tables: str | None = None,
    exclude_tables: str | None = None,
    clickhouse_engines: bool = False,
    relationships: bool = False,
    dialect: str | None = None,
    single_file: bool = False,
    base: str | None = None,
    database: str | None = None,
) -> None:
    config = get_database(database)
    actual_dialect = dialect or config.database_type

    if tables:
        table_filter = {t.strip() for t in tables.split(",")}
    else:
        table_filter = None

    if exclude_tables:
        exclude_set = {t.strip() for t in exclude_tables.split(",")}
    else:
        exclude_set = set()

    engine = get_engine(database)
    with engine.connect() as connection:
        if actual_dialect == "clickhouse":
            original_execute = connection.execute

            def _clickhouse_execute(statement: Any, *args: Any, **kwargs: Any) -> Any:
                if isinstance(statement, str):
                    return connection.exec_driver_sql(statement)
                return original_execute(statement, *args, **kwargs)

            connection.execute = _clickhouse_execute  # type: ignore[method-assign]
        elif actual_dialect == "postgresql" and config.postgres_schema:
            from sqlalchemy import text

            connection.execute(
                text("SET search_path TO :postgres_schema, public"),
                parameters={"postgres_schema": config.postgres_schema},
            )

        from sqlalchemy import inspect

        inspector = inspect(connection)
        all_table_names = inspector.get_table_names(schema=config.postgres_schema)

        target_tables = [t for t in all_table_names if not t.startswith(".")]
        if table_filter:
            target_tables = [t for t in target_tables if t in table_filter]
        target_tables = [t for t in target_tables if t not in exclude_set]

        if not target_tables:
            warning("No tables found matching the given criteria.")
            return

        tables_data: list[dict] = []

        for table_name in target_tables:
            columns_info: list[dict] = []

            pk_constraint = inspector.get_pk_constraint(table_name)
            if isinstance(pk_constraint, dict):
                pk_columns = set(pk_constraint.get("constrained_columns", []))
            elif isinstance(pk_constraint, (list, tuple, set)):
                pk_columns = set(pk_constraint)
            else:
                pk_columns = set()
            pk_was_inferred = False

            unique_constraints = inspector.get_unique_constraints(table_name)
            indexes_info = inspector.get_indexes(table_name)
            checks_info = inspector.get_check_constraints(table_name)
            unique_columns: set[str] = set()
            for uc in unique_constraints:
                cols = uc.get("column_names", [])
                if len(cols) == 1:
                    unique_columns.update(cols)

            raw_columns = inspector.get_columns(table_name)

            if not pk_columns:
                pk_was_inferred = True
                pk_columns = _infer_primary_key(pk_columns, unique_constraints, raw_columns)

            foreign_keys_raw = inspector.get_foreign_keys(table_name)

            fk_map: dict[str, str] = {}
            for fk in foreign_keys_raw:
                for constrained, referred in zip(
                    fk.get("constrained_columns", []),
                    fk.get("referred_columns", []) or [None] * len(fk.get("constrained_columns", [])),
                ):
                    if referred:
                        fk_map[constrained] = f"{fk['referred_table']}({referred})"

            fk_options_map: dict[str, dict[str, Any]] = {}
            for fk in foreign_keys_raw:
                options = fk.get("options", {})
                if options:
                    for c in fk.get("constrained_columns", []):
                        fk_options_map[c] = options

            for col in raw_columns:
                col_name = col["name"]
                col_type_str = str(col["type"])
                col_nullable = col.get("nullable", True)
                raw_default = col.get("default")
                col_default = raw_default
                if actual_dialect == "postgresql" and str(raw_default or "").startswith("nextval("):
                    col_default = None
                col_primary = col_name in pk_columns and not pk_was_inferred
                col_unique = col_name in unique_columns
                col_fk = fk_map.get(col_name)

                autoinc = getattr(col["type"], "autoincrement", None)
                if autoinc is None:
                    # Only treat a PostgreSQL integer PK as autoincrement if the
                    # database column actually owns a sequence (SERIAL/BIGSERIAL).
                    # Identity columns are handled separately via pg_meta.
                    if actual_dialect == "postgresql" and col_primary and col_type_str.upper() in ("INTEGER", "INT", "BIGINT"):
                        autoinc = bool(raw_default) and str(raw_default).lower().startswith("nextval(")
                    else:
                        autoinc = col_primary and col_type_str.upper() in ("INTEGER", "INT", "BIGINT")

                entry = {
                    "name": col_name,
                    "type": col_type_str,
                    "nullable": col_nullable,
                    "default": col_default,
                    "server_default": col_default,
                    "primary_key": col_primary,
                    "unique": col_unique,
                    "foreign_key": col_fk,
                    "autoincrement": autoinc,
                    "dialect": actual_dialect,
                }

                if actual_dialect == "postgresql":
                    type_obj = col["type"]
                    if getattr(type_obj, "item_type", None) is not None:
                        entry["pg_array_inner"] = str(type_obj.item_type)
                    if getattr(type_obj, "enums", None) and getattr(type_obj, "name", None):
                        entry["pg_enum_values"] = list(type_obj.enums)
                        entry["pg_enum_name"] = type_obj.name
                    if getattr(type_obj, "timezone", False):
                        entry["pg_timestamptz"] = True
                    type_str_upper = str(type_obj).upper()
                    if type_str_upper == "TSVECTOR":
                        config = getattr(type_obj, "regconfig", None)
                        pg_type_entry: dict[str, Any] = {"kind": "tsvector"}
                        if config:
                            pg_type_entry["config"] = str(config)
                        entry["pg_type"] = pg_type_entry
                    elif type_str_upper.endswith("RANGE"):
                        entry["pg_type"] = {"kind": "range"}
                    if col_name in fk_options_map:
                        entry["fk_options"] = fk_options_map[col_name]

                if actual_dialect in ("mysql", "mariadb"):
                    type_obj = col["type"]
                    if getattr(type_obj, "enums", None):
                        enum_values = ", ".join(repr(v) for v in type_obj.enums)
                        entry["type"] = f"ENUM({enum_values})"

                columns_info.append(entry)

            pg_meta: dict[str, Any] | None = None
            if actual_dialect == "postgresql":
                pg_meta, column_meta = _extract_postgresql_meta(
                    inspector,
                    connection,
                    table_name,
                    raw_columns,
                    indexes_info,
                    checks_info,
                    unique_constraints,
                )
                for col in columns_info:
                    if col["name"] in column_meta:
                        meta = dict(column_meta[col["name"]])
                        comment = meta.pop("comment", None)
                        if comment is not None:
                            col["comment"] = comment
                        if meta:
                            col["pg_meta"] = meta

            ch_options: dict = {}
            if actual_dialect == "clickhouse" and clickhouse_engines:
                ch_options = _extract_ch_meta(connection, table_name)
                # Mode B MVs (explicit TO target) must not declare columns in the
                # class API; discard reflected columns so the generated model only
                # carries the materialized_view(...) spec.
                if ch_options.get("ch_object_type") == "materialized_view" and ch_options.get("ch_to_table"):
                    columns_info = []
                else:
                    ch_columns_map: dict[str, dict] = {}
                    for ch_col in ch_options.get("columns", []):
                        ch_columns_map[ch_col["name"]] = ch_col
                    for col_entry in columns_info:
                        cname = col_entry["name"]
                        if cname in ch_columns_map:
                            ch_col = ch_columns_map[cname]
                            if ch_col.get("ch_meta"):
                                col_entry["ch_meta"] = ch_col["ch_meta"]
                            if ch_col.get("comment"):
                                col_entry["comment"] = col_entry.get("comment") or ch_col["comment"]
                    ch_pk_cols = set()
                    for key in ("ch_primary_key", "ch_order_by"):
                        pk_list = ch_options.get(key, [])
                        if pk_list:
                            if isinstance(pk_list, list):
                                ch_pk_cols.update(pk_list)
                            else:
                                ch_pk_cols.add(pk_list)
                    if ch_pk_cols:
                        for col_entry in columns_info:
                            if col_entry["name"] in ch_pk_cols:
                                col_entry["primary_key"] = True
                    elif actual_dialect == "clickhouse":
                        for col_entry in columns_info:
                            col_entry["primary_key"] = True
                            break
                ch_options.pop("columns", None)

            my_meta: dict[str, Any] | None = None
            if actual_dialect in ("mysql", "mariadb"):
                my_meta, column_meta = _extract_mysql_meta(
                    connection, table_name,
                    indexes=indexes_info,
                    checks=checks_info,
                    uniques=unique_constraints,
                )
                for col in columns_info:
                    if col["name"] in column_meta:
                        meta = dict(column_meta[col["name"]])
                        comment = meta.pop("comment", None)
                        if comment is not None:
                            col["comment"] = comment
                        if meta:
                            col["my_meta"] = meta

            sq_meta: dict[str, Any] | None = None
            if actual_dialect == "sqlite":
                sq_meta, column_meta = _extract_sqlite_meta(
                    connection, table_name,
                    indexes=indexes_info,
                    checks=checks_info,
                    uniques=unique_constraints,
                    raw_columns=columns_info,
                )
                sq_meta = sq_meta or None
                for col in columns_info:
                    meta = column_meta.get(col["name"])
                    if meta:
                        col["sq_meta"] = dict(meta)

            if pk_was_inferred and pk_columns:
                inferred_pk = list(pk_columns)
                if my_meta is not None:
                    my_meta["primary_key"] = inferred_pk
                elif pg_meta is not None:
                    pg_meta["primary_key"] = inferred_pk

            tables_data.append({
                "name": table_name,
                "columns": columns_info,
                "ch_options": ch_options if ch_options else None,
                "object_type": ch_options.get("ch_object_type", "table") if ch_options else "table",
                "dialect": actual_dialect,
                "pg_meta": pg_meta,
                "my_meta": my_meta,
                "sq_meta": sq_meta,
            })

    output_dir = Path(output)
    base_import_path, base_class_name = _resolve_base(base)
    _write_models(
        str(output_dir), tables_data,
        single_file=single_file,
        base_import_path=base_import_path,
        base_class_name=base_class_name,
    )
    success(f"Generated {len(tables_data)} model(s) in '{output_dir}/'")

    if actual_dialect == "clickhouse":
        for t in tables_data:
            if t.get("ch_options"):
                has_engine = bool(t["ch_options"].get("ch_engine") or t["ch_options"].get("ch_engine_raw"))
                if not has_engine:
                    warning(f"ClickHouse engine metadata for '{t['name']}' may be incomplete.")
