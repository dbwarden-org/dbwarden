"""Shared extraction helpers used by all backend-specific extractors.

This module contains the database-agnostic logic for introspecting columns,
indexes, foreign keys, unique constraints, and check constraints from any
SQLAlchemy inspector. Backend-specific enrichment is handled by the
corresponding ``extract_pg``, ``extract_mysql``, or ``extract_sqlite`` modules.
"""
from __future__ import annotations

import hashlib
from typing import Any

from dbwarden.engine.backends.postgresql.extract import _is_autoincrement
from dbwarden.engine.backends.postgresql.render import _is_expression
from dbwarden.logging import get_component_logger

_snapshot_logger = get_component_logger("snapshot")


def introspect_columns(
    inspector: Any,
    table_name: str,
    pk_columns: set[str],
    *,
    schema: str | None = None,
) -> dict[str, Any]:
    """Introspect columns for a table using the SQLAlchemy inspector.

    Returns a dict mapping column names to their snapshot entries with
    type, nullable, primary_key, default, and autoincrement fields.
    """
    from dbwarden.engine.snapshot.type_normalize import normalize_type

    inspect_kw = {"schema": schema} if schema else {}
    columns_info = inspector.get_columns(table_name, **inspect_kw)

    columns_dict: dict[str, Any] = {}
    for col in columns_info:
        col_name = col["name"]
        col_type = col.get("type", "")
        raw_type_str = str(col_type)
        normalized = normalize_type(raw_type_str)
        col_type_name = normalized["type"]
        is_pk = col_name in pk_columns
        col_entry: dict[str, Any] = {
            "type": col_type_name,
            "nullable": bool(col.get("nullable", True)),
            "primary_key": is_pk,
            "default": col.get("default"),
            "autoincrement": _is_autoincrement(col),
        }
        if normalized.get("raw"):
            col_entry["raw"] = True
        if "length" in normalized:
            col_entry["length"] = normalized["length"]
        if "precision" in normalized:
            col_entry["precision"] = normalized["precision"]
        if "scale" in normalized:
            col_entry["scale"] = normalized["scale"]

        if hasattr(col_type, "enums") and col_type.enums:
            enum_values = ", ".join(repr(v) for v in col_type.enums)
            col_entry["type"] = f"enum({enum_values})"

        comment = col.get("comment")
        if comment is not None:
            col_entry["comment"] = comment

        columns_dict[col_name] = col_entry

    return columns_dict


def build_table_entry(
    columns_dict: dict[str, Any],
    pk_columns: set[str],
    *,
    schema: str | None = None,
    comment: str | None = None,
) -> dict[str, Any]:
    """Create the base table entry dict shared by all backends."""
    return {
        "columns": columns_dict,
        "primary_key": list(pk_columns) if pk_columns else [],
        "comment": comment,
        "schema": schema,
    }


