from __future__ import annotations

import re
from typing import Any

from dbwarden.engine.backends.mysql.extract import (
    assert_complete_mysql_type as _assert_complete_mysql_type,
)
from dbwarden.engine.core.models import ModelTable
from dbwarden.engine.core.statement_order import MigrationStatement, StatementOrder

from dbwarden.engine.backends.clickhouse.render import (
    _apply_ch_nullable_low_cardinality,
    _parse_ch_type_wrappers,
)

from .index_utils import _build_index_name, _index_op_from_info
from .utils import _missing_def_placeholder, _quote_default_for_sql


def _build_alter_type_sql(
    table: str,
    column: str,
    new_type: str,
    backend: str,
    old_type: str | None = None,
    postgres_auto_using: bool = False,
) -> tuple[str, str]:
    if old_type:
        rollback_type = old_type
        rollback_comment = ""
    else:
        rollback_type = _missing_def_placeholder(backend)
        rollback_comment = "-- "

    if backend == "postgresql":
        upgrade = f"ALTER TABLE {table} ALTER COLUMN {column} TYPE {new_type}"
        if postgres_auto_using:
            upgrade += f" USING {column}::{new_type}"
        else:
            upgrade += f"\n-- USING {column}::{new_type}"
        rollback = f"{rollback_comment}ALTER TABLE {table} ALTER COLUMN {column} TYPE {rollback_type}"
    elif backend in ("mysql", "mariadb"):
        _assert_complete_mysql_type(new_type)
        upgrade = f"ALTER TABLE {table} MODIFY COLUMN {column} {new_type}"
        if old_type:
            _assert_complete_mysql_type(old_type)
        rollback = f"{rollback_comment}ALTER TABLE {table} MODIFY COLUMN {column} {rollback_type}"
    elif backend == "sqlite":
        upgrade = (
            f"-- SQLite does not support ALTER COLUMN TYPE.\n"
            f"-- Use 'dbwarden new' to write a manual migration for:\n"
            f"-- ALTER TABLE {table} ALTER COLUMN {column} TYPE {new_type}"
        )
        rollback = f"{rollback_comment}ALTER TABLE {table} ALTER COLUMN {column} TYPE {rollback_type}"
    elif backend == "clickhouse":
        upgrade = f"ALTER TABLE {table} MODIFY COLUMN {column} {new_type}"
        rollback = f"{rollback_comment}ALTER TABLE {table} MODIFY COLUMN {column} {rollback_type}"
    else:
        upgrade = f"ALTER TABLE {table} ALTER COLUMN {column} TYPE {new_type}"
        rollback = f"{rollback_comment}ALTER TABLE {table} ALTER COLUMN {column} TYPE {rollback_type}"
    return upgrade, rollback


def _build_alter_nullable_sql(
    table: str,
    column: str,
    nullable: bool,
    col_type: str,
    backend: str,
) -> tuple[str, str]:
    if backend == "postgresql":
        if nullable:
            upgrade = f"ALTER TABLE {table} ALTER COLUMN {column} DROP NOT NULL"
            rollback = f"ALTER TABLE {table} ALTER COLUMN {column} SET NOT NULL"
        else:
            upgrade = f"ALTER TABLE {table} ALTER COLUMN {column} SET NOT NULL"
            rollback = f"ALTER TABLE {table} ALTER COLUMN {column} DROP NOT NULL"
    elif backend in ("mysql", "mariadb"):
        _assert_complete_mysql_type(col_type)
        null_clause = "NULL" if nullable else "NOT NULL"
        upgrade = f"ALTER TABLE {table} MODIFY COLUMN {column} {col_type} {null_clause}"
        rollback = f"ALTER TABLE {table} MODIFY COLUMN {column} {col_type} {'NOT NULL' if nullable else 'NULL'}"
    elif backend == "sqlite":
        upgrade = f"-- SQLite: ALTER TABLE {table} ALTER COLUMN {column} {'DROP' if nullable else 'SET'} NOT NULL (not supported)"
        rollback = f"-- SQLite: ALTER TABLE {table} ALTER COLUMN {column} {'SET' if nullable else 'DROP'} NOT NULL (not supported)"
    elif backend == "clickhouse":
        base, _, low_cardinality = _parse_ch_type_wrappers(col_type or "")
        if base:
            mod_type = _apply_ch_nullable_low_cardinality(base, nullable, low_cardinality)
            rev_type = _apply_ch_nullable_low_cardinality(base, not nullable, low_cardinality)
            upgrade = f"ALTER TABLE {table} MODIFY COLUMN {column} {mod_type}"
            rollback = f"ALTER TABLE {table} MODIFY COLUMN {column} {rev_type}"
        else:
            upgrade = rollback = f"-- ClickHouse: cannot alter nullability for {table}.{column} (no type info)"
    else:
        if nullable:
            upgrade = f"ALTER TABLE {table} ALTER COLUMN {column} DROP NOT NULL"
            rollback = f"ALTER TABLE {table} ALTER COLUMN {column} SET NOT NULL"
        else:
            upgrade = f"ALTER TABLE {table} ALTER COLUMN {column} SET NOT NULL"
            rollback = f"ALTER TABLE {table} ALTER COLUMN {column} DROP NOT NULL"
    return upgrade, rollback


