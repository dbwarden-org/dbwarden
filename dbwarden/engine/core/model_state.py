from __future__ import annotations

import json
from typing import Any

from dbwarden.engine.core.models import IndexInfo, ModelColumn, ModelTable

STATE_FORMAT_VERSION = 2

MODEL_STATE_WARNING = (
    "This file records the schema that dbwarden expects the database to be in "
    "after the last migration. It is auto-generated and version-controlled. "
    "If accidentally deleted, restore from git (git checkout .dbwarden/model_state.*.json) "
    "or regenerate by running: dbwarden export-models. "
    "Without it, offline commands like make-migrations --offline will not work, "
    "but online operations are unaffected."
)


def model_state_json_dumps(state: dict, **extra: Any) -> str:
    data: dict[str, Any] = {"_warning": MODEL_STATE_WARNING}
    data.update(state)
    data.update(extra)
    return json.dumps(data, indent=2, default=str) + "\n"


def model_state_to_dict(tables: list[ModelTable], dbwarden_version: str = "") -> dict:
    state_tables: dict[str, dict[str, Any]] = {}
    state_indexes: dict[str, dict[str, Any]] = {}
    state_constraints: dict[str, dict[str, Any]] = {}
    state_enums: dict[str, list[str]] = {}

    for table in tables:
        state_tables[table.name] = _table_to_state_entry(table)

        for idx in table.indexes:
            idx_dict = idx.to_dict() if hasattr(idx, "to_dict") else dict(idx)
            idx_name = idx_dict.get("name") or _generated_index_key(table.name, idx_dict)
            state_indexes[idx_name] = {"table": table.name, **idx_dict}

        for i, fk in enumerate(table.foreign_keys or []):
            state_constraints[_constraint_key(table.name, "foreign_key", i, fk.get("name"))] = {
                "type": "foreign_key",
                "table": table.name,
                "columns": list(fk.get("columns", [])),
                "referenced_table": fk.get("referenced_table") or fk.get("referred_table", ""),
                "referenced_columns": list(fk.get("referenced_columns", fk.get("referred_columns", []))),
                "on_delete": fk.get("on_delete", "NO ACTION"),
                "on_update": fk.get("on_update", "NO ACTION"),
                "deferrable": bool(fk.get("deferrable", False)),
            }

        for i, uq in enumerate(table.uniques or []):
            name = uq.get("name") or f"uq_{table.name}_{'_'.join(uq.get('columns', []))}"
            state_constraints[_constraint_key(table.name, "unique", i, name)] = {
                "type": "unique",
                "table": table.name,
                "name": name,
                **{k: _serialize_value(v) for k, v in uq.items() if k != "name"},
            }

        for i, ck in enumerate(table.checks or []):
            name = ck.get("name") or f"ck_{table.name}_{i}"
            state_constraints[_constraint_key(table.name, "check", i, name)] = {
                "type": "check",
                "table": table.name,
                "name": name,
                **{("expression" if k == "sql_expression" else k): _serialize_value(v) for k, v in ck.items() if k != "name"},
            }

        for i, ex in enumerate(table.excludes or table.pg_table.get("pg_excludes", []) or []):
            name = ex.get("name") or f"ex_{table.name}_{i}"
            state_constraints[_constraint_key(table.name, "exclude", i, name)] = {
                "type": "exclude",
                "table": table.name,
                "name": name,
                "expression": ex.get("expression", ""),
            }

        for col in table.columns:
            pg_type = (col.pg_meta or {}).get("pg_type", {})
            if pg_type.get("kind") == "enum":
                type_name = pg_type.get("type_name", "")
                if type_name:
                    state_enums[type_name] = list(pg_type.get("values", []))

    return {
        "format_version": STATE_FORMAT_VERSION,
        "exported_at": __import__("datetime").datetime.now(__import__("zoneinfo").ZoneInfo("UTC")).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "dbwarden_version": dbwarden_version or "0.0.0",
        "tables": state_tables,
        "indexes": state_indexes,
        "constraints": state_constraints,
        "enums": state_enums,
    }