def introspect_indexes(
    inspector: Any,
    table_name: str,
    pk_columns: set[str],
    constraint_index_names: set[str],
    database_type: str,
    *,
    engine: Any = None,
    connection: Any = None,
    own_engine: bool = False,
    schema: str | None = None,
) -> dict[str, dict[str, Any]]:
    """Introspect indexes for a table.

    Returns a dict mapping ``"{table_name}.{idx_name}"`` to index entries.
    """
    from dbwarden.engine.backends.postgresql.extract import pg_index_sort_option

    inspect_kw = {"schema": schema} if schema else {}
    indexes: dict[str, dict[str, Any]] = {}

    for idx in inspector.get_indexes(table_name, **inspect_kw):
        idx_name = idx.get("name", "")
        if not idx_name:
            continue
        if idx_name in constraint_index_names:
            continue
        if idx.get("unique") and set(idx.get("column_names", [])) == pk_columns:
            continue
        raw_cols = list(idx.get("column_names", []))
        raw_exprs = list(idx.get("expressions", []))
        expr = None
        clean_cols: list[str] = []
        if raw_exprs:
            for c in raw_cols:
                if c is not None:
                    clean_cols.append(c)
            exprs = [e for e in raw_exprs if e is not None]
            if len(exprs) == 1 and not clean_cols:
                expr = exprs[0]
            else:
                clean_cols.extend(exprs)
        else:
            for c in raw_cols:
                if _is_expression(c):
                    expr = c
                else:
                    clean_cols.append(c)
        idx_entry: dict[str, Any] = {
            "table": table_name,
            "name": idx_name,
            "columns": clean_cols,
            "unique": bool(idx.get("unique", False)),
        }
        if expr:
            idx_entry["expression"] = expr
        idx_dialect = idx.get("dialect_options", {})
        for k in ("postgresql_using", "mysql_using", "mariadb_using", "sqlite_using"):
            val = idx_dialect.get(k)
            if val:
                idx_entry["using"] = val
                break
        if "using" not in idx_entry:
            idx_entry["using"] = "btree"
        for k in ("postgresql_where", "sqlite_where"):
            val = idx_dialect.get(k)
            if val is None:
                continue
            where_sql = val if isinstance(val, str) else str(val)
            if where_sql:
                idx_entry["where"] = where_sql
                break
        incl = idx.get("include_columns")
        if incl:
            idx_entry["include"] = list(incl)
        for k in ("postgresql_with",):
            val = idx_dialect.get(k)
            if val:
                idx_entry["with_params"] = val
                break
        for k in ("postgresql_tablespace",):
            val = idx_dialect.get(k)
            if val:
                idx_entry["tablespace"] = val
                break
        for k in ("postgresql_nulls_not_distinct",):
            val = idx_dialect.get(k)
            if val:
                idx_entry["nulls_not_distinct"] = True
                break

        if database_type == "postgresql" and idx_name:
            _pg_c = None
            try:
                _pg_c = engine.connect() if own_engine and engine is not None else connection
                sort_rows = _pg_c.execute(
                    __import__("sqlalchemy").text("""
                        SELECT a.attname,
                               pg_index_column_has_property(i.indexrelid, k, 'asc') AS is_asc,
                               pg_index_column_has_property(i.indexrelid, k, 'nulls_first') AS nf
                        FROM pg_index i
                        CROSS JOIN LATERAL generate_series(0, i.indnkeyatts - 1) AS k
                        JOIN pg_class ci ON ci.oid = i.indexrelid
                        JOIN pg_attribute a ON a.attrelid = i.indrelid AND a.attnum = i.indkey[k]
                        WHERE ci.relname = :idxname AND i.indkey[k] <> 0
                        ORDER BY k
                    """),
                    {"idxname": idx_name},
                ).fetchall()
                sorting: dict[str, str] = {}
                for r in sort_rows:
                    option = pg_index_sort_option(r.is_asc, r.nf)
                    if option:
                        sorting[r.attname] = option
                if sorting:
                    idx_entry["column_sorting"] = sorting
            except Exception:
                pass
            finally:
                if own_engine and _pg_c is not None:
                    _pg_c.close()

        if database_type == "postgresql" and idx_name:
            _pg_c = None
            try:
                _pg_c = engine.connect() if own_engine and engine is not None else connection
                opclass_rows = _pg_c.execute(
                    __import__("sqlalchemy").text("""
                        SELECT a.attname, o.opcname
                        FROM pg_index i
                        CROSS JOIN LATERAL generate_series(0, i.indnkeyatts - 1) AS k
                        JOIN pg_class ci ON ci.oid = i.indexrelid
                        JOIN pg_attribute a ON a.attrelid = i.indrelid AND a.attnum = i.indkey[k]
                        JOIN pg_opclass o ON o.oid = i.indclass[k]
                        WHERE ci.relname = :idxname AND i.indkey[k] <> 0
                          AND COALESCE(o.opcdefault, false) = false
                        ORDER BY k
                    """),
                    {"idxname": idx_name},
                ).fetchall()
                if opclass_rows:
                    idx_entry["postgresql_ops"] = {r.attname: r.opcname for r in opclass_rows}
            except Exception:
                pass
            finally:
                if own_engine and _pg_c is not None:
                    _pg_c.close()

        if database_type == "postgresql" and idx_name:
            _pg_c = None
            try:
                _pg_c = engine.connect() if own_engine and engine is not None else connection
                row = _pg_c.execute(
                    __import__("sqlalchemy").text("""
                        SELECT d.description
                        FROM pg_index i
                        JOIN pg_class ci ON ci.oid = i.indexrelid
                        LEFT JOIN pg_description d ON d.objoid = ci.oid AND d.objsubid = 0
                            WHERE ci.relname = :idxname
                    """),
                    {"idxname": idx_name},
                ).scalar()
                if row:
                    idx_entry["comment"] = row
            except Exception:
                pass
            finally:
                if own_engine and _pg_c is not None:
                    _pg_c.close()

        indexes[f"{table_name}.{idx_name}"] = idx_entry

    return indexes


