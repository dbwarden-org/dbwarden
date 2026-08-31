from dbwarden.engine.backends.mysql.render import (
    append_mysql_column_attrs as _append_mysql_column_attrs,
    mysql_auto_increment_clause as _mysql_auto_increment_clause,
    render_mysql_column_type as _render_mysql_column_type,
)
from dbwarden.engine.backends.postgresql.render import (
    _build_create_policy_sql,
    _build_grant_sql,
    _quote_pg,
    _render_postgres_column_type,
    generate_create_view_sql,
)
from dbwarden.engine.backends.clickhouse.render import (
    _ch_key_columns,
    _generate_clickhouse_materialized_view_sql,
    _make_clickhouse_key_type,
    _render_clickhouse_projections,
    _render_clickhouse_table_suffix,
    generate_create_dictionary_sql,
)
from dbwarden.engine.core.models import (
    ModelTable,
    column_foreign_key_is_table_constraint,
    column_unique_is_table_constraint,
)
from . import type_mapping as _type_mapping


def _get_backend_name(db_name=None):
    return _type_mapping._get_backend_name(db_name)

def _validate_identifier(name, field="identifier"):
    return _type_mapping._validate_identifier(name, field)


def _qualified_name(name: str, schema: str | None) -> str:
    if schema:
        return f"{schema}.{name}"
    return name


def _quote_identifier(name: str, backend: str) -> str:
    """Quote one generated SQL identifier for the selected backend."""
    if backend == "postgresql":
        return _quote_pg(name)
    safe = re.fullmatch(r"[a-z_][a-z0-9_]*", name) is not None
    reserved = {"order", "user", "select", "table", "where", "group", "from"}
    if safe and name not in reserved:
        return name
    if backend in ("mysql", "mariadb"):
        return f"`{name.replace('`', '``')}" + "`"
    if backend == "sqlite":
        return f'"{name.replace(chr(34), chr(34) + chr(34))}"'
    if backend == "clickhouse":
        return f"`{name.replace('`', '``')}" + "`"
    return name


def _qualified_generated_name(name: str, schema: str | None, backend: str) -> str:
    if schema:
        return f"{_quote_identifier(schema, backend)}.{_quote_identifier(name, backend)}"
    return _quote_identifier(name, backend)


def _append_postgresql_column_meta(col_def: str, column) -> tuple[str, bool, bool]:
    pg_meta = column.pg_meta or {}
    identity = pg_meta.get("identity") or pg_meta.get("pg_identity")
    generated = pg_meta.get("generated") or pg_meta.get("pg_generated")
    has_identity = bool(identity)
    has_generated = bool(generated)
    if has_identity:
        identity_sql = "ALWAYS" if str(identity).lower() == "always" else "BY DEFAULT"
        identity_options: list[str] = []
        for key, sql_key in (
            ("identity_start", "START WITH"),
            ("pg_identity_start", "START WITH"),
            ("identity_increment", "INCREMENT BY"),
            ("pg_identity_increment", "INCREMENT BY"),
            ("identity_min", "MINVALUE"),
            ("pg_identity_min", "MINVALUE"),
            ("identity_max", "MAXVALUE"),
            ("pg_identity_max", "MAXVALUE"),
        ):
            if pg_meta.get(key) is not None:
                identity_options.append(f"{sql_key} {pg_meta[key]}")
        col_def += f" GENERATED {identity_sql} AS IDENTITY"
        if identity_options:
            col_def += " (" + " ".join(identity_options) + ")"
    elif has_generated:
        col_def += f" GENERATED ALWAYS AS ({generated}) STORED"
    return col_def, has_identity, has_generated


def _postgres_serial_type(column, col_type: str, *, allow_composite: bool = True) -> str:
    if not column.primary_key or column.autoincrement not in (True, "auto"):
        return col_type
    if not allow_composite:
        return col_type
    pg_meta = column.pg_meta or {}
    if pg_meta.get("identity") or pg_meta.get("pg_identity"):
        return col_type
    normalized = col_type.lower().replace(" ", "")
    if normalized in ("biginteger", "bigint"):
        return "BIGSERIAL"
    if normalized in ("integer", "int", "int4"):
        return "SERIAL"
    if normalized in ("smallinteger", "smallint", "int2"):
        return "SMALLSERIAL"
    return col_type