def _build_alter_default_sql(
    table: str,
    column: str,
    default: Any,
    backend: str,
    col_type: str | None = None,
    nullable: bool | None = None,
    my_meta: dict[str, Any] | None = None,
) -> tuple[str, str]:
    if backend in ("mysql", "mariadb"):
        from dbwarden.engine.backends.mysql.sql_build import build_mysql_alter_default_sql
        return build_mysql_alter_default_sql(
            table, column, default,
            col_type=col_type, nullable=nullable, my_meta=my_meta or {},
        )
    if default is not None:
        safe_default = _quote_default_for_sql(str(default))
        upgrade = f"ALTER TABLE {table} ALTER COLUMN {column} SET DEFAULT {safe_default}"
        rollback = f"ALTER TABLE {table} ALTER COLUMN {column} DROP DEFAULT"
    else:
        upgrade = f"ALTER TABLE {table} ALTER COLUMN {column} DROP DEFAULT"
        rollback = f"ALTER TABLE {table} ALTER COLUMN {column} SET DEFAULT {_missing_def_placeholder(backend)}"
    return upgrade, rollback


def _build_ch_projection_sql(
    table: str,
    to_val: Any,
    from_val: Any,
    up_stmts: list[str],
    rb_stmts: list[str],
) -> None:
    snap_projs: list[dict] = from_val or []
    model_projs: list[dict] = to_val or []
    snap_by_name = {p.get("name"): p for p in snap_projs}
    model_by_name = {p.get("name"): p for p in model_projs}
    for name, snap_p in snap_by_name.items():
        if name not in model_by_name:
            up_stmts.append(f"ALTER TABLE {table} DROP PROJECTION {name}")
            model_p = model_by_name.get(name)
            if model_p:
                rb_stmts.append(f"ALTER TABLE {table} ADD PROJECTION {name} {model_p.get('query', '')}")
            else:
                rb_stmts.append(f"ALTER TABLE {table} ADD PROJECTION {name} {snap_p.get('query', '')}")
    for name, model_p in model_by_name.items():
        if name not in snap_by_name:
            up_stmts.append(f"ALTER TABLE {table} ADD PROJECTION {name} {model_p.get('query', '')}")
            rb_stmts.append(f"ALTER TABLE {table} DROP PROJECTION {name}")
            continue
        snap_p = snap_by_name[name]
        if model_p.get("query") != snap_p.get("query"):
            up_stmts.append(f"ALTER TABLE {table} DROP PROJECTION {name}")
            up_stmts.append(f"ALTER TABLE {table} ADD PROJECTION {name} {model_p.get('query', '')}")
            rb_stmts.append(f"ALTER TABLE {table} DROP PROJECTION {snap_p.get('name', name)}")
            rb_stmts.append(f"ALTER TABLE {table} ADD PROJECTION {name} {snap_p.get('query', '')}")


