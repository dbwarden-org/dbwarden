from __future__ import annotations

from typing import Any

from dbwarden.engine.core.type_parsing import _parse_type
from dbwarden.engine.shared.format_utils import _format_meta_value, _sanitize_identifier


def _resolve_postgresql_imports(columns: list[dict]) -> set[str]:
    imports: set[str] = set()
    for col in columns:
        raw_type = str(col.get("type", ""))
        upper = raw_type.upper()
        if upper == "JSONB":
            imports.add("JSONB")
        elif upper == "UUID":
            imports.add("UUID")
        elif upper.endswith("[]") or col.get("pg_array_inner"):
            imports.add("ARRAY")
        elif upper == "TSVECTOR":
            imports.add("TSVECTOR")
        elif upper.endswith("RANGE"):
            imports.add(upper)
    return imports


def _extract_postgresql_meta(
    inspector, connection, table_name: str,
    raw_columns: list[dict], indexes: list[dict],
    checks: list[dict], uniques: list[dict],
) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    from sqlalchemy import text
    table_meta: dict[str, Any] = {}
    column_meta: dict[str, dict[str, Any]] = {}

    try:
        comment = inspector.get_table_comment(table_name)
        if comment and comment.get("text"):
            table_meta["comment"] = comment["text"]
    except Exception:
        pass

    constraint_index_names: set[str] = set()
    try:
        rows = connection.execute(
            text(
                "SELECT ci.relname "
                "FROM pg_constraint c "
                "JOIN pg_class ci ON ci.oid = c.conindid "
                "WHERE c.conrelid = CAST(:t AS regclass) "
                "AND c.contype IN ('p', 'u', 'x') "
                "AND c.conindid <> 0"
            ),
            {"t": table_name},
        ).fetchall()
        constraint_index_names = {r[0] for r in rows}
    except Exception:
        try:
            connection.rollback()
        except Exception:
            pass

    pg_indexes: list[dict[str, Any]] = []
    for idx in indexes:
        if idx.get("name") in constraint_index_names:
            continue
        dialect_options = idx.get("dialect_options", {})
        columns = [col for col in idx.get("column_names", []) if col is not None]
        entry: dict[str, Any] = {
            "name": idx.get("name"),
            "columns": columns,
            "unique": bool(idx.get("unique", False)),
        }
        expressions = [expr for expr in idx.get("expressions", []) if expr]
        if expressions:
            entry["expression"] = expressions[0] if len(expressions) == 1 else ", ".join(expressions)
        if dialect_options.get("postgresql_using"):
            entry["using"] = dialect_options["postgresql_using"]
        if dialect_options.get("postgresql_where"):
            entry["where"] = dialect_options["postgresql_where"]
        if idx.get("include_columns"):
            entry["include"] = list(idx.get("include_columns", []))
        if dialect_options.get("postgresql_nulls_not_distinct"):
            entry["nulls_not_distinct"] = True
        idx_name = idx.get("name")
        if idx_name:
            try:
                sort_rows = connection.execute(
                    text("""
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
                    parts: list[str] = []
                    if r.is_asc is False:
                        parts.append("DESC")
                    if r.nf is False:
                        parts.append("NULLS LAST")
                    elif r.nf is True:
                        parts.append("NULLS FIRST")
                    if parts:
                        sorting[r.attname] = " ".join(parts)
                if sorting:
                    entry["column_sorting"] = sorting
            except Exception:
                try:
                    connection.rollback()
                except Exception:
                    pass
        pg_indexes.append(entry)
    if pg_indexes:
        table_meta["pg_indexes"] = pg_indexes

    if checks:
        no_inherit_checks: dict[str, bool] = {}
        try:
            rows = connection.execute(
                text("SELECT conname, connoinherit FROM pg_constraint WHERE conrelid = CAST(:t AS regclass) AND contype = 'c'"),
                {"t": table_name},
            ).fetchall()
            for r in rows:
                if r[1]:
                    no_inherit_checks[r[0]] = True
        except Exception:
            try:
                connection.rollback()
            except Exception:
                pass
        table_meta["pg_checks"] = []
        for ck in checks:
            entry: dict[str, Any] = {"name": ck.get("name"), "expression": ck.get("sqltext", "")}
            if ck.get("name") in no_inherit_checks:
                entry["no_inherit"] = True
            table_meta["pg_checks"].append(entry)
    if uniques:
        table_meta["pg_uniques"] = [
            {"name": uq.get("name"), "columns": list(uq.get("column_names", []))}
            for uq in uniques
        ]

    try:
        rows = connection.execute(
            text("SELECT unnest(COALESCE(reloptions, '{}')) FROM pg_class WHERE relname = :t"),
            {"t": table_name},
        ).fetchall()
        storage_params: dict[str, Any] = {}
        for row in rows:
            kv = row[0].split("=", 1)
            if len(kv) == 2:
                key = kv[0]
                val = kv[1]
                if val.isdigit():
                    val = int(val)
                storage_params[key] = val
        if storage_params:
            table_meta["pg_storage_params"] = storage_params
    except Exception:
        try:
            connection.rollback()
        except Exception:
            pass

    try:
        row = connection.execute(
            text("SELECT relpersistence FROM pg_class WHERE relname = :t"),
            {"t": table_name},
        ).fetchone()
        if row and row[0] == 'u':
            table_meta["pg_unlogged"] = True
    except Exception:
        try:
            connection.rollback()
        except Exception:
            pass

    try:
        row = connection.execute(
            text("SELECT spcname FROM pg_tablespace t JOIN pg_class c ON c.reltablespace = t.oid WHERE c.relname = :t"),
            {"t": table_name},
        ).fetchone()
        if row:
            table_meta["pg_tablespace"] = row[0]
    except Exception:
        try:
            connection.rollback()
        except Exception:
            pass

    try:
        part_child = connection.execute(
            text(
                "SELECT parent.relname AS parent_name, pg_get_expr(child.relpartbound, child.oid) AS bound "
                "FROM pg_inherits i "
                "JOIN pg_class child ON child.oid = i.inhrelid "
                "JOIN pg_class parent ON parent.oid = i.inhparent "
                "WHERE child.relname = :t AND child.relpartbound IS NOT NULL"
            ),
            {"t": table_name},
        ).fetchone()
        if part_child:
            table_meta["pg_partition_of"] = part_child.parent_name
            table_meta["pg_partition_bound"] = part_child.bound
    except Exception:
        try:
            connection.rollback()
        except Exception:
            pass

    try:
        rows = connection.execute(
            text("SELECT inhparent::regclass::text FROM pg_inherits WHERE inhrelid = CAST(:t AS regclass)"),
            {"t": table_name},
        ).fetchall()
        parents = [r[0] for r in rows]
        if parents and "pg_partition_of" not in table_meta:
            table_meta["pg_inherits"] = parents[0]
    except Exception:
        try:
            connection.rollback()
        except Exception:
            pass

    try:
        part_row = connection.execute(
            text(
                "SELECT p.partstrat, "
                "array_agg(a.attname ORDER BY a.attnum) AS part_columns, "
                "pg_get_expr(p.partexprs, p.partrelid) AS part_expr "
                "FROM pg_partitioned_table p "
                "JOIN pg_class c ON c.oid = p.partrelid "
                "LEFT JOIN pg_attribute a ON a.attrelid = p.partrelid AND a.attnum = ANY(p.partattrs) "
                "WHERE c.relname = :t "
                "GROUP BY p.partstrat, p.partexprs, p.partrelid"
            ),
            {"t": table_name},
        ).fetchone()
        if part_row:
            strat_map = {"r": "RANGE", "l": "LIST", "h": "HASH"}
            strategy = strat_map.get(part_row[0], part_row[0])
            part_columns = list(part_row[1] or [])
            part_expr = part_row[2]
            if part_expr:
                part_columns.append(part_expr.strip())
            table_meta["pg_partition"] = {
                "strategy": strategy,
                "columns": part_columns,
            }
    except Exception:
        try:
            connection.rollback()
        except Exception:
            pass

    try:
        rows = connection.execute(
            text("SELECT conname, pg_get_constraintdef(oid) AS definition FROM pg_constraint WHERE conrelid = CAST(:t AS regclass) AND contype = 'x'"),
            {"t": table_name},
        ).fetchall()
        excludes = [{"name": r[0], "expression": r[1]} for r in rows]
        if excludes:
            table_meta["pg_excludes"] = excludes
    except Exception:
        try:
            connection.rollback()
        except Exception:
            pass

    for col in raw_columns:
        meta: dict[str, Any] = {}
        if col.get("comment"):
            meta["comment"] = col["comment"]
        collation = col.get("collation") or getattr(col.get("type"), "collation", None)
        if collation:
            meta["pg_collation"] = collation
        if isinstance(col.get("identity"), dict):
            identity = col["identity"]
            always = identity.get("always")
            if always is True:
                meta["pg_identity"] = "always"
            elif always is False:
                meta["pg_identity"] = "by_default"
            for src, dst in (("start", "pg_identity_start"), ("increment", "pg_identity_increment"), ("minvalue", "pg_identity_min"), ("maxvalue", "pg_identity_max")):
                if identity.get(src) is not None:
                    meta[dst] = identity[src]
        if isinstance(col.get("computed"), dict) and col["computed"].get("sqltext"):
            meta["pg_generated"] = col["computed"]["sqltext"]
        if meta:
            column_meta[col["name"]] = meta

    try:
        rows = connection.execute(
            text("SELECT a.attname, a.attstorage, a.attcompression FROM pg_attribute a JOIN pg_class c ON c.oid = a.attrelid WHERE c.relname = :t AND a.attnum > 0 AND NOT a.attisdropped ORDER BY a.attnum"),
            {"t": table_name},
        ).fetchall()
        storage_map = {'p': 'PLAIN', 'm': 'MAIN', 'e': 'EXTERNAL', 'x': 'EXTENDED'}
        for r in rows:
            cname = r[0]
            if cname in column_meta or True:
                meta = column_meta.get(cname, {}) or {}
                if r[1]:
                    meta["pg_storage"] = storage_map.get(r[1], r[1])
                if r[2]:
                    meta["pg_compression"] = r[2]
                if meta:
                    column_meta[cname] = meta
    except Exception:
        try:
            connection.rollback()
        except Exception:
            pass

    return table_meta, column_meta


def _format_pg_type(col: dict) -> str | None:
    if col.get("pg_enum_values") and col.get("pg_enum_name"):
        values = ", ".join(repr(v) for v in col["pg_enum_values"])
        return f"Enum({values}, name={col['pg_enum_name']!r})"
    if col.get("pg_array_inner"):
        inner = _parse_type(col["pg_array_inner"], "postgresql")
        return f"ARRAY({inner})"
    if col.get("pg_timestamptz"):
        return "DateTime(timezone=True)"
    pg_type = col.get("pg_type") or {}
    kind = pg_type.get("kind")
    if kind == "tsvector":
        config = pg_type.get("config")
        if config:
            return f"TSVECTOR({config!r})"
        return "TSVECTOR"
    if kind == "range":
        raw_type = col.get("type", "").upper().strip()
        type_map = {
            "TSTZRANGE": "TSTZRANGE",
            "TSRANGE": "TSRANGE",
            "DATERANGE": "DATERANGE",
            "INT4RANGE": "INT4RANGE",
            "INT8RANGE": "INT8RANGE",
            "NUMRANGE": "NUMRANGE",
        }
        return type_map.get(raw_type, "TSTZRANGE")
    return None


def _render_postgresql_meta(columns: list[dict], pg_meta: dict | None = None) -> list[str]:
    if not pg_meta and not any(col.get("pg_meta") or col.get("comment") for col in columns):
        return []

    lines = ["    class Meta(PGTableMeta):"]
    pg_meta = pg_meta or {}
    for key, value in pg_meta.items():
        if key == "comment":
            lines.append(f"        comment = {value!r}")
        else:
            rendered = _format_meta_value(value)
            if len(rendered) == 1:
                lines.append(f"        {key} = {rendered[0].strip()}")
            else:
                lines.append(f"        {key} = {rendered[0].strip()}")
                lines.extend(rendered[1:])

    _PG_FLAT_TO_SPEC: dict[str, str] = {
        "pg_collation": "collation",
        "pg_storage": "storage",
        "pg_compression": "compression",
        "pg_generated": "generated",
        "pg_identity": "identity",
        "pg_identity_start": "identity_start",
        "pg_identity_increment": "identity_increment",
        "pg_identity_min": "identity_min",
        "pg_identity_max": "identity_max",
    }

    for col in columns:
        field_meta: dict[str, Any] = {}
        if col.get("comment"):
            field_meta["comment"] = col["comment"]
        field_meta.update(col.get("pg_meta") or {})
        if not field_meta:
            continue
        lines.append("")
        lines.append(f"        class {_sanitize_identifier(col['name'])}(PGColumnMeta):")
        comment_val = field_meta.pop("comment", None)
        if comment_val is not None:
            lines.append(f"            comment = {comment_val!r}")
        pg_kwargs = {}
        for flat_key, spec_key in _PG_FLAT_TO_SPEC.items():
            if flat_key in field_meta:
                pg_kwargs[spec_key] = field_meta[flat_key]
        if pg_kwargs:
            kwargs_repr = ", ".join(f"{k}={v!r}" for k, v in pg_kwargs.items())
            lines.append(f"            pg = pg.field({kwargs_repr})")

    return lines