def generate_add_column_sql(
    table_name: str, column: ModelTable, db_name: str | None = None,
    schema: str | None = None,
) -> str:
    _validate_identifier(table_name, "table_name")
    _validate_identifier(column.name, "column_name")

    backend = _get_backend_name(db_name)
    if _type_mapping.is_sqlite_target(db_name):
        from dbwarden.engine.backends.sqlite.sql_build import build_sqlite_add_column_sql

        return build_sqlite_add_column_sql(table_name, column, schema)
    if backend == "clickhouse":
        col_type = column.ch_meta.get("ch_type", column.type)
    elif backend in ("mysql", "mariadb"):
        col_type = _render_mysql_column_type(column.type, column.my_meta)
    elif backend == "postgresql":
        col_type = _render_postgres_column_type(column)
        col_type = _postgres_serial_type(column, col_type)
    else:
        col_type = column.type
    is_serial = (
        column.type.upper() in ("SERIAL", "BIGSERIAL")
        if backend == "postgresql"
        else False
    )

    nullable_sql = "" if column.nullable or is_serial or backend == "clickhouse" else "NOT NULL"
    default_sql = f" DEFAULT {column.default}" if column.default and backend != "clickhouse" else ""
    fk_sql = ""
    if column.foreign_key and backend != "postgresql":
        fk_sql = f" REFERENCES {column.foreign_key}"
        if column.fk_on_delete and column.fk_on_delete != "NO ACTION":
            fk_sql += f" ON DELETE {column.fk_on_delete}"
        if column.fk_on_update and column.fk_on_update != "NO ACTION":
            fk_sql += f" ON UPDATE {column.fk_on_update}"
    if backend == "postgresql":
        qtable = _quote_pg(table_name)
        qschema = _quote_pg(schema) if schema else None
        qname = _qualified_name(qtable, qschema)
        col_name = _quote_pg(column.name)
    else:
        qname = _qualified_generated_name(table_name, schema, backend)
        col_name = _quote_identifier(column.name, backend)
    sql = f"ALTER TABLE {qname} ADD COLUMN {col_name} {col_type}"
    if backend == "postgresql":
        sql, has_identity, has_generated = _append_postgresql_column_meta(sql, column)
        if has_generated:
            default_sql = ""
    if nullable_sql:
        sql += f" {nullable_sql}"
    if default_sql:
        sql += default_sql
    if backend == "clickhouse":
        ch_meta = column.ch_meta
        for key, keyword in (
            ("ch_default_expression", "DEFAULT"),
            ("ch_materialized", "MATERIALIZED"),
            ("ch_alias", "ALIAS"),
            ("ch_ephemeral", "EPHEMERAL"),
        ):
            val = ch_meta.get(key)
            if val:
                sql += f" {keyword} {val}"
        if column.comment:
            sql += f" COMMENT '{column.comment.replace(chr(39), chr(39) + chr(39))}'"
        ch_codec = column.codec or ch_meta.get("ch_codec")
        if ch_codec:
            sql += f" CODEC({ch_codec})"
        ch_ttl = ch_meta.get("ch_ttl")
        if ch_ttl:
            sql += f" TTL {ch_ttl}"
    if backend in ("mysql", "mariadb"):
        sql = _append_mysql_column_attrs(sql, column.my_meta)
    if fk_sql:
        sql += fk_sql
    return sql


