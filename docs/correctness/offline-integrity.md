# Offline Integrity

Offline integrity lets dbwarden generate migrations without connecting to a live database. It does this by comparing current models to a checked-in model state file or schema snapshot instead of querying the database at generation time.

This is a correctness feature because it removes accidental dependence on a developer's local database. CI can generate or verify migrations from repository state alone.

## Snapshot and Model State Concepts

dbwarden uses two related state files.

### Schema Snapshots

After migrations are applied, dbwarden can write checksummed schema snapshots under `.dbwarden/schemas/`. These snapshots represent the database schema after a migration point.

They support:

- Rename detection
- Offline comparisons
- Column-level diffing without querying a live database
- Auditability of schema history

### Model State

`export-models` writes a model state JSON file, usually under `.dbwarden/model_state.json` or a database-specific variant. This file records the model-derived schema state that offline migration generation uses as a baseline.

Create it with:

```bash
dbwarden export-models --database primary
```

Commit it with the repository:

```bash
git add .dbwarden/model_state.json
git commit -m "Update dbwarden model state"
```

The model state file records the schema that dbwarden expects the database to be in after the last migration. It is the baseline for offline diffing and can be regenerated at any time from a live database.

## Offline Migration Generation

Offline migration generation compares the checked-in state file with the current models:

```text
Checked-in model state
  |
  v
Normalize baseline

Current SQLAlchemy models
  |
  v
Extract model state

Baseline + current model state
  |
  v
Offline diff
  |
  v
Generated migration SQL and plan
```

Run it with:

```bash
dbwarden make-migrations "add profile fields" --offline --database primary
```

If the state file is missing, dbwarden tells you to run `export-models` first. If the state file is invalid, dbwarden refuses to use it.

> **If accidentally deleted:** restore it from git (`git checkout .dbwarden/model_state.json`) or regenerate it by running `dbwarden export-models --database <db>` against a live database. Offline commands will work again immediately.

## Integrity Check

Model state and schema snapshots are checksummed. Before dbwarden uses a state file, it validates that the file content matches the stored checksum.

The reason is straightforward:

```text
State file on disk
  |
  v
Recompute SHA-256 checksum
  |
  v
Compare with stored checksum
  |
  +--> match: use the state file
  |
  +--> mismatch: refuse and ask for regeneration
```

This protects against accidental edits, merge corruption, and stale generated files. A modified JSON file should not silently become the baseline for migration generation.

## CI Workflow Example

A deterministic CI workflow can use offline generation first, then live convergence when a database service is available.

```yaml
name: dbwarden-offline-integrity

on:
  pull_request:

jobs:
  offline-integrity:
    runs-on: ubuntu-latest

    steps:
      - uses: actions/checkout@v4

      - uses: actions/setup-python@v5
        with:
          python-version: '3.12'

      - name: Install project
        run: pip install -e .

      - name: Generate migrations from checked-in state
        run: dbwarden make-migrations "ci offline check" --offline --database primary

      - name: Show changed files
        run: git status --short
```

In a strict repository, CI should fail if offline generation creates unexpected changes. That means a developer changed models without committing the corresponding migration or model state update.

The live convergence job can run after this step:

```text
Offline integrity
  |
  v
Generated files are stable
  |
  v
Live convergence gate
  |
  v
Generated SQL applies and matches models
```

See [Convergence Gate](convergence-gate.md).

## Example Failure

Assume the committed model state says `users` has two columns:

```json
{
  "tables": {
    "users": {
      "columns": {
        "id": {"type": "INTEGER", "primary_key": true},
        "email": {"type": "VARCHAR(255)", "nullable": false}
      }
    }
  }
}
```

A developer adds a model field:

```python
display_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
```

Then CI runs:

```bash
dbwarden make-migrations "ci offline check" --offline --database primary
```

dbwarden detects the difference between the committed state and current models:

```sql
-- upgrade
ALTER TABLE users ADD COLUMN display_name VARCHAR(255);

-- rollback
ALTER TABLE users DROP COLUMN display_name;
```

If this migration was expected, commit it. If it was not expected, the model change is accidental and should be reverted or corrected.

## Why Offline Integrity Improves Correctness

Offline integrity decouples migration generation from live database accidents.

Without offline state, a developer's local database could contain manual changes. dbwarden might diff against that local drift and produce a migration that looks correct on one machine but fails in CI or production.

With offline state:

- The baseline is versioned in git.
- The same inputs produce the same diff.
- CI can detect missing migrations without a database service.
- Tampered state files are rejected by checksum validation.

Offline integrity is not the final proof. The final proof is still a live database convergence gate. Offline integrity ensures the inputs to that gate are deterministic.
