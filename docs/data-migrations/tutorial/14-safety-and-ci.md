# 14. Safety and CI

Data changes deserve the same gated rollout as schema changes. This page shows
how dbwarden classifies data operations, how to gate them by severity, and how to
drive the whole review sequence from CI.

## What you'll learn

- How data operations get `INFO`, `WARN`, or `CRITICAL` severity
- How `pre_migrate_safety` and `--force` acknowledgement work together
- How to cap execution with `--max-severity`, split with `--split-at-severity`,
  and require composable plans with `--strict-pending`
- How `category=` groups data operations into separate migrations
- A concrete offline CI sequence

## Prerequisites

- [Page 13](./13-reconcile-and-convergence.md) completed.
- Familiarity with [safety-scoped migrations](../../correctness/safety-scoped-migrations.md)
  and [CI/CD patterns](../../advanced/ci-cd-patterns.md).

## Step 1: Severity of data operations

Every data operation carries a severity derived from its declaration and rollback
behavior. Execution reads the plan; it never classifies at deploy time.

| Level | Examples |
|---|---|
| `INFO` | Managed-row seed; a validation-only declaration |
| `WARN` | A derived write or backfill; a reversible managed-row revision |
| `CRITICAL` | Dropping a transition source; overwrite; irreversible archive |

That is why the earlier pages ran `migrate --force`: seeds, backfills, and archives
are `WARN` or `CRITICAL` by design. A passing compile and a successful guard prove
only the declared checks ran - they do not prove application semantics or data
quality outside declared ownership. Severity makes the risk visible and requires
acknowledgement.

## Step 2: `pre_migrate_safety` and `--force`

`pre_migrate_safety` is a project policy with values `off`, `warn`, or `block`
(default `off`). When set to `block`, a `WARNING`-level change stops a migration
unless you acknowledge it with `--force`:

```bash
dbwarden migrate --database primary --force
```

`--force` is an acknowledgement, not a default. It does not raise a severity
ceiling and cannot bypass unknown SQL classification or data drift. A comment or
empty SQL section is not a successful rollback, and a stale acknowledgement whose
semantic checksum no longer matches is invalid.

## Step 3: Gate execution by severity

`--max-severity` applies a strict version prefix and stops before the first
higher-severity or unknown file:

```bash
dbwarden migrate --database primary --max-severity INFO
```

```text
Stopped: …/primary__0001_seed_reference_data.sql has file severity WARN, above ceiling SAFE.
1 migration(s) deferred. hint: run with a higher --max-severity to apply.
```

The command exits **3** on a severity deferral. Later versions and repeatables do
not run. UNKNOWN files stop under every ceiling, including the default `CRITICAL`.

Generate the low-risk base and the higher-risk work as separate files with
`--split-at-severity`:

```bash
dbwarden make-migrations --database primary --split-at-severity WARN
```

```text
Generated: primary__0002_seed_reference_data__deferred.sql (2 ops, max severity WARN)
```

Each file has its own upgrade, rollback, and plan. The base contains operations
below the threshold; the deferred file contains operations at or above it and
everything that depends on them. `--strict-pending` refuses to proceed when
pending files lack composable plans (override a configured policy with
`--no-strict-pending`).

## Step 4: Group data with `category=`

`category=` on `rows`, `derive`, or `DataTransition` selects a named migration
group:

```python
class Data(DataMeta):
    managed_rows = rows(
        key="code",
        rows=[{"code": "UY", "name": "Uruguay"}],
        owned_columns=["name"],
        rollback="restore_previous",
        category="reference",
    )
```

Groups reuse the existing category and CLI plugin API; they do not add severity
levels. Safety-scoped generation defers dependent data and schema operations
together, and each generated group has its own verified bundle. See
[safety-scoped migrations](../../correctness/safety-scoped-migrations.md) for
group order and split closure.

## Step 5: Offline generation for CI

Generation can run without a database connection, which is exactly what a CI
runner needs:

```bash
dbwarden export-models --database primary
dbwarden make-migrations --database primary --offline
```

```text
Exported 3 model(s) to .dbwarden/model_state.primary.json
```

Commit `.dbwarden/model_state.primary.json` so generation has a stable baseline,
then generate against it on every build. A historical source without a pinned
snapshot fails before SQL generation, so offline mode cannot silently invent
lineage.

## Step 6: A CI sequence

A practical pipeline generates the change, checks data convergence, and previews
the frozen bundle before any deploy job applies it:

```bash
# 1. Freeze the model baseline, then generate offline.
dbwarden export-models --database primary
dbwarden make-migrations "…" --database primary --offline --strict-pending

# 2. Prove declared data and schema converge and classify the operations.
dbwarden check --database primary --data

# 3. Preview the frozen bundle without writes.
dbwarden migrate --database primary --dry-run --data
```

Run `check --data` and `migrate --dry-run --data` as PR gates; keep the actual
`dbwarden migrate` in exactly one deploy job, serialized with
`concurrency`/`resource_group` so two agents never migrate the same database at
once. See [CI/CD patterns](../../advanced/ci-cd-patterns.md) for the full job
layout.

## Recap

- Data operations are `INFO`, `WARN`, or `CRITICAL`; execution reads the plan and
  never classifies at deploy time.
- `pre_migrate_safety = block` and `--force` are a paired acknowledgement, not a
  bypass.
- `--max-severity` caps execution (exit 3); `--split-at-severity` separates low-
  and high-risk files; `--strict-pending` requires composable plans.
- `category=` groups operations into separate verified bundles.
- Offline generation plus `check --data` and `migrate --dry-run --data` make a
  safe CI gate before a single serialized deploy job.

## What's next

Consolidate everything in the [recap](./recap.md), then return to the
[Reference](../reference.md) and the runnable examples for day-to-day work.
