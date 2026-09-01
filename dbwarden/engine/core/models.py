from __future__ import annotations

from dataclasses import dataclass
from typing import Any, List, Optional

from dbwarden.databases.pgsql.constraint import normalize_exclude_spec
from dbwarden.schema.constraint import normalize_check_spec, normalize_unique_spec


class ModelColumn:
    """Represents a column from a SQLAlchemy model.

    Attributes:
        name: Column name.
        type: Normalized SQL type string.
        nullable: Whether the column allows NULL.
        primary_key: Whether this column is part of the primary key.
        unique: Whether this column has a UNIQUE constraint.
        default: The default value expression, or None.
        foreign_key: The foreign key reference (e.g. ``"other_table.id"``), or None.
        codec: ClickHouse codec, or None.
        comment: Column comment, or None.
        pg_meta: PostgreSQL-specific metadata dict.
        ch_meta: ClickHouse-specific metadata dict.
        my_meta: MySQL/MariaDB-specific metadata dict.
        sq_meta: SQLite-specific metadata dict.
        autoincrement: Whether the column auto-increments, or None.
        fk_on_delete: ON DELETE action for foreign keys, or None.
        fk_on_update: ON UPDATE action for foreign keys, or None.
    """

    def __init__(
        self,
        name: str,
        type: str,
        nullable: bool,
        primary_key: bool,
        unique: bool,
        default: Optional[str],
        foreign_key: Optional[str],
        codec: Optional[str] = None,
        comment: Optional[str] = None,
        pg_meta: Optional[dict[str, Any]] = None,
        ch_meta: Optional[dict[str, Any]] = None,
        my_meta: Optional[dict[str, Any]] = None,
        sq_meta: Optional[dict[str, Any]] = None,
        autoincrement: Optional[bool] = None,
        fk_on_delete: Optional[str] = None,
        fk_on_update: Optional[str] = None,
    ):
        self.name = name
        self.type = type
        self.nullable = nullable
        self.primary_key = primary_key
        self.unique = unique
        self.default = default
        self.foreign_key = foreign_key
        self.codec = codec
        self.comment = comment
        self.pg_meta = pg_meta or {}
        self.ch_meta = ch_meta or {}
        self.my_meta = my_meta or {}
        self.sq_meta = sq_meta or {}
        self.autoincrement = autoincrement
        self.fk_on_delete = fk_on_delete
        self.fk_on_update = fk_on_update

    def to_dict(self) -> dict:
        d: dict[str, Any] = {
            "name": self.name,
            "type": self.type,
            "nullable": self.nullable,
            "primary_key": self.primary_key,
            "unique": self.unique,
            "default": self.default,
            "foreign_key": self.foreign_key,
            "codec": self.codec,
            "comment": self.comment,
            "pg_meta": self.pg_meta,
            "autoincrement": self.autoincrement,
        }
        if self.ch_meta:
            d["ch_meta"] = self.ch_meta
        if self.my_meta:
            d["my_meta"] = self.my_meta
        if self.sq_meta:
            d["sq_meta"] = self.sq_meta
        if self.fk_on_delete is not None:
            d["fk_on_delete"] = self.fk_on_delete
        if self.fk_on_update is not None:
            d["fk_on_update"] = self.fk_on_update
        return d


def column_unique_is_table_constraint(table: Any, column_name: str) -> bool:
    """True when this column's UNIQUE is already declared as a table constraint.

    ``mapped_column(unique=True)`` is collected into ``table.uniques`` so the
    constraint gets a deterministic name.  The column definition must then not
    repeat ``UNIQUE`` inline, or the table ends up with two unique constraints
    on the same column - the anonymous one the database names itself, plus the
    named one.
    """
    for unique in getattr(table, "uniques", None) or []:
        columns = unique.get("columns") or []
        if len(columns) == 1 and columns[0] == column_name:
            return True
    return False