def normalize_model_state(state: dict[str, Any]) -> dict[str, Any]:
    format_version = int(state.get("format_version", 1) or 1)
    if format_version >= STATE_FORMAT_VERSION:
        return state

    tables = state.get("tables", {}) or {}
    normalized: dict[str, Any] = {
        "format_version": STATE_FORMAT_VERSION,
        "exported_at": state.get("exported_at"),
        "dbwarden_version": state.get("dbwarden_version", "0.0.0"),
        "tables": {},
        "indexes": {},
        "constraints": {},
        "enums": {},
    }

    for table_name, raw_table in tables.items():
        table_entry = dict(raw_table)
        columns = table_entry.get("columns", {}) or {}
        normalized_columns: dict[str, dict[str, Any]] = {}
        for col_name, col_entry in columns.items():
            normalized_col = dict(col_entry)
            normalized_col.setdefault("name", col_name)
            normalized_col.setdefault("autoincrement", None)
            pg_meta = normalized_col.pop("pg_meta", None) or normalized_col.get("pg_column") or {}
            ch_meta = normalized_col.pop("ch_meta", None) or normalized_col.get("ch_column") or {}
            my_meta = normalized_col.pop("my_meta", None) or normalized_col.get("my_column") or {}
            normalized_col["pg_column"] = {k: _serialize_value(v) for k, v in pg_meta.items()}
            normalized_col["ch_column"] = {k: _serialize_value(v) for k, v in ch_meta.items()}
            normalized_col["my_column"] = {k: _serialize_value(v) for k, v in my_meta.items()}

            pg_type = normalized_col["pg_column"].get("pg_type", {})
            if isinstance(pg_type, dict) and pg_type.get("kind") == "enum":
                type_name = pg_type.get("type_name", "")
                if type_name:
                    normalized["enums"][type_name] = list(pg_type.get("values", []))

            normalized_columns[col_name] = normalized_col

        backend_table_spec = table_entry.get("backend_table_spec", {}) or {}
        pg_excludes = list(backend_table_spec.get("pg_excludes", []) or [])
        normalized_table = {
            "name": table_name,
            "object_type": table_entry.get("object_type", "table"),
            "comment": table_entry.get("comment"),
            "backend": backend_table_spec.get("backend"),
            "backend_table_spec": backend_table_spec,
            "columns": normalized_columns,
        }
        normalized["tables"][table_name] = normalized_table

        for idx in table_entry.get("indexes", []) or []:
            idx_dict = idx if isinstance(idx, dict) else idx.to_dict()
            idx_name = idx_dict.get("name") or _generated_index_key(table_name, idx_dict)
            normalized["indexes"][idx_name] = {"table": table_name, **idx_dict}

        for i, fk in enumerate(table_entry.get("foreign_keys", []) or []):
            normalized["constraints"][_constraint_key(table_name, "foreign_key", i, fk.get("name"))] = {
                "type": "foreign_key",
                "table": table_name,
                "columns": list(fk.get("columns", fk.get("column", [] if fk.get("column") is None else [fk.get("column")]))),
                "referenced_table": fk.get("referenced_table") or fk.get("referred_table") or _parse_reference_table(fk.get("references")),
                "referenced_columns": list(fk.get("referenced_columns", fk.get("referred_columns", _parse_reference_columns(fk.get("references"))))),
                "on_delete": fk.get("on_delete", "NO ACTION"),
                "on_update": fk.get("on_update", "NO ACTION"),
                "deferrable": bool(fk.get("deferrable", False)),
            }

        for i, uq in enumerate(table_entry.get("uniques", []) or []):
            name = uq.get("name") or f"uq_{table_name}_{'_'.join(uq.get('columns', []))}"
            normalized["constraints"][_constraint_key(table_name, "unique", i, name)] = {
                "type": "unique",
                "table": table_name,
                "name": name,
                **{k: _serialize_value(v) for k, v in uq.items() if k != "name"},
            }

        for i, ck in enumerate(table_entry.get("checks", []) or []):
            name = ck.get("name") or f"ck_{table_name}_{i}"
            expression = ck.get("expression", ck.get("sql_expression", ""))
            normalized["constraints"][_constraint_key(table_name, "check", i, name)] = {
                "type": "check",
                "table": table_name,
                "name": name,
                "expression": expression,
                **{k: _serialize_value(v) for k, v in ck.items() if k not in {"name", "sql_expression"}},
            }

        for i, ex in enumerate(pg_excludes):
            name = ex.get("name") or f"ex_{table_name}_{i}"
            normalized["constraints"][_constraint_key(table_name, "exclude", i, name)] = {
                "type": "exclude",
                "table": table_name,
                "name": name,
                "expression": ex.get("expression", ""),
            }

    return normalized