def _build_create_table_sequence(
    table: ModelTable, db_name: str | None,
    cluster_ctx: Any = None,
) -> list[MigrationStatement]:
    from dbwarden.engine.discovery import generate_create_table_sql, generate_drop_object_sql
    from dbwarden.engine.snapshot.utils import _get_backend
    from dbwarden.engine.backends.clickhouse.cluster import ClusterableStatement
    from dbwarden.databases.clickhouse.cluster import ClusterMode

    backend = _get_backend(db_name)
    order = StatementOrder.CREATE_VIEW if table.object_type in ("view", "materialized_view") else StatementOrder.CREATE_TABLE

    upgrade = generate_create_table_sql(table, db_name)
    rollback = generate_drop_object_sql(table)

    # Apply ON CLUSTER via ClusterableStatement if configured
    if cluster_ctx is not None and cluster_ctx.mode is ClusterMode.ON_CLUSTER:
        cs = ClusterableStatement.from_sql(upgrade)
        upgrade = cs.render(cluster_ctx)
        cs_rb = ClusterableStatement.from_sql(rollback)
        rollback = cs_rb.render(cluster_ctx)

    statements: list[MigrationStatement] = [
        MigrationStatement(order=order, upgrade_sql=upgrade, rollback_sql=rollback),
    ]

    for idx in table.indexes:
        statements.extend(_build_index_sql(_index_op_from_info(idx, table.name), backend))

    return statements


def _join_creation_sql(
    table: ModelTable, db_name: str | None,
    cluster_ctx: Any = None,
) -> str:
    return "\n\n".join(
        stmt.upgrade_sql
        for stmt in _build_create_table_sequence(table, db_name, cluster_ctx=cluster_ctx)
    )