def introspect_foreign_keys(
    inspector: Any,
    table_name: str,
    *,
    schema: str | None = None,
) -> dict[str, dict[str, Any]]:
    """Introspect foreign keys for a table.

    Returns a dict mapping ``"{table_name}.{fk_name}"`` to FK entries.
    """
    inspect_kw = {"schema": schema} if schema else {}
    constraints: dict[str, dict[str, Any]] = {}

    for fk in inspector.get_foreign_keys(table_name, **inspect_kw):
        fk_name = fk.get("name", "")
        if not fk_name:
            fk_name = f"fk_{table_name}_{'_'.join(fk.get('constrained_columns', []))}"
        fk_options = fk.get("options", {})
        fk_match = fk_options.get("match")
        if fk_match and fk_match.upper() != "SIMPLE":
            fk_match = fk_match.upper()
        else:
            fk_match = None
        constraints[f"{table_name}.{fk_name}"] = {
            "type": "foreign_key",
            "name": fk_name,
            "table": table_name,
            "columns": list(fk.get("constrained_columns", [])),
            "referenced_table": fk.get("referred_table", ""),
            "referenced_columns": list(fk.get("referred_columns", [])),
            "on_delete": fk_options.get("ondelete", "NO ACTION"),
            "on_update": fk_options.get("onupdate", "NO ACTION"),
            "deferrable": bool(fk_options.get("deferrable", False)),
        }
        if fk_match:
            constraints[f"{table_name}.{fk_name}"]["match"] = fk_match

    return constraints


def introspect_unique_constraints(
    inspector: Any,
    table_name: str,
    *,
    schema: str | None = None,
) -> dict[str, dict[str, Any]]:
    """Introspect unique constraints for a table.

    Returns a dict mapping ``"{table_name}.{uq_name}"`` to UQ entries.
    """
    inspect_kw = {"schema": schema} if schema else {}
    constraints: dict[str, dict[str, Any]] = {}

    for uq in inspector.get_unique_constraints(table_name, **inspect_kw):
        uq_name = uq.get("name", "")
        if not uq_name:
            columns = list(uq.get("column_names", []) or [])
            if not columns:
                continue
            uq_name = f"uq_{table_name}_{'_'.join(columns)}"
        constraints[f"{table_name}.{uq_name}"] = {
            "type": "unique",
            "name": uq_name,
            "table": table_name,
            "columns": list(uq.get("column_names", [])),
        }

    return constraints


def introspect_check_constraints(
    inspector: Any,
    table_name: str,
    *,
    schema: str | None = None,
) -> dict[str, dict[str, Any]]:
    """Introspect check constraints for a table.

    Returns a dict mapping ``"{table_name}.{ck_name}"`` to CK entries.
    """
    inspect_kw = {"schema": schema} if schema else {}
    constraints: dict[str, dict[str, Any]] = {}

    for ck in inspector.get_check_constraints(table_name, **inspect_kw):
        ck_name = ck.get("name", "")
        if not ck_name:
            digest = hashlib.sha1(
                str(ck.get("sqltext", "")).encode("utf-8")
            ).hexdigest()[:12]
            ck_name = f"ck_{table_name}_{digest}"
        constraints[f"{table_name}.{ck_name}"] = {
            "type": "check",
            "name": ck_name,
            "table": table_name,
            "columns": [],
            "expression": ck.get("sqltext", ""),
        }

    return constraints