def reconstruct_model_table(table_entry: dict[str, Any]) -> ModelTable:
    backend_spec = table_entry.get("backend_table_spec", {}) or {}
    indexes = [
        IndexInfo.from_dict(idx) if isinstance(idx, dict) else idx
        for idx in table_entry.get("indexes", []) or []
    ]
    columns = [reconstruct_model_column(col_entry) for _, col_entry in sorted((table_entry.get("columns", {}) or {}).items())]
    pg_table = {k: v for k, v in backend_spec.items() if k.startswith("pg_")}
    my_table = {k: v for k, v in backend_spec.items() if k.startswith("my_")}
    excludes = list(pg_table.get("pg_excludes", []) or [])
    return ModelTable(
        name=table_entry["name"],
        columns=columns,
        clickhouse_options={k: v for k, v in backend_spec.items() if k.startswith("ch_")},
        object_type=table_entry.get("object_type", "table"),
        foreign_keys=list(table_entry.get("foreign_keys", []) or []),
        indexes=indexes,
        comment=table_entry.get("comment"),
        checks=list(table_entry.get("checks", []) or []),
        uniques=list(table_entry.get("uniques", []) or []),
        excludes=excludes,
        pg_table=pg_table,
        my_table=my_table,
        schema=table_entry.get("schema"),
        pg_view_definition=table_entry.get("pg_view_definition"),
        pg_view_materialized=table_entry.get("pg_view_materialized", False),
        pg_view_auto_refresh=table_entry.get("pg_view_auto_refresh", False),
        pg_policies=list(table_entry.get("pg_policies", []) or []),
        pg_grants=list(table_entry.get("pg_grants", []) or []),
    )


def reconstruct_model_column(col_entry: dict[str, Any]) -> ModelColumn:
    return ModelColumn(
        name=col_entry["name"],
        type=col_entry.get("type", ""),
        nullable=col_entry.get("nullable", True),
        primary_key=col_entry.get("primary_key", False),
        unique=col_entry.get("unique", False),
        default=col_entry.get("default"),
        foreign_key=col_entry.get("foreign_key"),
        codec=(col_entry.get("ch_column", {}) or {}).get("ch_codec"),
        comment=col_entry.get("comment"),
        pg_meta=dict(col_entry.get("pg_column", {}) or {}),
        ch_meta=dict(col_entry.get("ch_column", {}) or {}),
        my_meta=dict(col_entry.get("my_column", {}) or {}),
        autoincrement=col_entry.get("autoincrement"),
    )


