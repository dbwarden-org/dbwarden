# 12. Apply, roll back, and reapply

A reviewed bundle still changes real rows. This page walks the execution
lifecycle: preview, apply, roll back, and start a new epoch when you want the
data back.

## What you'll learn

- How to preview and apply a frozen data bundle, and when `--force` is required
- What the execution journal records and which states it moves through
- How rollback follows the frozen rollback plan and refuses unsafe reversals
- Why an ordinary `migrate` will not replay a rolled-back migration
- How `migrate --reapply-data` starts a new linked epoch
- How `--baseline` records history without executing the plan

## Prerequisites

- [Page 11](./11-review-and-generate.md) completed: a generated bundle you have
  reviewed.
- The [lifecycle example](../../../examples/data-migrations/lifecycle/) to follow
  along, or an equivalent project.

## Step 1: Preview, then apply

`migrate --dry-run --data` inspects pending frozen bundles after version and
severity selection. It verifies and renders the bundle without writing:

```bash
dbwarden migrate --database primary --dry-run --data
```

```text
DRY RUN - No changes applied
0001: acknowledgement required. hint: review the plan and pass --force
Preflight checks would abort this migration.
Dry-run summary: 1 versioned, 0 runs-always, 0 runs-on-change migrations would be applied.
```

The `acknowledgement required` line is the severity gate: data operations that
change rows are reported as `WARN` or `CRITICAL`, so `--force` is needed to
acknowledge them. Apply under the normal migration lock:

```bash
dbwarden migrate --database primary --force
```

```text
[APPLIED] Completed migration: primary__0001_… (version: 0001)
Migrations completed successfully: 1 migrations applied.
```

Use `--force` only for severity you have reviewed. It acknowledges operation risk;
it does not raise a severity ceiling or bypass drift.

## Step 2: The execution journal

Execution records state in `_dbwarden_data_runs` and `_dbwarden_data_events`.
A run records the migration ID, application epoch, bundle checksum, run ID, status,
last completed operation, sanitized error, baseline flag, and timestamps.
Nontransactional backends also persist per-statement checkpoints.

Migration states are:

```text
NOT_STARTED
APPLYING
FAILED_RETRYABLE
FAILED_FINAL
UNKNOWN_REQUIRES_RECONCILIATION
APPLIED_SUCCESS
ABANDONED
ROLLING_BACK
ROLLED_BACK
```

The happy path is `NOT_STARTED → APPLYING → APPLIED_SUCCESS`. A checksum-matched
repeat returns as already applied and performs no migration writes.

## Step 3: Roll back

`rollback` uses the frozen rollback plan in the bundle; it never re-imports the
declarations and never invents reverse SQL. It refuses irreversible declarations
and stops when owned values have changed since apply:

```bash
dbwarden rollback --database primary --count 1
```

```text
Rollback completed successfully: 1 migration(s) reverted.
```

A completed rollback records `ROLLED_BACK`. A `capture` transformation restores
the recorded typed preimage, but only while the current value still matches the
recorded postimage — later application edits cause a conflict instead of being
overwritten. Irreversible data operations refuse rollback outright; a comment or
empty SQL section is not a successful reversal.

## Step 4: Reapply starts a new epoch

Ordinary `migrate` does **not** silently replay rolled-back data. Reapply is a
separate operator decision that creates a new epoch linked to the previous one:

```bash
dbwarden migrate --database primary --reapply-data
```

`--reapply-data` explains the intent to reapply, but `--force` is still needed to
acknowledge `WARN`/`CRITICAL` data operations:

```bash
dbwarden migrate --database primary --force --reapply-data
```

`--force` does not authorize reapply on its own, and `--reapply-data` does not
rerun an already successful migration. Successful migrations stay skipped.

## Step 5: Baseline without executing

Baseline records history without executing the data plan. It requires an exact
target version and stores an explicit acknowledgement, reason, and the skipped
checks. It does not prove current data convergence:

```bash
dbwarden migrate --database primary --baseline --to-version 0012
```

## Step 6: Walk the lifecycle example

The lifecycle example ends with a rollback/reapply/convergence script. It rolls
back its two data migrations, inspects the restored rows, reapplies, and checks
convergence:

```bash
bash scripts/04-rollback-reapply.sh
```

```text
=== 04: Rollback, reapply, converge ===
Rollback completed successfully: 2 migration(s) reverted.
restored item:    [(1, 7, 3), (2, 10, 4), (3, 20, 5)]
restored country: []
Migrations completed successfully: 2 migrations applied.
reapplied item:   [(1, 7, 8), (2, 10, 11), (3, 20, 21)]
country:          [('UY', 'Uruguay')]
country_archive:  [('AR', 'Argentina')]
```

Read the transitions in that output:

- The capture transformation restored `item.value` to its pre-write preimage
  `(3, 4, 5)` after rollback.
- Rollback runs in reverse: it first rolls back the archive revision (restoring
  `AR` from `country_archive`), then rolls back the seed revision, which removes
  the rows it owned. That is why `restored country` ends up empty.
- Reapply recomputed `value = source + 1` and re-archived `AR`.

The example README notes that reapplying re-registers the schema snapshot and may
log a benign "Snapshot ID already registered" warning during replay; the data
outcome is what matters.

## Recap

- `migrate --dry-run --data` previews; `migrate --force` applies when severity
  requires acknowledgement.
- The journal records runs and events; states move through `APPLYING`,
  `APPLIED_SUCCESS`, `ROLLED_BACK`, and the failure states.
- `rollback` follows the frozen plan and refuses irreversible or changed state.
- Ordinary `migrate` never replays rolled-back data; `migrate --reapply-data`
  starts a new linked epoch and still needs `--force` for `WARN`/`CRITICAL`.
- `--baseline --to-version` records history without executing the plan.

## What's next

When an apply fails with uncertain durable effects, you must reconcile before you
retry. Continue with [13. Reconcile and convergence](./13-reconcile-and-convergence.md).
