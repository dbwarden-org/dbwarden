"""DDL construction for the SQLite backend.

SQLite's ``ALTER TABLE`` only supports RENAME TABLE, RENAME COLUMN, ADD COLUMN
and DROP COLUMN.  Every other schema change - a column's type, its nullability,
its default, a table constraint, ``WITHOUT ROWID``, ``STRICT`` - is expressed by
rebuilding the table: create the new shape under a temporary name, copy the
rows, drop the original, rename into place, recreate the indexes.  That
procedure is generated here so a SQLite migration is executable SQL rather than
a comment telling the user to write it themselves.
"""

from __future__ import annotations

from typing import Any

from dbwarden.engine.backends.sqlite.render import (
    is_generated,
    is_stored_generated,
    is_strict_table,
    quote_sq,
    render_sqlite_column_def,
    render_sqlite_default,
    render_sqlite_table_suffix,
    sqlite_autoincrement_column,
)
from dbwarden.engine.core.models import column_unique_is_table_constraint
from dbwarden.engine.core.statement_order import MigrationStatement, StatementOrder

REBUILD_SUFFIX = "__dbw_new"


def build_sqlite_create_table_sql(table: Any, *, if_not_exists: bool = True) -> str:
    """Render a full ``CREATE TABLE`` for SQLite.

    Table constraints are inlined rather than added afterwards: SQLite has no
    ``ALTER TABLE ADD CONSTRAINT``, so a constraint that is not in the CREATE
    statement can only be introduced by a rebuild.
    """
    strict = is_strict_table(table)
    autoincrement_column = sqlite_autoincrement_column(table)
    primary_key_columns = [col.name for col in table.columns if col.primary_key]
    inline_pk = len(primary_key_columns) == 1

    body: list[str] = []
    for col in table.columns:
        body.append("    " + render_sqlite_column_def(
            col,
            strict=strict,
            inline_primary_key=inline_pk,
            autoincrement=col.name == autoincrement_column,
            table_has_unique_constraint=column_unique_is_table_constraint(table, col.name),
        ))

    if not inline_pk and primary_key_columns:
        pk_sql = ", ".join(quote_sq(c) for c in primary_key_columns)
        body.append(f"    PRIMARY KEY ({pk_sql})")

    for unique in table.uniques or []:
        columns = unique.get("columns") or []
        if not columns:
            continue
        cols_sql = ", ".join(quote_sq(c) for c in columns)
        name = unique.get("name")
        prefix = f"CONSTRAINT {quote_sq(name)} " if name else ""
        body.append(f"    {prefix}UNIQUE ({cols_sql})")

    for check in table.checks or []:
        expression = check.get("expression") or check.get("sql_expression")
        if not expression:
            continue
        name = check.get("name")
        prefix = f"CONSTRAINT {quote_sq(name)} " if name else ""
        body.append(f"    {prefix}CHECK ({expression})")

    for fk in table.foreign_keys or []:
        columns = list(fk.get("columns") or [])
        if not columns:
            continue
        # Single-column foreign keys are already rendered inline on the column
        # itself; emitting them again here would duplicate the constraint.
        if len(columns) == 1 and any(
            col.name == columns[0] and col.foreign_key for col in table.columns
        ):
            continue
        referred_table = fk.get("referred_table") or fk.get("referenced_table") or ""
        referred_columns = list(
            fk.get("referred_columns") or fk.get("referenced_columns") or ["id"]
        )
        cols_sql = ", ".join(quote_sq(c) for c in columns)
        ref_cols_sql = ", ".join(quote_sq(c) for c in referred_columns)
        clause = (
            f"    FOREIGN KEY ({cols_sql}) "
            f"REFERENCES {quote_sq(referred_table)} ({ref_cols_sql})"
        )
        on_delete = fk.get("on_delete")
        on_update = fk.get("on_update")
        if on_delete and on_delete != "NO ACTION":
            clause += f" ON DELETE {on_delete}"
        if on_update and on_update != "NO ACTION":
            clause += f" ON UPDATE {on_update}"
        body.append(clause)

    columns_sql = ",\n".join(body)
    qname = quote_sq(table.name)
    suffix = render_sqlite_table_suffix(table)
    exists_clause = "IF NOT EXISTS " if if_not_exists else ""
    return f"CREATE TABLE {exists_clause}{qname} (\n{columns_sql}\n){suffix};"


