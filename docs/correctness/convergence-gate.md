# Convergence Gate

The convergence gate is the strongest correctness check in dbwarden. It proves that the complete migration history can reproduce the schema declared by the current models.

The gate answers one question:

> If a new environment starts with an empty database and applies every migration, does the resulting schema exactly match the models in the repository?

If the answer is no, CI should fail.

## Definition

Convergence means:

```text
Empty database
  |
  v
Apply every migration
  |
  v
Extract live schema
  |
  v
Compare live schema with model spec
  |
  v
Pass only if there is zero drift
```

This is an end-to-end, model-history-consistency check. It does not only test the newest migration. It tests the entire path from an empty database to the current model state.

## Pipeline

The typical CI pipeline has four phases.

### 1. Spin Up an Ephemeral Database

CI starts a disposable database service. For PostgreSQL this is usually a container. For ClickHouse this can be a ClickHouse service container. The database should be empty at the start of the job.

The important property is isolation. The gate should never run against a shared developer database, because manual changes in that database would hide or create drift that is unrelated to the repository.

### 2. Apply the Full Migration History

Run the same command used in deployment:

```bash
dbwarden migrate --database primary
```

This applies versioned migrations and any repeatable migration behavior supported by the project configuration. The gate tests the migration files that will run in production, not a simplified test fixture.

### 3. Check the Resulting Schema

Run dbwarden's diff command:

```bash
dbwarden diff --database primary
```

The diff compares the database state with the model state. If the command reports differences, CI fails. `dbwarden check` is a separate safety classifier, not the schema-convergence comparison. Some teams also run `dbwarden diff` in table or JSON mode for diagnostic output:

```bash
dbwarden diff --database primary --out table
dbwarden diff --database primary --out json
```

Use the diff output to see which object drifted. The gate should not ignore drift. If the models are correct, generate or write a migration. If the migration is correct, update the model.

### 4. Fail Fast on Drift

The final rule is simple:

```text
No drift -> merge allowed
Any drift -> merge blocked
```

The migration history and the models must agree.

## GitHub Actions Example

This example uses PostgreSQL. The same pattern applies to any backend that can run in CI.

```yaml
name: dbwarden-convergence

on:
  pull_request:
  push:
    branches: [main]

jobs:
  convergence:
    runs-on: ubuntu-latest

    services:
      postgres:
        image: postgres:16
        env:
          POSTGRES_USER: dbwarden
          POSTGRES_PASSWORD: dbwarden
          POSTGRES_DB: app
        ports:
          - 5432:5432
        options: >-
          --health-cmd pg_isready
          --health-interval 10s
          --health-timeout 5s
          --health-retries 5

    env:
      DATABASE_URL: postgresql://dbwarden:dbwarden@localhost:5432/app

    steps:
      - uses: actions/checkout@v4

      - uses: actions/setup-python@v5
        with:
          python-version: '3.12'

      - name: Install project
        run: pip install -e .

      - name: Apply migrations
        run: dbwarden migrate --database primary

       - name: Check convergence
         run: dbwarden diff --database primary

      - name: Print drift diagnostics on failure
        if: failure()
        run: dbwarden diff --database primary --out table
```

The `--health-cmd` option belongs to Docker service configuration. The dbwarden commands are the two important convergence commands: `migrate` and `diff`.

## Measured Cost

The cost is dominated by replaying the migration history, not by the final
schema diff. On 2026-08-14, the harness measured a synthetic 500-migration
SQLite project with one table per migration:

| dbwarden executable | Preparation | Full replay | Final diff | Total |
|---|---:|---:|---:|---:|
| Current core checkout | 2.2s | 274.1s | 6.0s | 282.3s |
| PyPI `0.16.5` | 1.8s | 291.8s | 6.7s | 300.3s |

The current core checkout also supports bulk replay with one final snapshot:

```bash
dbwarden migrate --defer-snapshots
```

The same 500-migration SQLite workload measured `10.4s` for replay and
`18.3s` total with deferred snapshots. This preserves the final convergence
artifact while avoiding a full schema reflection after every migration. The
default remains per-migration snapshots for compatibility with audit workflows;
long-history CI gates should use the deferred mode and retain the final
snapshot as their correctness artifact.

These are local reference measurements, not an SLA. Database startup,
filesystem speed, migration complexity, indexes, plugins, and backend network
latency can materially change the result. The benchmark is reproducible from
the standalone harness with:

```bash
DBWARDEN_HARNESS_RUN_500_MIGRATION=1 uv run pytest \
  suites/performance/test_convergence_500.py -s
```

The current `convergence-audit` GitHub Actions job runs `pytest -m p0`. At
present only a handler-level ClickHouse test carries the `p0` marker, so that
job is not equivalent to replaying a project's full migration history. A
project that needs the stronger guarantee should run the harness's
provider-backed convergence suite as a separate, explicitly budgeted job.

## Example Error Caught by the Gate

Assume a model declares an index:

```python
class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(255), nullable=False)

    class Meta:
        indexes = [IndexSpec(name="idx_users_email", columns=["email"])]
```

But the migration only creates the table:

```sql
-- upgrade
CREATE TABLE users (
    id INTEGER NOT NULL PRIMARY KEY,
    email VARCHAR(255) NOT NULL
);

-- rollback
DROP TABLE IF EXISTS users CASCADE;
```

The migration applies successfully. A unit test that only checks syntax might pass. Production would have a table without the declared index.

The convergence gate catches this:

```text
Model state:
  users has idx_users_email

Extracted database state after migrations:
  users has no idx_users_email

Result:
  drift detected
```

The fix is to generate or add a migration that creates the missing index.

## Other Errors It Catches

### Leftover Column

A column is removed from the model, but no migration drops it. The gate applies all migrations, extracts the schema, and sees that the database still contains the old column.

### Reversed Migration

A migration accidentally renames `customer_id` to `client_id` while the model still declares `customer_id`. The migration applies, but the final schema disagrees with the model.

### Missing Backend Metadata

PostgreSQL table storage parameters, identity options, RLS policies, or ClickHouse engine settings are easy to miss in hand-written SQL. The gate compares the extracted backend-specific shape, not only the visible column list.

## Why This Is the Gold Standard

The convergence gate tests exactly the behavior that matters in production:

- It uses the real migration files.
- It uses the real backend SQL emitters.
- It uses a real database engine.
- It verifies the final schema against the models.
- It catches drift introduced anywhere in the history.

This is stronger than checking that a single generated migration looks plausible. A migration can be syntactically valid and still fail to converge.

## Relationship to Offline Integrity

Offline workflows are useful when CI cannot reach a database service. They let dbwarden generate migrations from a checked-in model state instead of a live database. That is deterministic, but it does not prove the SQL applies to a real engine.

A strong pipeline uses both:

```text
Offline integrity check
  |
  v
Generate or verify migration files deterministically
  |
  v
Convergence gate on a live database
  |
  v
Prove the SQL reproduces the model state
```

See [Offline Integrity](offline-integrity.md) for the first half of that workflow.