def column_foreign_key_is_table_constraint(table: Any, column_name: str) -> bool:
    """True when this column's foreign key is also declared at table level.

    A single-column ``ForeignKey`` reaches the model twice: once on the column
    and once in ``table.foreign_keys``.  Backends that emit foreign keys as
    named ``ADD CONSTRAINT`` statements must not also render ``REFERENCES``
    inline, or the table ends up with the constraint twice.
    """
    for foreign_key in getattr(table, "foreign_keys", None) or []:
        columns = foreign_key.get("columns") or []
        if len(columns) == 1 and columns[0] == column_name:
            return True
    return False


@dataclass
class IndexInfo:
    """Describes an index for migration generation.

    Attributes:
        columns: Column names or expressions in the index.
        name: Explicit index name, or None for auto-generated.
        unique: Whether this is a UNIQUE index.
        using: Index method (btree, hash, gist, gin, etc.).
        where: Partial index WHERE clause, or None.
        include: INCLUDE columns for covering indexes, or None.
        with_params: Storage parameters (e.g. fillfactor), or None.
        tablespace: Tablespace name, or None.
        nulls_not_distinct: PostgreSQL NULLS NOT DISTINCT, or False.
        column_sorting: Per-column sort options (ASC/DESC, NULLS FIRST/LAST).
        postgresql_ops: Per-column operator class names.
        comment: Index comment, or None.
        concurrently: Whether to build concurrently (default True).
        clickhouse_type: ClickHouse index type, or None.
        clickhouse_granularity: ClickHouse index granularity, or None.
        expression: Index expression for expression-based indexes.
    """
    columns: list[str]
    name: str | None = None
    unique: bool = False
    using: str | None = None
    where: str | None = None
    include: list[str] | None = None
    with_params: dict[str, Any] | None = None
    tablespace: str | None = None
    nulls_not_distinct: bool = False
    column_sorting: dict[str, str] | None = None
    postgresql_ops: dict[str, str] | None = None
    comment: str | None = None
    concurrently: bool = True
    clickhouse_type: str | None = None
    clickhouse_granularity: int | None = None
    expression: str | None = None

    def to_dict(self) -> dict:
        d: dict[str, Any] = {
            "columns": self.columns,
            "unique": self.unique,
        }
        if self.name is not None:
            d["name"] = self.name
        if self.using is not None:
            d["using"] = self.using
        if self.where is not None:
            d["where"] = self.where
        if self.include is not None:
            d["include"] = self.include
        if self.with_params is not None:
            d["with_params"] = self.with_params
        if self.tablespace is not None:
            d["tablespace"] = self.tablespace
        if self.nulls_not_distinct:
            d["nulls_not_distinct"] = True
        if self.column_sorting is not None:
            d["column_sorting"] = self.column_sorting
        if self.postgresql_ops is not None:
            d["postgresql_ops"] = self.postgresql_ops
        if self.comment is not None:
            d["comment"] = self.comment
        if not self.concurrently:
            d["concurrently"] = False
        if self.clickhouse_type is not None:
            d["clickhouse_type"] = self.clickhouse_type
        if self.clickhouse_granularity is not None:
            d["clickhouse_granularity"] = self.clickhouse_granularity
        if self.expression is not None:
            d["expression"] = self.expression
        return d

    @staticmethod
    def from_dict(d: dict) -> "IndexInfo":
        return IndexInfo(
            name=d.get("name"),
            columns=list(d.get("columns", [])),
            unique=bool(d.get("unique", False)),
            using=d.get("using"),
            where=d.get("where"),
            include=list(d.get("include", [])) if d.get("include") else None,
            with_params=dict(d.get("with_params", {})) if d.get("with_params") else None,
            tablespace=d.get("tablespace"),
            nulls_not_distinct=bool(d.get("nulls_not_distinct", False)),
            column_sorting=dict(d.get("column_sorting", {})) if d.get("column_sorting") else None,
            postgresql_ops=dict(d.get("postgresql_ops", {})) if d.get("postgresql_ops") else None,
            comment=d.get("comment"),
            concurrently=bool(d.get("concurrently", True)),
            clickhouse_type=d.get("clickhouse_type"),
            clickhouse_granularity=d.get("clickhouse_granularity"),
            expression=d.get("expression"),
        )