def _table_to_state_entry(table: ModelTable) -> dict[str, Any]:
    backend_table_spec: dict[str, Any] = {}
    ch_opts = table.clickhouse_options or {}
    if ch_opts:
        backend_table_spec["backend"] = "clickhouse"
        backend_table_spec.update({k: _serialize_value(v) for k, v in ch_opts.items() if k.startswith("ch_")})

    pg_table = {
        k: _serialize_value(v)
        for k, v in (table.pg_table or {}).items()
        if v is not None
    }
    if pg_table:
        backend_table_spec["backend"] = "postgresql"
        backend_table_spec.update(pg_table)

    my_table = {
        k: _serialize_value(v)
        for k, v in (table.my_table or {}).items()
        if v is not None
    }
    if my_table:
        backend_table_spec["backend"] = "mysql"
        backend_table_spec.update(my_table)

    entry = {
        "name": table.name,
        "columns": {col.name: _column_to_entry(col) for col in table.columns},
        "indexes": [idx.to_dict() if hasattr(idx, "to_dict") else idx for idx in table.indexes],
        "foreign_keys": [_serialize_value(fk) for fk in table.foreign_keys],
        "checks": [_serialize_value(ck) for ck in table.checks],
        "uniques": [_serialize_value(uq) for uq in table.uniques],
        "comment": table.comment,
        "object_type": table.object_type,
        "backend": backend_table_spec.get("backend"),
        "backend_table_spec": backend_table_spec,
        "schema": table.schema,
    }
    if table.pg_view_definition is not None:
        entry["pg_view_definition"] = table.pg_view_definition
        entry["pg_view_materialized"] = table.pg_view_materialized
        entry["pg_view_auto_refresh"] = table.pg_view_auto_refresh
    if table.pg_policies:
        entry["pg_policies"] = table.pg_policies
    if table.pg_grants:
        entry["pg_grants"] = table.pg_grants
    return entry


def _column_to_entry(col: Any) -> dict[str, Any]:
    return {
        "name": col.name,
        "type": col.type,
        "nullable": col.nullable,
        "primary_key": col.primary_key,
        "unique": col.unique,
        "default": col.default,
        "foreign_key": col.foreign_key,
        "comment": col.comment,
        "autoincrement": getattr(col, "autoincrement", None),
        "pg_column": {k: _serialize_value(v) for k, v in (getattr(col, "pg_meta", None) or {}).items() if v is not None},
        "ch_column": {k: _serialize_value(v) for k, v in (getattr(col, "ch_meta", None) or {}).items() if v is not None},
        "my_column": {k: _serialize_value(v) for k, v in (getattr(col, "my_meta", None) or {}).items() if v is not None},
    }


def _serialize_value(val: Any) -> Any:
    if hasattr(val, "to_dict"):
        return val.to_dict()
    if hasattr(val, "isoformat"):
        return val.isoformat()
    if isinstance(val, (list, tuple)):
        return [_serialize_value(v) for v in val]
    if isinstance(val, dict):
        return {k: _serialize_value(v) for k, v in val.items()}
    return val


def _normalize_type(t: str | None) -> str:
    if t is None:
        return ""
    return t.strip().lower().replace(" ", "").replace("(", "(").replace(")", ")")


def _normalize_mysql_table_value(key: str, value: Any) -> Any:
    if value is None and key in {"my_auto_increment", "my_row_format"}:
        return None
    if isinstance(value, str) and key in {"my_engine", "my_charset", "my_collate", "my_row_format"}:
        return value.lower()
    return value


def _generated_index_key(table: str, idx: dict[str, Any]) -> str:
    cols = "_".join(idx.get("columns", [])) or "anon"
    unique = "uq" if idx.get("unique") else "idx"
    return f"{table}:{unique}:{cols}:{idx.get('using') or 'btree'}"


def _constraint_key(table: str, kind: str, index: int, name: str | None) -> str:
    return f"{table}:{kind}:{name or index}"


def _parse_reference_table(reference: Any) -> str:
    if not isinstance(reference, str) or "." not in reference:
        return ""
    return reference.split(".", 1)[0]


def _parse_reference_columns(reference: Any) -> list[str]:
    if not isinstance(reference, str) or "." not in reference:
        return []
    return [reference.split(".", 1)[1]]
