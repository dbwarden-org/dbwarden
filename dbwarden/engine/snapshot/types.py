"""Typed structures for schema snapshots.

This module defines TypedDicts that replace the untyped ``dict[str, Any]``
used throughout the snapshot pipeline. Every field that ``extract_full_schema_snapshot``
returns is typed here, including backend-specific metadata (PG, MySQL, SQLite).

Usage::

    from dbwarden.engine.snapshot.types import Snapshot

    snapshot: Snapshot = extract_full_schema_snapshot(database="primary")
"""
from __future__ import annotations

from typing import Any, Literal, TypedDict


# ---------------------------------------------------------------------------
# Column types
# ---------------------------------------------------------------------------

class SnapshotPgColumnMeta(TypedDict, total=False):
    """PostgreSQL-specific column metadata (stored under ``pg_column``)."""
    identity: str
    identity_start: int
    identity_increment: int
    identity_min: int
    identity_max: int
    collation: str
    generated: str
    storage: str
    compression: str
    statistics: int


class SnapshotMysqlColumnMeta(TypedDict, total=False):
    """MySQL/MariaDB-specific column metadata (stored under ``my_column``)."""
    my_charset: str
    my_collate: str
    my_unsigned: bool
    my_on_update: str


class SnapshotSqliteColumnMeta(TypedDict, total=False):
    """SQLite-specific column metadata (stored under ``sq_column``)."""
    sq_autoincrement: bool


class SnapshotPgType(TypedDict, total=False):
    """PostgreSQL type information (stored under ``pg_type``)."""
    kind: str
    type_name: str
    values: list[str]
    inner: str
    dimensions: int
    config: str
    range_type: str


class SnapshotColumn(TypedDict, total=False):
    """A single column in a snapshot table."""
    type: str
    nullable: bool
    primary_key: bool
    default: Any
    autoincrement: bool
    raw: bool
    length: int
    precision: int
    scale: int
    comment: str
    enum_name: str
    pg_type: SnapshotPgType
    pg_column: SnapshotPgColumnMeta
    my_column: SnapshotMysqlColumnMeta
    sq_column: SnapshotSqliteColumnMeta


# ---------------------------------------------------------------------------
# Table types
# ---------------------------------------------------------------------------

class SnapshotPolicy(TypedDict):
    """A PostgreSQL RLS policy."""
    name: str
    permissive: str
    command: str
    role: str | list[str]
    using: str | None
    with_check: str | None


class SnapshotGrant(TypedDict):
    """A PostgreSQL table-level grant."""
    role: str
    privileges: list[str]
    grantable: bool


class SnapshotTrigger(TypedDict):
    """A PostgreSQL trigger."""
    name: str
    definition: str


class SnapshotPgTableMeta(TypedDict, total=False):
    """PostgreSQL-specific table metadata (stored under ``pg_table``)."""
    backend: Literal["postgresql"]
    pg_rls: bool
    pg_rls_force: bool
    pg_unlogged: bool
    pg_tablespace: str
    pg_inherits: str | list[str]
    pg_partition_of: str
    pg_partition_bound: str
    pg_partition: SnapshotPartition
    pg_excludes: list[dict[str, Any]]
    pg_partitions: list[dict[str, Any]]
    pg_storage_params: dict[str, Any]
    pg_policies: list[SnapshotPolicy]
    pg_grants: list[SnapshotGrant]
    pg_triggers: list[SnapshotTrigger]


class SnapshotPartition(TypedDict):
    """PostgreSQL partition info."""
    strategy: str
    columns: list[str]


class SnapshotMysqlTableMeta(TypedDict, total=False):
    """MySQL/MariaDB-specific table metadata (stored under ``my_table``)."""
    my_engine: str
    my_collate: str
    my_charset: str
    my_auto_increment: int
    my_row_format: str


class SnapshotSqliteTableMeta(TypedDict, total=False):
    """SQLite-specific table metadata (stored under ``sq_table``)."""
    pass  # Defined dynamically by parse_sqlite_table_options


class SnapshotTable(TypedDict, total=False):
    """A table (or view/materialized view) in the snapshot."""
    columns: dict[str, SnapshotColumn]
    primary_key: list[str]
    comment: str | None
    schema: str | None
    object_type: str
    pg_view_definition: str
    pg_view_materialized: bool
    pg_table: SnapshotPgTableMeta
    my_table: SnapshotMysqlTableMeta
    sq_table: SnapshotSqliteTableMeta
    sq_index_ddl: str


