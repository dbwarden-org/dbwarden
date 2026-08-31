# SQL Databases

dbwarden supports PostgreSQL, MySQL, MariaDB, and SQLite. While all four share standard SQL DDL, each backend has distinct behaviors that affect generated migrations. This page documents backend-specific syntax, limitations, and edge cases.

## DDL Transactional Behavior

| Backend | Transactional DDL | Impact |
|---------|------------------|--------|
| PostgreSQL | Yes | Entire migration file succeeds or rolls back atomically |
| MySQL / MariaDB | No | Each DDL auto-commits; partial failure leaves schema inconsistent |
| SQLite | Mostly | Per-statement auto-commit outside explicit transactions |

**PostgreSQL**: DDL is transactional. If a migration file contains multiple statements and one fails, all prior DDL in that file is rolled back. This makes PostgreSQL the safest backend for automated migration runs.

PostgreSQL is also a **first-class backend** with full support for identity columns, collation, storage, compression, generated columns, table fillfactor, tablespace, inheritance, EXCLUDE constraints, deferrable FK options, and advanced index parameters. See [PostgreSQL Deep Dive](postgresql/index.md) for complete details.

**MySQL / MariaDB**: DDL statements auto-commit immediately. If a 5-statement migration fails on the 4th statement, the first 3 are already committed and cannot be rolled back. Manual inspection and recovery may be needed. Always test MySQL/MariaDB migrations in a staging environment first.

**SQLite**: DDL is transactional only within explicit `BEGIN/COMMIT` blocks. dbwarden migration files are executed with each statement as a separate implicit transaction. This means SQLite is similar to MySQL in practice; partial failure is possible.

## Column Rename

| Backend | Syntax | Supported |
|---------|--------|-----------|
| PostgreSQL | `ALTER TABLE t RENAME COLUMN old TO new` | Yes |
| SQLite | `ALTER TABLE t RENAME COLUMN old TO new` | Yes (3.25+) |
| MySQL | `ALTER TABLE t CHANGE old new type` | Workaround needed |
| MariaDB | `ALTER TABLE t CHANGE old new type` | Workaround needed |

PostgreSQL and SQLite (3.25+) support `RENAME COLUMN` natively. dbwarden emits the standard form for all backends. If you need a backend that does not support native column rename (e.g., MySQL < 8.0, MariaDB), you must write a manual migration or verify the generated SQL.

## Column Type Change

| Backend | Syntax | Supported |
|---------|--------|-----------|
| PostgreSQL | `ALTER TABLE t ALTER COLUMN c TYPE newtype` (commented-out `USING` by default, active with `--postgres-auto-using`) | Yes |
| MySQL / MariaDB | `ALTER TABLE t MODIFY COLUMN c newtype` | Yes |
| SQLite | Table rebuild | Yes |

**PostgreSQL**: Emits `ALTER TABLE t ALTER COLUMN c TYPE newtype` with a commented-out `-- USING col::newtype` line. Pass `--postgres-auto-using` on `make-migrations` to emit an active `USING` clause.

**MySQL / MariaDB**: Emits `ALTER TABLE t MODIFY COLUMN c newtype`. Note that `MODIFY COLUMN` requires specifying the entire column definition, not just the type. dbwarden includes only the type in the `MODIFY` statement; if you need additional attributes (e.g., `NOT NULL`, `DEFAULT`), add them manually.