def _build_clickhouse_recreate_table_sql(
    op: dict[str, Any],
    db_name: str | None,
    cluster_ctx: Any = None,
) -> list[MigrationStatement]:
    from dbwarden.databases.clickhouse.cluster import ClusterContext, ClusterMode
    from dbwarden.engine.backends.clickhouse.cluster import ClusterableStatement
    from dbwarden.engine.discovery import generate_drop_object_sql
    from dbwarden.engine.offline import reconstruct_model_table

    def _cluster(prefix: str, suffix: str = "") -> str:
        """Render a ClusterableStatement with the given prefix+suffix."""
        cs = ClusterableStatement(prefix=prefix, suffix=suffix)
        if cluster_ctx:
            return cs.render(cluster_ctx)
        return cs.render(ClusterContext.from_config(type("_", (), {})()))

    def _cluster_sql(sql: str) -> str:
        """Parse a DDL string and apply ON CLUSTER via from_sql.

        Used for statement shapes where prefix/suffix construction is
        impractical (RENAME TABLE with multiple pairs, DROP from
        generate_drop_object_sql).  Non-DDL passes through unchanged.
        """
        import re
        if not re.match(r'\s*(CREATE|ALTER|DROP|RENAME|DETACH|ATTACH)\b', sql, re.IGNORECASE):
            return sql
        return ClusterableStatement.from_sql(sql).render(
            cluster_ctx or ClusterContext.from_config(type("_", (), {})())
        )

    table_name = op["table"]
    from_table = reconstruct_model_table(op["from_table"])
    to_table = reconstruct_model_table(op["to_table"])

    if from_table.object_type == "dictionary" or to_table.object_type == "dictionary":
        upgrade_sql = (
            f"{_cluster_sql(generate_drop_object_sql(from_table))};\n"
            f"{_join_creation_sql(to_table, db_name, cluster_ctx=cluster_ctx)};"
        )
        rollback_sql = (
            f"{_cluster_sql(generate_drop_object_sql(to_table))};\n"
            f"{_join_creation_sql(from_table, db_name, cluster_ctx=cluster_ctx)};"
        )
        return [
            MigrationStatement(
                order=StatementOrder.ALTER_TABLE_OPTIONS,
                upgrade_sql=upgrade_sql,
                rollback_sql=rollback_sql,
            )
        ]

    if from_table.object_type == "materialized_view" or to_table.object_type == "materialized_view":
        upgrade_sql = (
            f"{_cluster_sql(generate_drop_object_sql(from_table))};\n"
            f"{_join_creation_sql(to_table, db_name, cluster_ctx=cluster_ctx)};"
        )
        rollback_sql = (
            f"{_cluster_sql(generate_drop_object_sql(to_table))};\n"
            f"{_join_creation_sql(from_table, db_name, cluster_ctx=cluster_ctx)};"
        )
        return [
            MigrationStatement(
                order=StatementOrder.ALTER_TABLE_OPTIONS,
                upgrade_sql=upgrade_sql,
                rollback_sql=rollback_sql,
            )
        ]

    new_name = f"{table_name}__dbw_new"
    preserved_name = f"{table_name}{op.get('preserve_old_suffix', '__dbw_old')}"
    failed_name = f"{table_name}{op.get('failed_suffix', '__dbw_failed')}"
    drop_old_after_swap = bool(op.get("drop_old_after_swap", False))
    dependent_mvs: list[str] = op.get("dependent_mvs", [])
    rollback_kind = op.get("rollback_kind", "conditional")
    rollback_reason = op.get("rollback_reason")

    copy_columns = [col.name for col in to_table.columns if any(src.name == col.name for src in from_table.columns)]
    copy_cols_sql = ", ".join(copy_columns)

    upgrade_parts = []
    if dependent_mvs:
        upgrade_parts.append(
            ";\n".join(_cluster(f"DETACH TABLE {mv}", "") for mv in dependent_mvs) + ";"
        )
    upgrade_parts += [
        _join_creation_sql(ModelTable(
            name=new_name,
            columns=to_table.columns,
            clickhouse_options=to_table.clickhouse_options,
            object_type=to_table.object_type,
            foreign_keys=to_table.foreign_keys,
            indexes=to_table.indexes,
            comment=to_table.comment,
            checks=to_table.checks,
            uniques=to_table.uniques,
            excludes=to_table.excludes,
            pg_table=to_table.pg_table,
        ), db_name, cluster_ctx=cluster_ctx),
        # INSERT INTO is non-DDL: pass through without cluster decoration
        f"INSERT INTO {new_name} ({copy_cols_sql}) SELECT {copy_cols_sql} FROM {table_name};",
        # RENAME TABLE needs from_sql because insertion point is end-of-statement
        _cluster_sql(f"RENAME TABLE {table_name} TO {preserved_name}, {new_name} TO {table_name};"),
    ]
    if drop_old_after_swap:
        upgrade_parts.append(_cluster_sql(generate_drop_object_sql(ModelTable(name=preserved_name, columns=[], object_type=to_table.object_type))))
    else:
        upgrade_parts.append(f"-- Preserved previous table as {preserved_name}. Drop it after validation:\n-- DROP TABLE {preserved_name};")

    if dependent_mvs:
        upgrade_parts.append(
            ";\n".join(_cluster(f"ATTACH TABLE {mv}", "") for mv in dependent_mvs) + ";"
        )

    rollback_parts = []
    if dependent_mvs:
        rollback_parts.append(
            ";\n".join(_cluster(f"DETACH TABLE {mv}", "") for mv in dependent_mvs) + ";"
        )
    rollback_parts.append(_join_creation_sql(ModelTable(
        name=failed_name,
        columns=from_table.columns,
        clickhouse_options=from_table.clickhouse_options,
        object_type=from_table.object_type,
        foreign_keys=from_table.foreign_keys,
        indexes=from_table.indexes,
        comment=from_table.comment,
        checks=from_table.checks,
        uniques=from_table.uniques,
        excludes=from_table.excludes,
        pg_table=from_table.pg_table,
    ), db_name, cluster_ctx=cluster_ctx))
    rollback_copy_columns = [col.name for col in from_table.columns if any(dst.name == col.name for dst in to_table.columns)]
    rollback_cols_sql = ", ".join(rollback_copy_columns)
    rollback_parts.append(f"INSERT INTO {failed_name} ({rollback_cols_sql}) SELECT {rollback_cols_sql} FROM {table_name};")
    rollback_parts.append(_cluster_sql(f"RENAME TABLE {table_name} TO {preserved_name}, {failed_name} TO {table_name};"))
    if dependent_mvs:
        rollback_parts.append(
            ";\n".join(_cluster(f"ATTACH TABLE {mv}", "") for mv in dependent_mvs) + ";"
        )
    rollback_parts.append(f"-- Preserved forward table as {preserved_name}. Drop it after validation:\n-- DROP TABLE {preserved_name};")

    if rollback_kind == "irreversible":
        reason = rollback_reason or "ClickHouse recreate rollback was not proven row-preserving."
        rollback_sql = f"-- Irreversible ClickHouse recreate for {table_name}: {reason}"
    else:
        rollback_sql = "\n".join(rollback_parts)

    return [
        MigrationStatement(
            order=StatementOrder.ALTER_TABLE_OPTIONS,
            upgrade_sql="\n".join(upgrade_parts),
            rollback_sql=rollback_sql,
            rollback_kind=rollback_kind,
            rollback_reason=rollback_reason,
        )
    ]


