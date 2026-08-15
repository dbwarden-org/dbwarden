# `make-migrations`

Generate SQL migration file(s) from SQLAlchemy models.

## Usage

```bash
# Auto-generated name from schema changes
$ dbwarden make-migrations

# User-provided description
$ dbwarden make-migrations "create users table"

# With database option
$ dbwarden make-migrations --database primary --verbose

# Output plan JSON only
$ dbwarden make-migrations --database primary --plan

# Explicitly declare column renames
$ dbwarden make-migrations --rename users.username:email --rename posts.title:headline

# Explicitly declare table renames
$ dbwarden make-migrations --rename-table users:accounts

# Safe multi-step type changes
$ dbwarden make-migrations --database primary --safe-type-change
```

## Options

- `description` (optional): Custom migration name. If not provided, automatically generated from schema changes.
- `--database`, `-d`: Target database.
- `--plan`: Print the migration plan JSON without writing files.
- `--verbose`, `-v`: Verbose output.
- `--rename`: Repeatable. Declare a column rename in the format `table.old_name:new_name`. See [Rename Detection](#rename-detection) below.
- `--rename-table`: Repeatable. Declare a table rename in the format `old_table:new_table`. See [Table Rename Detection](#table-rename-detection) below.
- `--safe-type-change`: Use a multi-step strategy for type changes: add a temporary column, data migration comment, verification step, then drop-and-rename. Useful for databases where `ALTER COLUMN TYPE` would lock the table.
- `--concurrent` / `--no-concurrent`: Enable or disable `CREATE INDEX CONCURRENTLY` for PostgreSQL (default: `--concurrent`). Use `--no-concurrent` when the migration runs inside a transaction block.
- `--offline`: Use a model state file (`.dbwarden/model_state.json`) instead of a live database or schema snapshot. Run `dbwarden export-models` first to establish a baseline. Useful for CI pipelines without a database service.
- `--clickhouse-engine-recreate`: Allow automatic ClickHouse table rebuild when engine changes require recreation. Required to generate `recreate_ch_table` operations. See [ClickHouse Engine Recreate](#clickhouse-engine-recreate) below.
- `--drop-preserved-clickhouse-table` / `--keep-preserved-clickhouse-table`: Control whether the preserved old ClickHouse table is dropped after the engine-recreate swap. If omitted, interactive terminals are prompted; non-TTY preserves by default.
- `--postgres-auto-using`: Emit an active `USING col::newtype` clause on PostgreSQL `ALTER COLUMN TYPE` statements. Default is a commented-out line for manual review. See the PostgreSQL docs section on Column Type Changes for details.
- `--type`, `-t`: Output prefix for the generated migration file: `versioned` (default), `ra` / `runs_always`, or `roc` / `runs_on_change`. Use `ra` for SQL that should run every migration cycle (e.g. grants, materialized view refreshes) and `roc` for SQL that should re-run when the file changes (e.g. stored procedures, triggers).

## Schema Snapshots

After each migration is applied, DBWarden writes a **schema snapshot** to `.dbwarden/schemas/<migration_id>.schema.json`. These snapshots capture the full DDL state (tables, columns, types, indexes, constraints, enums) at that point in time.

For long migration histories, `dbwarden migrate --defer-snapshots` writes only
the final schema snapshot for the batch. This avoids repeated full-schema
reflection while preserving the final state needed by convergence and offline
diff workflows. Omit the flag when per-migration audit snapshots are required.

`make-migrations` diffs your SQLAlchemy models against the **latest** snapshot instead of the live database. This means:

- You don't need a running database to generate migrations (after the first `migrate`).
- Rename detection works by comparing dropped and added columns between the snapshot and your models.
- If no snapshot exists, `make-migrations` falls back to diffing against the live database.

See [Schema Snapshots](schema-snapshots.md) for details.

## Rollback contract

`make-migrations` enforces rollback correctness for generated migrations. Each emitted rollback statement is classified before the file is accepted:

| Kind | Behavior |
|------|----------|
| `real` | Generated normally. |
| `conditional` | Generated only when prior state exists and the operation is statically proven safe. Verbose mode prints a warning. |
| `irreversible` | Generated only for known irreversible operations or explicit acknowledgement. Normal mode prints a warning. |
| `placeholder` | Refused by default. |

A rollback SQL comment is not treated as a successful rollback. If rollback cannot be generated safely, DBWarden fails generation instead of writing a migration that appears reversible.

Explicit irreversible declaration for committed migrations:

```sql
-- dbwarden: irreversible
```

See [Rollback Coverage](../correctness/rollback-coverage-matrix.md) for PostgreSQL and ClickHouse operation coverage.

## Rename Detection

When a column is dropped from the snapshot and a new column of the same type is added to the model, DBWarden auto-detects it as a potential **rename** and emits `ALTER TABLE ... RENAME COLUMN` instead of `DROP` + `ADD`.

### Auto-detection rules

| Condition | Outcome |
|-----------|---------|
| Same table, 1 dropped + 1 added, same normalized type | Auto-detected as rename |
| Different types | Never auto-detected (emits drop + add) |
| Same name kept | Skipped (no op) |
| 2+ dropped + 2+ added of same type | Paired sequentially (positional) |

### Rename detection edge cases

- **Ambiguous multi-rename**: When 2+ dropped columns and 2+ added columns share the same normalized type, all are treated as renames (paired in insertion order). This is intentionally permissive; false positives can be declined interactively or overridden with `--rename`.
- **Drop-add conversion with `resolved_from`**: A confirmed drop+add pair is converted to a `rename_column` op with `resolved_from` tracking the confirmation source (`"rename_flag"` or `"prompt"`).
- **Non-matching confirmed set**: If a confirmed rename tuple does not match any op (e.g., table name mismatch), it is silently ignored.
- **Table + column rename interaction**: Table renames are processed first (statement order 0). After the snapshot is updated with the new table name, column renames are detected against the renamed table. The column rename's `resolved_from` is independent of the table rename's `resolved_from`.

### Interactive prompt (TTY)

When auto-detected renames are found and you're in an interactive terminal, `make-migrations` prompts you to confirm each one:

**Single rename:**
```
Detected rename: users.username → email. Confirm rename? [Y/n]:
```

**Multiple renames:**
```
Detected column renames:
  [1] users.username → email
  [2] posts.title → headline
  [s] Skip all
  [a] Accept all
Select renames to confirm (e.g. 1,3 or a or s):
```

### CI / non-TTY behavior

When not in an interactive terminal, auto-detected renames are **not applied**. Instead, a warning is printed suggesting the `--rename` flag:

```
The following auto-detected column renames were not confirmed:
  users.username → email (use --rename users.username:email to confirm)
  posts.title → headline (use --rename posts.title:headline to confirm)
These will be emitted as DROP + ADD instead of RENAME.
```

To apply them in CI, pass the corresponding `--rename` flags.

### `--rename` flag

The `--rename` flag explicitly tells `make-migrations` to treat a drop+add pair as a rename. It is required in non-TTY environments (CI) and can also be used to force renames that auto-detection would miss (e.g., type changes).

Format: `--rename <table>.<old_name>:<new_name>`

Examples:

```bash
# Single rename
$ dbwarden make-migrations --rename users.username:email

# Multiple renames
$ dbwarden make-migrations --rename users.username:email --rename posts.title:headline

# Force rename even when types differ
$ dbwarden make-migrations --rename users.phone:mobile_phone
```

### Resolution order

1. **`--rename` flags** are always applied as `RENAME COLUMN` with `resolved_from: "rename_flag"`.
2. **Auto-detected renames** confirmed via interactive prompt get `resolved_from: "prompt"`.
3. **Auto-detected renames** not in `--rename` flags and not confirmed (or in CI) are emitted as `DROP` + `ADD` instead.

## Column-Level Diff

When a schema snapshot exists, `make-migrations` does more than detect new and dropped columns. It also compares columns that exist in both the snapshot and the model for three kinds of change:

| Change | Snapshot vs Model | Generated SQL |
|--------|-------------------|---------------|
| **Type change** | `snapshot.type != model.type` (normalized) | `ALTER COLUMN ... TYPE ...` |
| **Nullability change** | `snapshot.nullable != model.nullable` | `ALTER COLUMN ... SET NOT NULL` / `DROP NOT NULL` |
| **Default change** | `snapshot.default != model.default` | `ALTER COLUMN ... SET DEFAULT` / `DROP DEFAULT` |

### Type change detection

Types are normalized before comparison (see [Schema Snapshots](schema-snapshots.md)). If the normalized type differs between the snapshot and the model, an `alter_column_type` operation is emitted.

Example: model changes `VARCHAR` to `TEXT`:

```sql
-- upgrade

ALTER TABLE users ALTER COLUMN bio TYPE TEXT

-- rollback

ALTER TABLE users ALTER COLUMN bio TYPE VARCHAR
```

### Nullability change

When a model column changes nullable, the corresponding `SET NOT NULL` or `DROP NOT NULL` is generated:

```sql
-- upgrade

ALTER TABLE users ALTER COLUMN email SET NOT NULL

-- rollback

ALTER TABLE users ALTER COLUMN email DROP NOT NULL
```

### Default change

```sql
-- upgrade

ALTER TABLE users ALTER COLUMN role SET DEFAULT 'user'

-- rollback

ALTER TABLE users ALTER COLUMN role DROP DEFAULT
```

### Safe type change (`--safe-type-change`)

For databases that don't support in-place `ALTER COLUMN TYPE` (or when you want to avoid table locks), pass `--safe-type-change` to generate a multi-step strategy:

1. Add a temporary column with the new type
2. Comment indicating a data migration (`UPDATE ... SET temp = CAST(...)`)
3. Verification step comment
4. After manual verification, drop the old column and rename the temporary column

**Limitations:**

| Backend | Supported | Notes |
|---------|-----------|-------|
| PostgreSQL | Yes | Multi-step temp column strategy |
| MySQL / MariaDB | Yes | Multi-step temp column strategy |
| SQLite | No | Comment emitted (SQLite cannot drop columns before 3.35.0 and has limited ALTER TABLE) |
| ClickHouse | No | Comment emitted |

## ClickHouse Engine Recreate

When a ClickHouse table's engine changes, for example `MergeTree` to `ReplicatedMergeTree`, it cannot be altered in-place. DBWarden supports two strategies depending on the table type.

### Table strategy (CREATE + INSERT + RENAME)

For regular `MergeTree`-family tables, DBWarden generates a multi-step operation:

1. Create the new table with the new engine as `<table>__dbw_new`
2. Copy data: `INSERT INTO __dbw_new SELECT ... FROM <table>`
3. Swap: `RENAME TABLE <table> TO <table>__dbw_old, <table>__dbw_new TO <table>`
4. (optional) Drop the preserved old table

**Materialized view targets:** If a materialized view targets the table being recreated (via `TO <table>`), the MV is automatically detached before and reattached after the swap:

```sql
DETACH TABLE events_mv;
CREATE TABLE events__dbw_new (...);
INSERT INTO events__dbw_new SELECT ... FROM events;
RENAME TABLE events TO events__dbw_old, events__dbw_new TO events;
ATTACH TABLE events_mv;
```

**Column renames:** If the table also has column renames, these should be performed in a separate migration (engine recreate and column rename in the same migration is not supported).

### Dictionary strategy (DROP + CREATE)

Dictionaries are recreated with a simple DROP + CREATE since ClickHouse does not support `RENAME DICTIONARY`:

```sql
-- upgrade
DROP DICTIONARY my_dict;
CREATE DICTIONARY my_dict (... ReplicatedMergeTree() ...);

-- rollback
DROP DICTIONARY my_dict;
CREATE DICTIONARY my_dict (... MergeTree() ...);
```

> ⚠️ Dictionaries lose their cached data on recreation. The data will be re-fetched from the source.

### Materialized views and unsupported objects

Engine recreation is **blocked** for tables that are themselves materialized views (`ch_select_statement` or `ch_object_type = materialized_view`). Handle these manually with a DROP/CREATE migration.

Projections (`ch_projections`) are automatically preserved through the table rebuild and do not block it.

### Safety

Rollback for ClickHouse table recreation is deliberately conservative:

| Transition class | Rollback behavior |
|------------------|-------------------|
| Row-preserving engines | Reverse recreate rollback is generated as conditional. |
| Lossy engines | Rollback is irreversible because row-level detail can be lost. |
| Unknown engines | Rollback is irreversible because DBWarden cannot prove safety. |

Lossy engines include `ReplacingMergeTree`, `SummingMergeTree`, `AggregatingMergeTree`, `CollapsingMergeTree`, `VersionedCollapsingMergeTree`, and replicated variants.

| Object type | Safety | Notes |
|------------|--------|-------|
| Regular table with engine change | INFO | Preserved old table by default |
| Table with dependent MVs | INFO | MVs detached before, reattached after |
| Dictionary | CRITICAL | Cached data lost on DROP/CREATE |

### Flags

#### `--clickhouse-engine-recreate`

**Required** to generate engine recreation operations. Without this flag, any detected engine change raises an error:

```
ClickHouse table 'events' cannot be automatically recreated:
is a dictionary (current). This operation requires manual DROP/CREATE,
or use --force to skip this check.
```

#### `--drop-preserved-clickhouse-table` / `--keep-preserved-clickhouse-table`

Controls whether the preserved old table (renamed to `<table>__dbw_old`) is dropped immediately after the swap:

- `--drop-preserved-clickhouse-table`: Drop the old table after successful swap
- `--keep-preserved-clickhouse-table`: Keep the old table (default in non-TTY)

Interactive terminals are prompted to confirm. The preserved table name always ends with `__dbw_old` for easy identification.

### DROP COLUMN warning

All `DROP COLUMN` statements are prefixed with a warning comment:

```sql
-- WARNING: DROPPING COLUMN users.legacy_field

ALTER TABLE users DROP COLUMN legacy_field
```

## Table Rename Detection

When a table is dropped from the snapshot and a new table with similar columns is added to the model, DBWarden auto-detects it as a potential **table rename** using a column-overlap heuristic.

### Auto-detection

| Condition | Outcome |
|-----------|---------|
| A table present in the snapshot but absent from models AND a table absent from the snapshot but present in models | Overlap computed by matching column names and normalized types |
| Overlap ratio ≥ 0.6 | Prompted as a rename candidate |
| Overlap ratio < 0.6 | Emitted as drop+add with a warning comment |

The overlap ratio is `matching_columns / max(len(snapshot_cols), len(model_cols))`. A 0.6 threshold is intentionally conservative: a 10-column table with 6 matching columns is a plausible rename, while a 2-column table with 1 match is not.

### Interactive prompt (TTY)

**Single candidate:**
```
Possible table rename detected:
  users → accounts  (78% columns match)

Treat as rename? [Y/n]:
```

**Multiple candidates:**
```
Possible table renames detected:
  [1] users → accounts     (78% columns match)
  [2] posts → articles     (100% columns match)

Treat as renames? (default: all yes)
  - Press Enter to rename all
  - Type numbers to drop+add instead (e.g. "1" or "1 2"):
```

Table rename prompts appear **before** column rename prompts to ensure table names are resolved before column-level changes.

### CI / non-interactive path

```
Warning: table rename candidates detected but running non-interactive. Emitting drop+add.
  users → accounts  (78% columns match)
Rerun with --rename-table users:accounts to resolve.
```

### `--rename-table` flag

Format: `--rename-table <old_table>:<new_table>`

```bash
# Single table rename
$ dbwarden make-migrations --rename-table users:accounts

# Multiple renames
$ dbwarden make-migrations --rename-table users:accounts --rename-table posts:articles

# Combined with column rename
$ dbwarden make-migrations --rename-table users:accounts --rename accounts.username:email
```

Note: when combining table and column renames, the column rename references the **new** table name. Table renames are applied to the snapshot before column-level processing.

### Table rename edge cases

- **Empty tables**: If either the snapshot table or the model table has zero columns, the overlap ratio is `0.0` and the pair is not a rename candidate.
- **Zero overlap**: If no columns match by name and normalized type, the ratio is `0.0`: emitted as drop+add.
- **Exact match**: If all columns match, the ratio is `1.0`: always a rename candidate.
- **Table rename + column changes in the same table**: After the table rename is applied to the snapshot, column diffs are computed against the new table name. Column renames, type changes, nullable changes, and default changes are all detected on the renamed table.
- **ClickHouse**: `ALTER TABLE RENAME` emits a comment-only placeholder since ClickHouse does not support it.

### SQL generation

All four supported backends (SQLite, PostgreSQL, MySQL, MariaDB) use the same syntax:

```sql
-- upgrade
ALTER TABLE users RENAME TO accounts;

-- rollback
ALTER TABLE accounts RENAME TO users;
```

ClickHouse emits `RENAME TABLE old TO new;` (ClickHouse supports this as a standalone statement).

## Foreign Key and Index Diff

When a schema snapshot exists, `make-migrations` also detects changes to foreign keys and indexes by comparing the snapshot's stored constraints and indexes against the model's declared relationships and indexes.

### Foreign Key vs Index limitations and edge cases

- **Silent skip on missing ref**: If an FK references a table that does not exist in the snapshot, the FK is silently skipped (no error, no SQL). This prevents generating broken SQL but can be surprising. To ensure the FK is emitted, make sure the referenced table exists in the snapshot before running `make-migrations`.
- **Content-based comparison (not name-based)**: Both FKs and indexes are compared by their structural properties, not their names. Renaming a constraint or index does not produce a drop+add.
- **ClickHouse**: FK and index operations emit comment-only placeholders (not supported).
- **SQLite FKs**: Not directly alterable. A comment suggesting table recreation is emitted.

### Foreign Key Detection

| Change | Detection | Generated SQL |
|--------|-----------|---------------|
| **FK added** | FK present in model columns but absent from snapshot constraints | `ALTER TABLE ... ADD CONSTRAINT ... FOREIGN KEY ...` |
| **FK dropped** | FK present in snapshot constraints but absent from model columns | `ALTER TABLE ... DROP CONSTRAINT ...` (or `DROP FOREIGN KEY` on MySQL/MariaDB) |

FKs are compared by content (columns, referenced table, referenced columns), not by name. This means renaming an FK constraint is not treated as a drop+add.

**Validation:** Before emitting an `ADD FOREIGN KEY`, the diff engine verifies that the referenced table and columns exist in the snapshot. If they don't, the FK is silently skipped to avoid generating broken SQL.

**Deferrable constraints (Postgres only):** When detected, `DEFERRABLE INITIALLY DEFERRED` is appended to the constraint SQL.

**SQLite:** FK constraints are not directly alterable. A comment is emitted suggesting table recreation.

### Index Detection

| Change | Detection | Generated SQL |
|--------|-----------|---------------|
| **Index added** | Index present in model but absent from snapshot indexes | `CREATE [UNIQUE] INDEX ... ON table (columns)` |
| **Index dropped** | Index present in snapshot but absent from model indexes | `DROP INDEX ...` |

**Full-content comparison:** Indexes are compared by **all** of these attributes, not just columns + unique. Any difference triggers a drop+add:

| Attribute | SQL Clause | Backend | Example |
|-----------|-----------|---------|---------|
| `using` | `USING <method>` | PostgreSQL, SQLite (partial) | `USING gin`, `USING gist`, `USING hash` |
| `unique` | `UNIQUE` | All | `CREATE UNIQUE INDEX` |
| `where` | `WHERE <predicate>` | PostgreSQL | `WHERE status = 'active'` |
| `include` | `INCLUDE (<cols>)` | PostgreSQL | `INCLUDE (email, name)` |
| `with_params` | `WITH (<params>)` | PostgreSQL | `WITH (fillfactor = 70)` |
| `tablespace` | `TABLESPACE <name>` | PostgreSQL | `TABLESPACE fast_space` |
| `nulls_not_distinct` | `NULLS NOT DISTINCT` | PostgreSQL 15+ | On unique indexes |
| `column_sorting` | Per-column `ASC/DESC NULLS FIRST/LAST` | PostgreSQL | `col1 DESC NULLS LAST, col2 ASC` |
| `type` | `TYPE <type>` | ClickHouse | `TYPE minmax`, `TYPE bloom_filter` |
| `granularity` | `GRANULARITY <n>` | ClickHouse | `GRANULARITY 3` |
| `concurrently` | `CONCURRENTLY` | PostgreSQL | `--concurrent` / `--no-concurrent` |

Omitted attributes or `None`-valued attributes are treated as defaults (btree, no partial, no INCLUDE, etc.), so a plain `Index("ix", "col")` produces the same signature across versions.

**Name generation:** Auto-generated index names follow the pattern:
- `idx_{table}_{col1}_{col2}` for non-unique indexes
- `uq_{table}_{col1}_{col2}` for unique indexes
- Non-btree `USING` methods append a suffix: `idx_{table}_{col}_{method}`

**Backend specifics:**
- PostgreSQL uses `CREATE INDEX CONCURRENTLY` by default (`--concurrent`). Use `--no-concurrent` inside transaction blocks.
- SQLite and MySQL use standard `CREATE INDEX`.
- ClickHouse generates `ALTER TABLE ... ADD INDEX ... TYPE <type> GRANULARITY <n>` for `ChIndexSpec` entries in `ch_indexes`; standard SQL indexes still emit a comment.

## Statement ordering

Operations in the generated migration are ordered consistently:

```
RENAME TABLE        (0)  : table renames first (all subsequent ops use new name)
RENAME COLUMN       (1)
ALTER COLUMN TYPE   (2)
ALTER COLUMN NULLABLE (3)
ALTER COLUMN DEFAULT  (4)
CREATE TABLE        (5)
ADD COLUMN          (6)
ALTER FOREIGN KEY   (7)  : FK adds and drops
ALTER INDEX         (8)  : index adds and drops
DROP COLUMN         (9)
DROP TABLE          (10)
```

Table renames are ordered first so that all subsequent statements reference the new table name.

## Generated artifacts

When a migration is generated, DBWarden writes two files side by side:

- `{database_name}__{version}_{description}.sql`
- `{database_name}__{version}_{description}.plan.json`

The companion plan file contains machine-readable metadata about the generated migration:

- `migration_id`
- `operations`: each operation includes `type`, `table`, `severity` and optionally `resolved_from` (for rename operations)
- `required_flags`
- `checksum`

Example with rename:

```json
{
  "migration_id": "primary__0003_rename_column_users_username",
  "operations": [
    {
      "type": "rename_column",
      "table": "users",
      "new_name": "email",
      "severity": "INFO",
      "resolved_from": "rename_flag"
    },
    {
      "type": "add_column",
      "table": "users",
      "column": "phone",
      "severity": "INFO"
    }
  ],
  "required_flags": [],
  "checksum": "sha256..."
}
```

Possible `resolved_from` values:

| Value | Meaning |
|-------|---------|
| `"rename_flag"` | Explicitly declared via `--rename` or `--rename-table` CLI flag |
| `"prompt"` | Confirmed interactively by the user |
| (absent) | Auto-detected rename kept without prompt (currently unused, reserved) |

`--plan` switches the command into JSON-output mode. In that mode DBWarden prints the plan to stdout and does not write the `.sql` or `.plan.json` files.

## Auto-Generated Names

When no description is provided, DBWarden automatically generates a descriptive name from the schema changes:

| Change | Generated Name |
|--------|----------------|
| Single CREATE TABLE | `create_table_tablename` |
| Multiple CREATE TABLE | `create_tables_users_posts` |
| Single ADD COLUMN | `add_column_tablename_columnname` |
| Multiple ADD COLUMN (same table) | `add_columns_tablename_col1_col2` |
| Single RENAME COLUMN | `rename_column_tablename_new_name` |
| Multiple RENAME COLUMN (same table) | `rename_columns_tablename_col1_col2` |
| Single RENAME TABLE | `rename_table_oldname_newname` |
| Multiple RENAME TABLE | `rename_tables_old1_old2` |
| Single ALTER COLUMN TYPE | `alter_column_type_tablename_col` |
| Single ALTER COLUMN NULLABLE | `alter_column_nullable_tablename_col` |
| Single ALTER COLUMN DEFAULT | `alter_column_default_tablename_col` |
| Single ADD FOREIGN KEY | `add_foreign_key_tablename_ref_table` |
| Single DROP FOREIGN KEY | `drop_foreign_key_tablename` |
| Single ADD INDEX | `add_index_tablename_col` |
| Single DROP INDEX | `drop_index_tablename` |
| Single RECREATE CH TABLE | `recreate_ch_table_tablename` |
| ADD + DROP (same table) | `alter_tablename_col1_col2` |
| Changes across tables | `add_column_users_email_and_1_more_tables` |
| Many targets | `add_columns_tablename_col1_col2_and_3_more` |

### Name Rules

- Snake case throughout.
- Operation words pluralized for multiple targets (e.g., `add_column` → `add_columns`).
- Mixed operations use `alter`.
- Max 72 characters (table/target names truncated as needed).

## Examples

```bash
# Creates primary__0001_create_table_users.sql + .plan.json
$ dbwarden make-migrations --database primary

# Creates primary__0002_add_column_users_email.sql + .plan.json
$ dbwarden make-migrations --database primary

# Creates primary__0003_rename_column_users_email.sql with a confirmed rename
$ dbwarden make-migrations --database primary --rename users.username:email

# Creates primary__0003_alter_column_type_users_bio.sql with type change
$ dbwarden make-migrations --database primary

# Creates primary__0004_add_columns_users_email_name.sql + .plan.json
$ dbwarden make-migrations --database primary

# Creates primary__0003_rename_table_users_accounts.sql with a confirmed table rename
$ dbwarden make-migrations --database primary --rename-table users:accounts

# Uses safe multi-step type change for PostgreSQL
$ dbwarden make-migrations --database primary --safe-type-change

# Uses custom name
$ dbwarden make-migrations "initial_schema" --database primary

# Preview plan JSON without writing files
$ dbwarden make-migrations --database primary --plan
```

## Notes

- Generated file includes both `-- upgrade` and `-- rollback`.
- Generated `.plan.json` files are useful for CI checks and debugging.
- If no models are discovered, configure `model_paths` explicitly.
- With `--dev`, translation can target dev SQLite behavior.
- Schema snapshots are written to `.dbwarden/schemas/` after each successful `migrate`: see [Schema Snapshots](schema-snapshots.md).
- Column-level diff (type/null/default changes) works with a cached schema snapshot, or via a live snapshot taken automatically by `make-migrations` when no cached snapshot exists.
- Without a cached snapshot, `make-migrations` takes a full schema snapshot from the live database internally and detects column-level changes. Only rename detection requires a cached snapshot.
- For authoring guidelines and the review checklist, see [Migration File Format](../migration-files.md).

See also: [Migration File Format](../migration-files.md), [Schema Snapshots](schema-snapshots.md)