**SQLite**: `ALTER COLUMN TYPE` does not exist, so dbwarden rebuilds the table: create the new shape under a temporary name, copy the rows, drop the original, rename into place, recreate the indexes. The rollback is the rebuild in the other direction. See [SQLite Deep Dive](sqlite.md#table-rebuilds).

## Column Nullable Change

| Backend | Syntax | Supported |
|---------|--------|-----------|
| PostgreSQL | `ALTER TABLE t ALTER COLUMN c [SET/DROP] NOT NULL` | Yes |
| MySQL / MariaDB | `ALTER TABLE t MODIFY COLUMN c coltype [NOT] NULL` | Yes |
| SQLite | Table rebuild | Yes |

**PostgreSQL**: Uses `SET NOT NULL` / `DROP NOT NULL`. No column type needed.

**MySQL / MariaDB**: Uses `MODIFY COLUMN` which requires the full column type. dbwarden includes the type from the model column definition. If the type is not available, nullable changes for MySQL/MariaDB may produce incomplete SQL.

**SQLite**: Nullability is part of the column definition and can only change by rebuilding the table, which dbwarden generates. See [SQLite Deep Dive](sqlite.md#table-rebuilds).

## Column Default Change

PostgreSQL, MySQL and MariaDB support `ALTER TABLE t ALTER COLUMN c SET DEFAULT value` and `ALTER TABLE t ALTER COLUMN c DROP DEFAULT`. SQLite has no `ALTER COLUMN`, so a default change is emitted as a table rebuild.

## Foreign Key Handling

| Backend | ADD FK | DROP FK | Notes |
|---------|--------|---------|-------|
| PostgreSQL | `ADD CONSTRAINT ... FOREIGN KEY` | `DROP CONSTRAINT ...` | Supports `ON DELETE`, `ON UPDATE`, `DEFERRABLE` |
| MySQL | `ADD CONSTRAINT ... FOREIGN KEY` | `DROP FOREIGN KEY ...` | Uses constraint name, not FK name |
| MariaDB | `ADD CONSTRAINT ... FOREIGN KEY` | `DROP FOREIGN KEY ...` | Same as MySQL |
| SQLite | Table rebuild | Table rebuild | Constraint is inlined in the rebuilt CREATE TABLE |
| ClickHouse | Not supported (error) | Not supported (error) | `ForeignKey()` raises `DBWardenConfigError` |

**PostgreSQL FK options**: `ON DELETE`, `ON UPDATE`, and `DEFERRABLE INITIALLY DEFERRED` are fully supported. See [PostgreSQL Deep Dive](postgresql/index.md).

**Validation**: Before emitting `ADD FOREIGN KEY`, dbwarden verifies that the referenced table and columns exist in the snapshot. If they don't, the FK is silently skipped to avoid generating broken SQL. This can happen when the referenced table is added in the same migration batch. If an FK is unexpectedly missing from generated SQL, check whether the referenced table exists in the snapshot.

**Content-based comparison**: FKs are compared by a 6-tuple signature `(columns, ref_table, ref_columns, on_delete, on_update, deferrable)`, not by constraint name. Renaming an FK constraint or changing options produces a drop+add.

## Index Handling

| Backend | CREATE INDEX | DROP INDEX | Notes |
|---------|-------------|------------|-------|
| PostgreSQL | `CREATE [UNIQUE] INDEX [CONCURRENTLY] ... USING <method> INCLUDE (<cols>) WITH (<params>) WHERE <pred> TABLESPACE <ts>` | `DROP INDEX` | Full feature support |
| MySQL / MariaDB | `CREATE [UNIQUE] INDEX` | `DROP INDEX` | Standard |
| SQLite | `CREATE [UNIQUE] INDEX` | `DROP INDEX` | Standard |

**PostgreSQL advanced parameters**: all are supported in `_build_index_sql`. See [PostgreSQL Deep Dive](postgresql/index.md) for full coverage.

**PostgreSQL `CONCURRENTLY`**: dbwarden defaults to `CREATE INDEX CONCURRENTLY` to avoid table locking. Pass `--no-concurrent` when the migration must run inside a transaction block (PostgreSQL requires `CONCURRENTLY` outside a transaction).

**Full-content comparison**: Indexes are compared by **all** attributes (using, unique, where, include, with_params, tablespace, nulls_not_distinct, column_sorting, concurrently), not just columns + name. Any difference produces a drop+add; ALTER INDEX is not used.

**Auto-generated names**: `idx_{table}_{col1}_{col2}` (non-unique), `uq_{table}_{col1}_{col2}` (unique). Non-btree `USING` methods append a suffix: `idx_{table}_{col}_{method}`.

**No ALTER INDEX**: All index parameter changes (adding a WHERE clause, switching USING methods, changing sort order) produce `DROP INDEX` + `CREATE INDEX`. This is intentional; `ALTER INDEX` support varies widely across backends and index attribute types.

## Safe Type Change

| Backend | Supported | Behavior |
|---------|-----------|----------|
| PostgreSQL | Yes | Multi-step: add temp column, backfill comment, verify, drop+rename |
| MySQL / MariaDB | Yes | Multi-step: add temp column, backfill comment, verify, drop+rename |
| SQLite | Not needed | The rebuild already copies the table atomically |

The `--safe-type-change` flag generates a multi-step strategy:
1. Add a temporary column with the new type
2. Emit a `--` comment with an `UPDATE` statement template
3. Emit a verification comment
4. After manual verification, drop the old column and rename the temporary column

On SQLite the flag has no effect: a type change is already emitted as a table rebuild, which copies every row into the new column definition in one statement sequence rather than in manually verified steps.

## Table Rename

All four SQL backends support `ALTER TABLE t RENAME TO newname`. The syntax is uniform. ClickHouse is the only supported backend that does not support table rename (see [ClickHouse](clickhouse/index.md)).

## DROP COLUMN Warning

All DROP COLUMN statements are prefixed with a warning comment:

```sql
-- WARNING: DROPPING COLUMN users.legacy_field

ALTER TABLE users DROP COLUMN legacy_field
```

This applies to all SQL backends. The warning is a comment only and does not affect execution. In MySQL/MariaDB, be especially careful with DROP COLUMN since the DDL auto-commits and cannot be rolled back.

## DROP TABLE

`DROP TABLE` emits a rollback comment that references restoring from snapshot. The actual rollback SQL is a placeholder and must be written manually if needed.

## Statement Ordering

```
RENAME TABLE         (0)
RENAME COLUMN        (1)
ALTER COLUMN TYPE    (2)
ALTER COLUMN NULLABLE (3)
ALTER COLUMN DEFAULT (4)
CREATE TABLE         (5)
ADD COLUMN           (6)
ALTER FOREIGN KEY    (7)
ALTER INDEX          (8)
DROP COLUMN          (9)
DROP TABLE           (10)
ALTER TABLE COMMENT  (11)
ALTER COLUMN COMMENT (12)
ALTER TABLE OPTIONS  (13)
ALTER TABLE CONSTRAINT (14)
```

This ordering ensures safe execution across all SQL backends. Table renames come first so all subsequent ops use the new name. Drops come last to minimize risk of referencing dropped objects.

## Migration Name Generation

Auto-generated migration names are truncated to 72 characters. Operation words (`add`, `drop`, `alter`, `create`, `rename`, `add_columns`, etc.) are preserved during truncation; table and column names are shortened as needed. This applies uniformly across all backends.

## Resolved From Values

The plan JSON `resolved_from` field indicates how a rename was confirmed:

| Value | Meaning |
|-------|---------|
| `"rename_flag"` | Explicitly declared via `--rename` or `--rename-table` CLI flag |
| `"prompt"` | Confirmed interactively by the user |
| (absent) | Auto-detected and kept without explicit confirmation |

## Known Backend-Specific Limitations

### PostgreSQL
- `USING` clause for type casts is not auto-generated (write a manual migration)
- `CREATE INDEX CONCURRENTLY` may not work within multi-statement transactions
- `ALTER COLUMN ADD GENERATED ... AS (expr) STORED` is not supported by PostgreSQL; dbwarden emits a comment placeholder

### MySQL / MariaDB
- DDL is not transactional; partial failure leaves schema inconsistent
- `MODIFY COLUMN` requires full column definition; auto-generated nullable change SQL includes the column type but may omit other attributes
- Column rename is not natively supported (requires `CHANGE` syntax with type)
- Foreign key drop uses `DROP FOREIGN KEY` (constraint name is still the auto-generated name)

### SQLite
- Column type, nullability, default and constraint changes are emitted as a table rebuild; the migration copies every row and holds a write lock for its duration
- `DROP COLUMN` in a rebuild discards the column's data; the rollback restores the column, not the values
- Column rename supported since 3.25.0; `DROP COLUMN` since 3.35.0
- Declared types are preserved as written; inside a `STRICT` table they collapse to `INTEGER` / `REAL` / `TEXT` / `BLOB`
- Foreign key enforcement is off by default, which the rebuild relies on; do not enable `PRAGMA foreign_keys` on the migration connection
- Limited to single-writer; no concurrent writes
