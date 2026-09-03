# Safety Classifier

The safety classifier detects risky schema changes before migration execution. It reads the migration plan, classifies each operation, and forces the operator to acknowledge changes that may affect data or availability.

The principle is simple: dbwarden should not silently drop data.

## What It Scans

`make-migrations` writes a companion `.plan.json` file next to generated SQL migrations. The plan records the typed operations that produced the SQL. A safety check can inspect that plan before the SQL is applied.

The flow is:

```text
Models or manual migration request
  |
  v
Diff engine
  |
  v
Typed operations
  |
  v
.plan.json
  |
  v
Safety classifier
  |
  v
Safe, warning, or blocking result
```

Run the check command before applying migrations:

```bash
dbwarden check --database primary
```

If the command reports warnings that require acknowledgement, use the documented command option only after reviewing the plan:

```bash
dbwarden check --database primary --force
```

`--force` is an acknowledgement. It should not be used as a default in CI.

## Severity Levels

dbwarden safety classifications map to three operational meanings.

### Info

Info-level changes are expected to be safe from a schema perspective.

Examples:

- Create a table.
- Create an index.
- Add a nullable column.
- Add a column with a safe default on an empty table.

Example operation:

```sql
ALTER TABLE users ADD COLUMN nickname VARCHAR(255);
```

Why it is low risk:

- Existing rows remain valid because the column is nullable.
- No data is removed.
- The operation is visible in the migration file and plan.

### Warning

Warning-level changes may be valid, but they need human review.

Examples:

- Add `NOT NULL` to a column when existing data may violate it.
- Change a column type where conversion may fail.
- Change storage parameters or backend-specific physical settings.
- Recreate a ClickHouse table for an engine transition that is classified as row-preserving but still operationally sensitive.

Example operation:

```sql
ALTER TABLE users ALTER COLUMN status SET NOT NULL;
```

Questions to answer before allowing it:

- Are there existing rows with `NULL` in `status`?
- Does the migration include a backfill if needed?
- Does the backend require a lock that could affect production traffic?

### Blocking

Blocking changes are destructive or ambiguous enough that they must not be hidden inside an ordinary migration review.

These changes are classified as `ERROR` severity in the safety check output.

Examples:

- Drop a table.
- Drop a column.
- Rename a table without explicit rename intent.
- Change a ClickHouse engine in a way that can collapse, aggregate, or otherwise lose row-level data.

Example operation:

```sql
DROP TABLE audit_log;
```

Why it blocks:

- The table data is removed.
- Rollback may recreate the table shape, but it cannot recover deleted rows.
- The operator must explicitly confirm this is intended.

## Examples by Severity

### Info: Add a Nullable Column

Model change:

```python
class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    display_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
```

Generated SQL:

```sql
-- upgrade
ALTER TABLE users ADD COLUMN display_name VARCHAR(255);

-- rollback
ALTER TABLE users DROP COLUMN display_name;
```

The upgrade does not invalidate existing rows. The rollback removes the newly added column.

### Warning: Add NOT NULL

Model change:

```python
status: Mapped[str] = mapped_column(String(32), nullable=False)
```

Generated SQL may include:

```sql
ALTER TABLE users ALTER COLUMN status SET NOT NULL;
```

This is structurally valid but operationally risky if existing rows contain `NULL`. The safe approach is to backfill first, verify, then enforce `NOT NULL`.

### Blocking: Drop a Column

Model change:

```python
# legacy_code was removed from the model
```

Generated SQL may include:

```sql
ALTER TABLE users DROP COLUMN legacy_code;
```

This removes stored data. A rollback can add the column back, but it cannot reconstruct the dropped values. The plan should be reviewed before applying.

## How It Fits in CI

A practical CI sequence is:

```text
Generate migration
  |
  v
Inspect plan and safety classification
  |
  v
Reject unexpected warnings or blocking changes
  |
  v
Apply to ephemeral database
  |
  v
Run convergence gate
```

The safety check runs before the convergence gate. Safety asks whether the operation is acceptable. Convergence asks whether the resulting schema is correct.

See [Convergence Gate](convergence-gate.md).

## Philosophy

dbwarden does not try to guess business intent. It can tell that a column drop destroys stored values, but it cannot know whether those values are obsolete. It can identify that a ClickHouse engine transition is lossy, but it cannot know whether the application already copied the data elsewhere.

For that reason, the safety classifier makes risk visible and requires acknowledgement. It is a correctness mechanism because it prevents accidental data loss from being treated as ordinary schema drift.
