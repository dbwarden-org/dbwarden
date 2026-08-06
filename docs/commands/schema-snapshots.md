# Schema Snapshots

Schema snapshots are JSON files that record the full DDL state of a database at the point a migration was applied. They enable offline migration generation, intelligent rename detection, and column-level change detection (type, nullability, default).

## How they work

After each versioned migration is successfully applied by `migrate`, DBWarden extracts the complete schema from the live database and writes it as a `<migration_id>.schema.json` file:

```
.dbwarden/schemas/
  primary__0001_init.schema.json
  primary__0002_add_email.schema.json
  primary__0003_create_posts.schema.json
```

### Snapshot contents

Each snapshot captures:

```json
{
  "format_version": 2,
  "migration_id": "primary__0003_create_posts",
  "database_name": "primary",
  "database_type": "postgresql",
  "applied_at": "2026-06-07T10:30:00Z",
  "checksum": "sha256...",
  "tables": {
    "users": {
      "object_type": "table",
      "columns": {
        "id": {
          "name": "id",
          "type": "integer",
          "nullable": false,
          "primary_key": true,
          "default": null,
          "comment": null,
          "pg_column": {}
        },
        "email": {
          "name": "email",
          "type": "varchar",
          "nullable": false,
          "primary_key": false,
          "default": null,
          "comment": null,
          "pg_column": {}
        }
      },
      "backend_table_spec": {"backend": "postgresql"},
      "comment": null
    }
  },
  "enums": {},
  "indexes": {},
  "constraints": {}
}
```

Fields added in format v2:

| Field | Level | Purpose |
|-------|-------|---------|
| `backend_table_spec` | Per-table | Backend-specific table options (e.g., `ch_engine`, `pg_fillfactor`, `my_engine`) |
| `pg_column` / `ch_column` / `my_column` | Per-column | Backend-specific column metadata (e.g., `ch_type`, `ch_codec`, `pg_type`, `my_unsigned`) |
| `object_type` | Per-table | `"table"`, `"materialized_view"`, or `"dictionary"` |
| `name` | Per-column | Column name (v1 stored names as keys; v2 stores them in both key and value) |

**Backward compatibility:** Snapshots with `format_version: 1` are automatically normalized to v2 when read. V1 keys like `clickhouse_options`, `pg_table`, `my_table`, and per-table `indexes`/`primary_key` are mapped to their v2 equivalents.

### Integrity

Snapshots are self-integrity-checked with a SHA-256 checksum. If a snapshot file is tampered with (manually edited, corrupted), `read_snapshot()` returns `None` and `make-migrations` falls back to the live database.

## Why snapshots?

### Offline diffing

Once the first snapshot exists, `make-migrations` can generate new migrations **without a live database connection**. This is useful in:

- CI pipelines where only model files are available
- Air-gapped environments
- Development setups without a full database

### Rename detection (columns)

Rename detection relies on comparing the snapshot's columns against the model's columns:

1. A column present in the snapshot but absent from the model → **dropped**.
2. A column present in the model but absent from the snapshot → **added**.
3. If a dropped column and an added column share the same normalized type, they are candidates for a **rename**.

Without snapshots (the legacy live-DB fallback path), rename detection is not possible because the live DB already reflects the current state and has no record of dropped columns.

### Rename detection (tables)

Table renames are detected by comparing tables present in the snapshot but absent from models (dropped) against tables absent from the snapshot but present in models (added). A **column overlap heuristic** computes the ratio of matching column names+types between the two tables. If the ratio is ≥ 0.6, the pair is a rename candidate.

Detected table renames are prompted interactively (TTY) or suggested via the `--rename-table` flag (CI). Confirmed renames emit `ALTER TABLE ... RENAME TO` and are applied to the snapshot before column-level processing, so subsequent column renames reference the new table name.

### Column-level change detection

Snapshots also enable `make-migrations` to detect when a column's **type**, **nullability**, or **default** has changed. For columns present in both the snapshot and the model with the same name, the diff engine compares:

- **Normalized type**: If `varchar` in the snapshot but `text` in the model, an `ALTER COLUMN TYPE` operation is emitted.
- **Nullable flag**: If nullable differs, `SET NOT NULL` or `DROP NOT NULL` is generated.
- **Default value**: If the default differs, `SET DEFAULT` or `DROP DEFAULT` is generated.

When a cached snapshot exists, these operations are compared against the historical schema. Without a cached snapshot, `make-migrations` takes a full schema snapshot from the live database internally and detects column-level changes (type, nullability, default) from that live snapshot. Only rename detection requires a cached snapshot.

### Foreign key and index change detection

Snapshots also store the database's foreign keys (in `constraints`) and indexes (in `indexes`). The diff engine compares these against the model's declared FK relationships (parsed from `ModelColumn.foreign_key`) and indexes (extracted from `__table__.indexes`). Comparisons are content-based (columns, referenced table, referenced columns for FKs; columns + unique flag for indexes), so constraint/index name changes are not treated as drop+add.

### Audit trail