def generate_create_table_sql(table: ModelTable, db_name: str | None = None) -> str:
    backend = _get_backend_name(db_name)

    if backend == "clickhouse" and table.object_type == "dictionary":
        return generate_create_dictionary_sql(table)

    if table.object_type == "table" and _type_mapping.is_sqlite_target(db_name):
        from dbwarden.engine.backends.sqlite.sql_build import build_sqlite_create_table_sql

        return build_sqlite_create_table_sql(table)

    column_defs = []

    primary_key_columns = [col.name for col in table.columns if col.primary_key]
    composite_primary_key = backend != "clickhouse" and len(primary_key_columns) > 1

    ch_key_columns: set[str] = set()
    if backend == "clickhouse":
        ch_key_columns = _ch_key_columns(table.clickhouse_options)

    for col in table.columns:
        is_ch_key_column = backend == "clickhouse" and col.name in ch_key_columns
        if backend == "clickhouse":
            col_type = col.ch_meta.get("ch_type", col.type)
            if is_ch_key_column:
                col_type = _make_clickhouse_key_type(col_type)
        elif backend in ("mysql", "mariadb"):
            col_type = _render_mysql_column_type(col.type, col.my_meta)
        elif backend == "postgresql":
            col_type = _render_postgres_column_type(col)
            col_type = _postgres_serial_type(col, col_type, allow_composite=not composite_primary_key)
        else:
            col_type = col.type
        col_name = _quote_identifier(col.name, backend)
        col_def = f"    {col_name} {col_type}"
        is_serial = (
            col.type.upper() in ("SERIAL", "BIGSERIAL")
            if backend == "postgresql"
            else False
        )

        has_identity = False
        has_generated = False
        if backend == "postgresql":
            col_def, has_identity, has_generated = _append_postgresql_column_meta(col_def, col)
        if not col.nullable and not is_serial and backend != "clickhouse":
            col_def += " NOT NULL"
        if backend != "clickhouse" and col.primary_key and not composite_primary_key:
            col_def += " PRIMARY KEY"
        elif col.unique and not column_unique_is_table_constraint(table, col.name):
            col_def += " UNIQUE"
        if backend != "clickhouse" and col.default and not is_serial and not has_identity and not has_generated:
            col_def += f" DEFAULT {col.default}"
        if backend == "clickhouse":
            for key, keyword in (
                ("ch_default_expression", "DEFAULT"),
                ("ch_materialized", "MATERIALIZED"),
                ("ch_alias", "ALIAS"),
                ("ch_ephemeral", "EPHEMERAL"),
            ):
                val = col.ch_meta.get(key)
                if val:
                    col_def += f" {keyword} {val}"
            if is_ch_key_column:
                col_def += " NOT NULL"
        if backend in ("mysql", "mariadb"):
            col_def += _mysql_auto_increment_clause(
                col, is_sole_primary_key=not composite_primary_key,
            )
            col_def = _append_mysql_column_attrs(col_def, col.my_meta)
        if (
            col.foreign_key
            and backend != "postgresql"
            and not column_foreign_key_is_table_constraint(table, col.name)
        ):
            col_def += f" REFERENCES {col.foreign_key}"
            if col.fk_on_delete and col.fk_on_delete != "NO ACTION":
                col_def += f" ON DELETE {col.fk_on_delete}"
            if col.fk_on_update and col.fk_on_update != "NO ACTION":
                col_def += f" ON UPDATE {col.fk_on_update}"
        if backend == "clickhouse":
            if col.comment:
                col_def += f" COMMENT '{col.comment.replace(chr(39), chr(39) + chr(39))}'"
            if col.codec:
                col_def += f" CODEC({col.codec})"
            ch_ttl = col.ch_meta.get("ch_ttl")
            if ch_ttl:
                col_def += f" TTL {ch_ttl}"
        column_defs.append(col_def)

    if backend == "clickhouse":
        column_defs.extend(f"    {projection_sql}" for projection_sql in _render_clickhouse_projections(table))

    if composite_primary_key:
        pk_cols = ", ".join(_quote_identifier(c, backend) for c in primary_key_columns)
        column_defs.append(f"    PRIMARY KEY ({pk_cols})")

    columns_sql = ",\n".join(column_defs)
    if backend == "postgresql":
        qtable = _quote_pg(table.name)
        qschema = _quote_pg(table.schema) if table.schema else None
    else:
        qtable = _quote_identifier(table.name, backend)
        qschema = _quote_identifier(table.schema, backend) if table.schema else None
    qname = _qualified_name(qtable, qschema)
    if backend == "clickhouse" and table.object_type == "materialized_view":
        sql = _generate_clickhouse_materialized_view_sql(table, columns_sql)
    elif backend == "postgresql" and table.object_type in ("view", "materialized_view"):
        return generate_create_view_sql(table, qname)
    elif backend == "postgresql" and table.pg_table and table.pg_table.get("pg_partition_of"):
        parent = table.pg_table["pg_partition_of"]
        bound = table.pg_table.get("pg_partition_bound") or "DEFAULT"
        qparent = _quote_pg(parent) if "." not in parent else ".".join(_quote_pg(p) for p in parent.split("."))
        sql = f"CREATE TABLE IF NOT EXISTS {qname} PARTITION OF {qparent} {bound}"
    else:
        unlogged = "UNLOGGED " if table.pg_table and table.pg_table.get("pg_unlogged") else ""
        sql = f"CREATE {unlogged}TABLE IF NOT EXISTS {qname} (\n{columns_sql}\n)"
    if backend == "clickhouse":
        if table.object_type == "table":
            sql += _render_clickhouse_table_suffix(table)
        if table.comment:
            sql += f" COMMENT '{table.comment.replace(chr(39), chr(39) + chr(39))}'"
    if backend in ("mysql", "mariadb") and table.my_table:
        parts: list[str] = []
        if table.my_table.get("my_engine"):
            parts.append(f"ENGINE={table.my_table['my_engine']}")
        if table.my_table.get("my_charset"):
            parts.append(f"DEFAULT CHARSET={table.my_table['my_charset']}")
        if table.my_table.get("my_collate"):
            parts.append(f"COLLATE={table.my_table['my_collate']}")
        if table.my_table.get("my_row_format"):
            parts.append(f"ROW_FORMAT={table.my_table['my_row_format']}")
        if table.my_table.get("my_auto_increment") is not None:
            parts.append(f"AUTO_INCREMENT={table.my_table['my_auto_increment']}")
        if parts:
            sql += " " + " ".join(parts)
    if backend == "postgresql" and table.pg_table:
        pg_partition = table.pg_table.get("pg_partition")
        if pg_partition:
            strategy = pg_partition.get("strategy", "RANGE")
            columns = pg_partition.get("columns", [])
            quoted_cols = ", ".join(_quote_pg(c) for c in columns)
            sql += f"\nPARTITION BY {strategy} ({quoted_cols})"
        pg_inherits = table.pg_table.get("pg_inherits")
        if pg_inherits:
            parent = _quote_pg(pg_inherits) if isinstance(pg_inherits, str) else ", ".join(_quote_pg(p) for p in pg_inherits)
            sql += f"\nINHERITS ({parent})"
        pg_tablespace = table.pg_table.get("pg_tablespace")
        if pg_tablespace:
            sql += f"\nTABLESPACE {pg_tablespace}"
    if backend == "postgresql" and table.comment:
        sql += f";\nCOMMENT ON TABLE {qname} IS '{table.comment.replace(chr(39), chr(39) + chr(39))}';"
    if backend == "postgresql":
        for col in table.columns:
            if col.comment:
                col_name = _quote_pg(col.name)
                comment = col.comment.replace(chr(39), chr(39) + chr(39))
                if not sql.endswith(";"):
                    sql += ";"
                sql += f"\nCOMMENT ON COLUMN {qname}.{col_name} IS '{comment}';"
    if backend == "postgresql":
        pg_rls = table.pg_table.get("pg_rls", False)
        pg_rls_force = table.pg_table.get("pg_rls_force", False)
        pg_suffix = ""
        if pg_rls:
            pg_suffix += f"\nALTER TABLE {qname} ENABLE ROW LEVEL SECURITY;"
        if pg_rls_force:
            pg_suffix += f"\nALTER TABLE {qname} FORCE ROW LEVEL SECURITY;"
        for policy in table.pg_policies:
            pg_suffix += f"\n{_build_create_policy_sql(policy, qname)}"
        for grant_entry in table.pg_grants:
            pg_suffix += f"\n{_build_grant_sql(grant_entry, qname)}"
        if pg_suffix:
            if not sql.endswith(";"):
                sql += ";"
            sql += pg_suffix
    if not sql.endswith(";"):
        sql += ";"
    return sql


def generate_drop_table_sql(table_name: str, schema: str | None = None) -> str:
    _validate_identifier(table_name, "table_name")
    backend = _get_backend_name(None)
    qname = _qualified_generated_name(table_name, schema, backend)
    return f"DROP TABLE {qname}"


def generate_drop_object_sql(table: ModelTable) -> str:
    backend = _get_backend_name(None)
    qname = _qualified_generated_name(table.name, table.schema, backend)
    if table.object_type == "materialized_view" and table.pg_view_materialized:
        return f"DROP MATERIALIZED VIEW IF EXISTS {qname}"
    if table.object_type in ("view", "materialized_view"):
        return f"DROP VIEW IF EXISTS {qname}"
    if table.object_type == "dictionary":
        return f"DROP DICTIONARY {qname}"
    return generate_drop_table_sql(table.name, table.schema)
import re