# ---------------------------------------------------------------------------
# Index types
# ---------------------------------------------------------------------------

class SnapshotIndex(TypedDict, total=False):
    """An index entry in the snapshot."""
    table: str
    name: str
    columns: list[str]
    unique: bool
    expression: str
    using: str
    where: str
    include: list[str]
    with_params: list[str]
    tablespace: str
    nulls_not_distinct: bool
    column_sorting: dict[str, str]
    postgresql_ops: dict[str, str]
    comment: str


# ---------------------------------------------------------------------------
# Constraint types
# ---------------------------------------------------------------------------

class SnapshotForeignKey(TypedDict):
    """A foreign key constraint."""
    type: Literal["foreign_key"]
    name: str
    table: str
    columns: list[str]
    referenced_table: str
    referenced_columns: list[str]
    on_delete: str
    on_update: str
    deferrable: bool
    match: str


class SnapshotUniqueConstraint(TypedDict):
    """A unique constraint."""
    type: Literal["unique"]
    name: str
    table: str
    columns: list[str]


class SnapshotCheckConstraint(TypedDict):
    """A check constraint."""
    type: Literal["check"]
    name: str
    table: str
    columns: list[str]
    expression: str


SnapshotConstraint = SnapshotForeignKey | SnapshotUniqueConstraint | SnapshotCheckConstraint


# ---------------------------------------------------------------------------
# PostgreSQL-only object types
# ---------------------------------------------------------------------------

class SnapshotDomain(TypedDict, total=False):
    """A PostgreSQL domain."""
    domain_type: str
    not_null: bool
    default: str
    schema: str
    check: str


class SnapshotSequence(TypedDict, total=False):
    """A PostgreSQL sequence."""
    increment: int
    minvalue: int
    maxvalue: int
    start: int
    cycle: bool
    owned_by: str


class SnapshotCompositeType(TypedDict, total=False):
    """A PostgreSQL composite type."""
    columns: list[dict[str, str]]
    schema: str


class SnapshotFunction(TypedDict, total=False):
    """A PostgreSQL function or procedure."""
    definition: str
    schema: str


class SnapshotRole(TypedDict, total=False):
    """A PostgreSQL role."""
    superuser: bool
    inherit: bool
    createrole: bool
    createdb: bool
    login: bool
    connlimit: int
    valid_until: str


class SnapshotDefaultPrivilege(TypedDict):
    """A PostgreSQL default privilege entry."""
    schema: str
    role: str
    object_type: str
    privileges: list[str]


class SnapshotSchemaGrant(TypedDict):
    """A PostgreSQL schema-level grant."""
    role: str
    privileges: list[str]
    grantable: bool


class SnapshotEventTrigger(TypedDict, total=False):
    """A PostgreSQL event trigger."""
    event: str
    function: dict[str, str]
    tags: list[str]
    enabled: str


class SnapshotExtendedStats(TypedDict, total=False):
    """PostgreSQL extended statistics."""
    table: str
    kinds: list[str]
    schema: str
    columns: list[int]
    expressions: list[str]


# ---------------------------------------------------------------------------
# Top-level snapshot
# ---------------------------------------------------------------------------

class Snapshot(TypedDict):
    """Complete schema snapshot returned by ``extract_full_schema_snapshot``."""
    format_version: int
    migration_id: str
    database_name: str
    database_type: str
    applied_at: str
    tables: dict[str, SnapshotTable]
    enums: dict[str, list[str]]
    domains: dict[str, SnapshotDomain]
    indexes: dict[str, SnapshotIndex]
    constraints: dict[str, SnapshotConstraint]
    sequences: dict[str, SnapshotSequence]
    composite_types: dict[str, SnapshotCompositeType]
    functions: dict[str, SnapshotFunction]
    roles: dict[str, SnapshotRole]
    default_privileges: dict[str, SnapshotDefaultPrivilege]
    schema_grants: dict[str, list[SnapshotSchemaGrant]]
    event_triggers: dict[str, SnapshotEventTrigger]
    extended_stats: dict[str, SnapshotExtendedStats]