def build_sqlite_add_column_sql(
    table_name: str, column: Any, schema: str | None = None
) -> str:
    """Render ``ALTER TABLE ... ADD COLUMN`` for SQLite."""
    qname = quote_sq(table_name)
    if schema:
        qname = f"{quote_sq(schema)}.{qname}"
    col_def = render_sqlite_column_def(column, inline_primary_key=False)
    return f"ALTER TABLE {qname} ADD COLUMN {col_def}"


def add_column_requires_rebuild(column: Any) -> str | None:
    """Return why ``ADD COLUMN`` cannot express this column, or ``None``.

    The restrictions are SQLite's own: a column added to an existing table
    cannot be a primary key or unique, cannot be ``NOT NULL`` without a
    default, cannot take a non-constant default, and cannot be a STORED
    generated column.
    """
    if column.primary_key:
        return "a primary key column cannot be added by ALTER TABLE"
    if column.unique:
        return "a UNIQUE column cannot be added by ALTER TABLE"
    if is_stored_generated(column):
        return "a STORED generated column cannot be added by ALTER TABLE"
    if not column.nullable and column.default is None:
        return "a NOT NULL column without a default cannot be added by ALTER TABLE"
    default = column.default
    if default is not None and str(default).strip():
        rendered = render_sqlite_default(default)
        if rendered.startswith("(") or rendered.upper() in (
            "CURRENT_TIMESTAMP", "CURRENT_DATE", "CURRENT_TIME",
        ):
            return "a non-constant DEFAULT cannot be added by ALTER TABLE"
    return None


def drop_column_requires_rebuild(
    column_name: str,
    table_entry: dict[str, Any] | None,
) -> str | None:
    """Return why ``DROP COLUMN`` cannot express this drop, or ``None``.

    ``table_entry`` is the table's model-state entry as it was before the
    change.  SQLite refuses to drop a column that is part of the primary key,
    is named by an index or a table constraint, or is referenced by a generated
    column.
    """
    table_entry = table_entry or {}

    column_entry = (table_entry.get("columns") or {}).get(column_name) or {}
    if column_entry.get("primary_key"):
        return "a primary key column cannot be dropped by ALTER TABLE"
    if column_name in (table_entry.get("primary_key") or []):
        return "a primary key column cannot be dropped by ALTER TABLE"

    for index in table_entry.get("indexes") or []:
        if column_name in (index.get("columns") or []):
            return "an indexed column cannot be dropped by ALTER TABLE"
        if column_name in str(index.get("expression") or ""):
            return "a column used by an index expression cannot be dropped by ALTER TABLE"

    for key in ("foreign_keys", "uniques", "checks"):
        for constraint in table_entry.get(key) or []:
            if column_name in (constraint.get("columns") or []):
                return "a column named by a table constraint cannot be dropped by ALTER TABLE"
            expression = constraint.get("expression") or constraint.get("sql_expression") or ""
            if column_name in str(expression):
                return "a column named by a CHECK constraint cannot be dropped by ALTER TABLE"

    for other_name, other in (table_entry.get("columns") or {}).items():
        generated = (other.get("sq_column") or other.get("sq_meta") or {}).get("sq_generated")
        if generated and column_name in str(generated):
            return (
                f"a column referenced by generated column '{other_name}' "
                "cannot be dropped by ALTER TABLE"
            )

    return None


def _render_index(table_name: str, index: Any) -> tuple[str, str] | None:
    """Render one ``CREATE INDEX`` from a modelled index. Returns (name, sql)."""
    from dbwarden.engine.snapshot.index_utils import _build_index_name

    columns = list(index.columns or [])
    target = index.expression or ", ".join(quote_sq(c) for c in columns)
    if not target:
        return None
    name = index.name or _build_index_name(
        table_name, columns, index.unique, index.using, index.expression
    )
    unique_sql = "UNIQUE " if index.unique else ""
    sql = (
        f"CREATE {unique_sql}INDEX IF NOT EXISTS {quote_sq(name)} "
        f"ON {quote_sq(table_name)} ({target})"
    )
    if index.where:
        sql += f" WHERE {index.where}"
    return name, sql + ";"