class ModelTable:
    """Represents a table (or view) from a SQLAlchemy model.

    Attributes:
        name: Table name.
        columns: List of ``ModelColumn`` instances.
        clickhouse_options: ClickHouse engine/codec options dict.
        object_type: ``"table"``, ``"view"``, or ``"materialized_view"``.
        foreign_keys: List of foreign key dicts.
        indexes: List of ``IndexInfo`` or index dicts.
        comment: Table comment, or None.
        checks: List of CHECK constraint specs.
        uniques: List of UNIQUE constraint specs.
        excludes: List of EXCLUDE constraint specs (PostgreSQL).
        pg_table: PostgreSQL-specific table metadata dict.
        my_table: MySQL/MariaDB-specific table metadata dict.
        sq_table: SQLite-specific table metadata dict.
        schema: Database schema name, or None for the default.
        pg_view_definition: PostgreSQL view SQL, or None.
        pg_view_materialized: Whether this is a materialized view.
        pg_view_auto_refresh: Whether to auto-refresh the materialized view.
        pg_policies: List of RLS policy dicts.
        pg_grants: List of grant dicts.
    """

    def __init__(
        self,
        name: str,
        columns: List[ModelColumn],
        clickhouse_options: Optional[dict] = None,
        object_type: str = "table",
        foreign_keys: Optional[list[dict]] = None,
        indexes: Optional[list[dict | IndexInfo]] = None,
        comment: str | None = None,
        checks: Optional[list[dict[str, Any]]] = None,
        uniques: Optional[list[dict[str, Any]]] = None,
        excludes: Optional[list[dict[str, Any]]] = None,
        pg_table: Optional[dict[str, Any]] = None,
        my_table: Optional[dict[str, Any]] = None,
        sq_table: Optional[dict[str, Any]] = None,
        schema: str | None = None,
        pg_view_definition: str | None = None,
        pg_view_materialized: bool = False,
        pg_view_auto_refresh: bool = False,
        pg_policies: Optional[list[dict[str, Any]]] = None,
        pg_grants: Optional[list[dict[str, Any]]] = None,
    ):
        self.name = name
        self.columns = columns
        self.clickhouse_options = clickhouse_options or {}
        self.object_type = object_type
        self.foreign_keys = foreign_keys or []
        self.indexes: list[IndexInfo] = [
            IndexInfo.from_dict(idx) if isinstance(idx, dict) else idx
            for idx in (indexes or [])
        ]
        self.comment = comment
        self.checks = [normalize_check_spec(c) for c in (checks or [])]
        self.uniques = [normalize_unique_spec(u) for u in (uniques or [])]
        self.excludes = [normalize_exclude_spec(ex) for ex in (excludes or [])]
        self.pg_table = pg_table or {}
        self.my_table = my_table or {}
        self.sq_table = sq_table or {}
        self.schema = schema
        self.pg_view_definition = pg_view_definition
        self.pg_view_materialized = pg_view_materialized
        self.pg_view_auto_refresh = pg_view_auto_refresh
        self.pg_policies = pg_policies or []
        self.pg_grants = pg_grants or []

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "columns": [col.to_dict() for col in self.columns],
            "clickhouse_options": self.clickhouse_options,
            "object_type": self.object_type,
            "foreign_keys": self.foreign_keys,
            "indexes": [idx.to_dict() if isinstance(idx, IndexInfo) else idx for idx in self.indexes],
            "comment": self.comment,
            "checks": self.checks,
            "uniques": self.uniques,
            "excludes": self.excludes,
            "pg_table": self.pg_table,
            "my_table": self.my_table,
            "sq_table": self.sq_table,
            "schema": self.schema,
            "pg_view_definition": self.pg_view_definition,
            "pg_view_materialized": self.pg_view_materialized,
            "pg_view_auto_refresh": self.pg_view_auto_refresh,
            "pg_policies": self.pg_policies,
            "pg_grants": self.pg_grants,
        }