Every schema snapshot is an immutable record of the database schema at a specific migration version. You can inspect any historical snapshot to see exactly what the schema looked like.

## How they are created

Snapshots are created automatically by the `migrate` command after applying a versioned migration:

```
$ dbwarden migrate --database primary
```

The snapshot is written **after** all pending migrations have been applied. If the write fails (permission issue, disk full, etc.), a warning is logged but the migration itself is not rolled back. Failure to write a snapshot is non-fatal.

### Snapshots are NOT created for

- `--dry-run` or `--sandbox` runs (no real schema change)
- Rollback operations (snapshot remains as-is for audit)
- Repeatable migrations (`RA__`, `ROC__`)

## Rollback and re-apply

- **Rollback does not delete the snapshot.** The snapshot stays as an audit record of what was applied.
- **Re-applying a migration** overwrites the snapshot with the current schema state.

## Finding the latest snapshot

`make-migrations` uses `find_latest_snapshot()` which scans `.dbwarden/schemas/` for snapshot files matching the current database name and picks the one with the highest version prefix (e.g., `0003` > `0002`).

If no snapshot exists for the database, `make-migrations` takes a full schema snapshot from the live database internally and runs the standard diff pipeline against it. This enables column-level change detection (type, nullability, default). Only rename detection requires a cached snapshot.

## Snapshot lifecycle summary

| Event | Snapshot |
|-------|----------|
| First `migrate` | Created after apply |
| Subsequent `migrate` | Overwritten with latest schema |
| `rollback` | Unchanged (kept as audit) |
| Re-apply same version | Overwritten |
| `--dry-run` / `--sandbox` | Not written |
| `make-migrations` (snapshot exists) | Read for diff + rename detection |
| `make-migrations` (no snapshot) | Fallback to live DB diff |

## DB-agnostic type normalization

Column types in the snapshot are normalized to a canonical set so that equivalent types across databases are treated the same:

| Canonical type | Matches |
|----------------|---------|
| `integer` | INT, INTEGER, INT4, TINYINT, SMALLINT |
| `biginteger` | BIGINT, INT8 |
| `varchar` | VARCHAR, CHARACTER VARYING |
| `text` | TEXT, LONGTEXT, CLOB |
| `boolean` | BOOLEAN, BOOL |
| `timestamp` | TIMESTAMP, DATETIME |
| `numeric` | NUMERIC, DECIMAL (with precision/scale) |
| `float` | FLOAT, REAL, DOUBLE |
| `bytes` | BYTEA, BLOB, BINARY |
| `uuid` | UUID |
| `enum` | ENUM |
| (unknown) | Stored as-is with `"raw": true` |

This normalization is what powers the rename detection: two columns with the same normalized type are candidates for rename, even if their raw SQL type strings differ.

## Edge Cases and Restrictions

### Rename detection
- **Ambiguous renames**: When multiple columns of the same type are dropped and added, all possible pairs are treated as renames (not just one). This maximizes detection but may produce false positives that must be confirmed interactively or via `--rename`.
- **Type change prevents rename**: If a dropped column and an added column have different normalized types, they are never auto-detected as renames. Use `--rename` to force the rename anyway.
- **Same name**: If a column with the same name exists in both the snapshot and the model, no rename is detected even if its type changes (that is handled by type-change detection).

### Table rename detection
- **Column-overlap heuristic**: The ratio is `matching_columns / max(len(snapshot_cols), len(model_cols))`. The 0.6 threshold is intentionally conservative.
- **Empty tables**: Either table having zero columns results in a ratio of `0.0` (no candidate).
- **Table rename + column diff interaction**: Confirmed table renames are applied to the snapshot before column diffs are computed. This ensures column renames and other column-level changes reference the new table name.

### Foreign key and index detection
- **Silent skip on missing ref**: If an FK references a table that does not exist in the snapshot, the FK is silently skipped (no error, no SQL emitted). This prevents broken SQL but can be surprising. Ensure the referenced table exists in the snapshot first.
- **Content-based comparison**: FKs are compared by `(columns, referenced_table, referenced_columns)`. Indexes are compared by `(frozenset(columns), unique)`. Renaming a constraint or index does not produce a drop+add.
- **ClickHouse**: FK and index operations emit comment-only placeholders (not supported by ClickHouse).
- **SQLite FKs**: A comment is emitted suggesting table recreation (not directly alterable).

### Column-level change detection
- **Cached snapshot not required**: Column-level diff works with a live snapshot taken automatically by `make-migrations`. A cached snapshot enables rename detection in addition to column-level diff.
- **Backend limits**: Type changes emit different SQL per backend. SQLite emits comment-only placeholders for type and nullable changes. ClickHouse auto-generates `MODIFY COLUMN` for type, nullable, and LowCardinality changes. Default changes work uniformly across all backends.

### Integrity
- **Tampered snapshots**: If the checksum does not match, `read_snapshot()` returns `None`, and `make-migrations` falls back to the live database. A warning is logged.
- **Checksum-excluded fields**: The `checksum` field itself is excluded from the hash computation, so checksum updates do not cascade.