def _index_statements(
    from_table: Any,
    to_table: Any,
    index_ddl: dict[str, str] | None = None,
) -> list[str]:
    """Render the ``CREATE INDEX`` statements a rebuilt table must end up with.

    ``DROP TABLE`` takes the table's indexes with it, so every index the table
    should still have is recreated here.  Reflection of a SQLite index is lossy
    - it drops expression indexes entirely and loses ``DESC`` and ``COLLATE``
    inside an index - so an index the migration is not changing is recreated
    from its stored DDL rather than from the reflected shape.  Only an index the
    model actually changes is rendered from the model.
    """
    index_ddl = index_ddl or {}
    from_by_name = {idx.name: idx for idx in (from_table.indexes or []) if idx.name}

    statements: list[str] = []
    rendered_names: set[str] = set()
    for index in to_table.indexes or []:
        rendered = _render_index(to_table.name, index)
        if rendered is None:
            continue
        name, sql = rendered
        rendered_names.add(name)
        previous = from_by_name.get(name)
        unchanged = previous is not None and previous.to_dict() == index.to_dict()
        if unchanged and name in index_ddl:
            statements.append(_ddl_statement(index_ddl[name]))
        else:
            statements.append(sql)

    # Indexes the model cannot express - an expression index, for instance - are
    # invisible to the diff but real in the database. Recreate them verbatim
    # rather than losing them to the rebuild.
    for name, ddl in index_ddl.items():
        if name in rendered_names or name in from_by_name:
            continue
        statements.append(_ddl_statement(ddl))

    return statements


def _ddl_statement(ddl: str) -> str:
    text = ddl.strip().rstrip(";")
    return text + ";"


def _copyable_columns(from_table: Any, to_table: Any) -> list[str]:
    """Columns present in both shapes and writable in the target.

    Generated columns are computed by SQLite, so they are neither selected nor
    inserted.
    """
    source_names = {col.name for col in from_table.columns if not is_generated(col)}
    return [
        col.name
        for col in to_table.columns
        if not is_generated(col) and col.name in source_names
    ]


def build_sqlite_rebuild_sql(
    from_table: Any, to_table: Any, index_ddl: dict[str, str] | None = None,
) -> str:
    """Render the rebuild sequence that turns ``from_table`` into ``to_table``."""
    table_name = to_table.name
    temp_name = f"{table_name}{REBUILD_SUFFIX}"

    # The new table is created under a temporary name, so render it with that
    # name and rename it into place once the rows are copied.
    staged = _renamed_copy(to_table, temp_name)

    copy_columns = _copyable_columns(from_table, to_table)
    copy_sql = ", ".join(quote_sq(c) for c in copy_columns)

    # No IF NOT EXISTS on the staging table: a leftover from a failed run must
    # fail the migration rather than silently receive the copied rows.
    parts = [build_sqlite_create_table_sql(staged, if_not_exists=False)]
    if copy_columns:
        parts.append(
            f"INSERT INTO {quote_sq(temp_name)} ({copy_sql}) "
            f"SELECT {copy_sql} FROM {quote_sq(table_name)};"
        )
    parts.append(f"DROP TABLE {quote_sq(table_name)};")
    parts.append(f"ALTER TABLE {quote_sq(temp_name)} RENAME TO {quote_sq(table_name)};")
    parts.extend(_index_statements(from_table, to_table, index_ddl))
    return "\n".join(parts)


def _renamed_copy(table: Any, name: str) -> Any:
    from dbwarden.engine.core.models import ModelTable

    return ModelTable(
        name=name,
        columns=table.columns,
        object_type=table.object_type,
        foreign_keys=table.foreign_keys,
        indexes=[],
        comment=table.comment,
        checks=table.checks,
        uniques=table.uniques,
        pg_table=table.pg_table,
        my_table=table.my_table,
        sq_table=table.sq_table,
        schema=table.schema,
    )


def build_sqlite_table_rebuild(
    op: dict[str, Any], db_name: str | None = None
) -> list[MigrationStatement]:
    """Emit the rebuild statement pair for a ``recreate_sq_table`` op."""
    from dbwarden.engine.offline import reconstruct_model_table

    from_table = reconstruct_model_table(op["from_table"])
    to_table = reconstruct_model_table(op["to_table"])
    index_ddl = op.get("index_ddl") or {}

    upgrade_sql = build_sqlite_rebuild_sql(from_table, to_table, index_ddl)
    rollback_sql = build_sqlite_rebuild_sql(to_table, from_table, index_ddl)

    dropped = {c.name for c in from_table.columns} - {c.name for c in to_table.columns}
    rollback_kind = "real"
    rollback_reason = op.get("reason")
    if dropped:
        rollback_kind = "conditional"
        rollback_reason = (
            f"rebuild of '{to_table.name}' drops column(s) "
            f"{', '.join(sorted(dropped))}; the rollback restores the columns but "
            "not their data"
        )

    return [
        MigrationStatement(
            order=StatementOrder.ALTER_TABLE_OPTIONS,
            upgrade_sql=upgrade_sql,
            rollback_sql=rollback_sql,
            rollback_kind=rollback_kind,
            rollback_reason=rollback_reason,
        )
    ]