def _build_index_sql(op: dict[str, Any], backend: str) -> list[MigrationStatement]:
    table = op["table"]
    columns = op.get("columns", [])
    unique = op.get("unique", False)
    using = op.get("using")
    where = op.get("where")
    include = op.get("include")
    with_params = op.get("with_params")
    tablespace = op.get("tablespace")
    nulls_not_distinct = op.get("nulls_not_distinct", False)
    column_sorting = op.get("column_sorting")
    postgresql_ops = op.get("postgresql_ops")
    concurrently = op.get("concurrently", True)
    clickhouse_type = op.get("clickhouse_type")
    clickhouse_granularity = op.get("clickhouse_granularity")
    expression = op.get("expression")
    idx_name = op.get("index_name") or _build_index_name(table, columns, unique, using, expression)

    if op["type"] == "add_index":
        if backend == "clickhouse" and clickhouse_type:
            upgrade = (
                f"ALTER TABLE {table} ADD INDEX {idx_name} "
                f"({', '.join(columns)}) "
                f"TYPE {clickhouse_type} "
                f"GRANULARITY {clickhouse_granularity or 1};"
            )
            rollback = f"ALTER TABLE {table} DROP INDEX {idx_name};"
            return [
                MigrationStatement(
                    order=StatementOrder.ALTER_INDEX,
                    upgrade_sql=upgrade,
                    rollback_sql=rollback,
                ),
            ]

        parts = ["CREATE"]
        if unique:
            parts.append("UNIQUE")
        parts.append("INDEX")
        if backend == "postgresql" and concurrently:
            parts.append("CONCURRENTLY")
        parts.append(idx_name)
        parts.append(f"ON {table}")

        if using and using != "btree":
            parts.append(f"USING {using}")

        col_parts = []
        for col in columns:
            col_sql = col
            opclass = (postgresql_ops or {}).get(col, "")
            if opclass and using:
                col_sql += f" {opclass}"
            sorting = (column_sorting or {}).get(col, "")
            if sorting:
                col_sql += f" {sorting}"
            col_parts.append(col_sql)
        if expression:
            col_parts.append(expression)
        parts.append(f"({', '.join(col_parts)})")

        if include and backend == "postgresql":
            parts.append(f"INCLUDE ({', '.join(include)})")

        if with_params and backend == "postgresql":
            opts = ", ".join(f"{k} = {v}" for k, v in with_params.items())
            parts.append(f"WITH ({opts})")

        if tablespace and backend == "postgresql":
            parts.append(f"TABLESPACE {tablespace}")

        if where:
            parts.append(f"WHERE {where}")

        if nulls_not_distinct and backend == "postgresql":
            parts.append("NULLS NOT DISTINCT")

        upgrade = " ".join(parts) + ";"
        if backend == "postgresql" and concurrently:
            from dbwarden.engine.file_parser import DBWARDEN_AUTOCOMMIT_MARKER
            upgrade = f"{DBWARDEN_AUTOCOMMIT_MARKER}\n{upgrade}"
        rollback = f"DROP INDEX {idx_name};"
        return [
            MigrationStatement(
                order=StatementOrder.ALTER_INDEX,
                upgrade_sql=upgrade,
                rollback_sql=rollback,
            ),
        ]
    else:
        if backend == "clickhouse":
            upgrade = f"ALTER TABLE {table} DROP INDEX {idx_name};"
            ch_type = using.upper() if using else "MINMAX"
            ch_granularity = op.get("granularity", 1)
            rollback = (
                f"ALTER TABLE {table} ADD INDEX {idx_name} "
                f"({', '.join(columns)}) "
                f"TYPE {ch_type} "
                f"GRANULARITY {ch_granularity};"
            )
            return [
                MigrationStatement(
                    order=StatementOrder.ALTER_INDEX,
                    upgrade_sql=upgrade,
                    rollback_sql=rollback,
                ),
            ]
        upgrade = f"DROP INDEX {idx_name};"
        unique_clause = "UNIQUE " if unique else ""
        cols_list = ", ".join(columns)
        rollback_parts = ["CREATE"]
        if unique:
            rollback_parts.append("UNIQUE")
        rollback_parts.append("INDEX")
        if backend == "postgresql" and concurrently:
            rollback_parts.append("CONCURRENTLY")
        rollback_parts.append(idx_name)
        rollback_parts.append(f"ON {table}")

        if using and using != "btree":
            rollback_parts.append(f"USING {using}")

        col_parts_rb = []
        for col in columns:
            col_sql = col
            opclass = (postgresql_ops or {}).get(col, "")
            if opclass and using:
                col_sql += f" {opclass}"
            sorting = (column_sorting or {}).get(col, "")
            if sorting:
                col_sql += f" {sorting}"
            col_parts_rb.append(col_sql)
        if expression:
            col_parts_rb.append(expression)
        rollback_parts.append(f"({', '.join(col_parts_rb)})")

        if include and backend == "postgresql":
            rollback_parts.append(f"INCLUDE ({', '.join(include)})")
        if with_params and backend == "postgresql":
            opts = ", ".join(f"{k} = {v}" for k, v in with_params.items())
            rollback_parts.append(f"WITH ({opts})")
        if tablespace and backend == "postgresql":
            rollback_parts.append(f"TABLESPACE {tablespace}")
        if where:
            rollback_parts.append(f"WHERE {where}")
        if nulls_not_distinct and backend == "postgresql":
            rollback_parts.append("NULLS NOT DISTINCT")

        rollback = " ".join(rollback_parts) + ";"
        return [
            MigrationStatement(
                order=StatementOrder.ALTER_INDEX,
                upgrade_sql=upgrade,
                rollback_sql=rollback,
            ),
        ]


def _build_safe_type_change_sql(
    table: str,
    column: str,
    new_type: str,
    backend: str,
) -> list[MigrationStatement]:
    if backend == "sqlite":
        return [
            MigrationStatement(
                order=StatementOrder.ALTER_COLUMN_TYPE,
                upgrade_sql=(
                    f"-- SQLite safe type change not supported.\n"
                    f"-- Manually recreate {table} with new type for {column}."
                ),
                rollback_sql="-- (inverse requires manual migration)",
            ),
        ]

    temp_col = f"{column}__new"
    return [
        MigrationStatement(
            order=StatementOrder.ADD_COLUMN,
            upgrade_sql=f"ALTER TABLE {table} ADD COLUMN {temp_col} {new_type}",
            rollback_sql=f"ALTER TABLE {table} DROP COLUMN {temp_col}",
        ),
        MigrationStatement(
            order=StatementOrder.ALTER_COLUMN_DEFAULT,
            upgrade_sql=f"-- Data migration needed: UPDATE {table} SET {temp_col} = CAST({column} AS {new_type})",
            rollback_sql="-- (inverse requires manual migration)",
        ),
        MigrationStatement(
            order=StatementOrder.ALTER_COLUMN_DEFAULT,
            upgrade_sql=f"-- Manually verify {temp_col} before dropping {column}",
            rollback_sql="-- (inverse requires manual migration)",
        ),
        MigrationStatement(
            order=StatementOrder.DROP_COLUMN,
            upgrade_sql=f"-- After verification: ALTER TABLE {table} DROP COLUMN {column}; ALTER TABLE {table} RENAME COLUMN {temp_col} TO {column}",
            rollback_sql="-- (inverse requires manual migration)",
        ),
    ]
