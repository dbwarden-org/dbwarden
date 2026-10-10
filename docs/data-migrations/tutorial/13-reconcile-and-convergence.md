# 13. Reconcile and convergence

Some failures leave the database in a state nobody can assume away. This page
covers how dbwarden refuses to guess, how you reconcile deliberately, and how to
prove that declared data has actually converged.

## What you'll learn

- What `UNKNOWN_REQUIRES_RECONCILIATION` means and why nontransactional backends need it
- How to inspect journal state and record a reconciliation decision
- What evidence `verified_retry` requires
- How `check --data` and `diff --data` define convergence
- Why reserved journal and ownership tables are excluded from drift

## Prerequisites

- [Page 12](./12-apply-rollback-reapply.md) completed: you can apply and roll back.
- The [transitions example](../../../examples/data-migrations/transitions/) or an
  equivalent project with a transition.

## Step 1: Why reconciliation exists

Transactional backends can prove that a failed attempt rolled back completely, so
a retry is safe. Nontransactional backends cannot. MySQL and MariaDB commit each
DDL statement implicitly; a crash after a possible effect but before verification
leaves an unknown cut. Marking it `FAILED_RETRYABLE` would risk applying the same
effect twice.

So a possible durable effect produces `UNKNOWN_REQUIRES_RECONCILIATION`, and
ordinary `migrate` stops. The rule is simple: automatic retry is allowed only when
the whole attempted segment rolled back atomically, or reconciliation proves every
completed effect and the next action. An ambiguous partial statement stays blocked.

## Step 2: Inspect, then decide

Inspect the journal state first. Inspection writes nothing:

```bash
dbwarden data transition reconcile primary__0002_split_legacy_people --database primary
```

On a clean, applied migration the projection looks like:

```json
{
  "migration_id": "primary__0002_split_legacy_people",
  "direction": "upgrade",
  "epoch": 1,
  "status": "APPLIED_SUCCESS",
  "retry_allowed": false,
  "last_operation": "768925d3cee0daaebc855b711b3124ab2a58e7edfb48a7597e5aa9258e7a4fe1",
  "error": null
}
```

After repairing or verifying the database, record only a decision the reported
state permits:

```bash
dbwarden data transition reconcile primary__0002_split_legacy_people \
  --database primary --apply --decision verified_retry
```

`--decision abandon` is an audited terminal decision where supported. It does not
mark the declared state converged. The command takes the migration lock before it
writes.

## Step 3: What `verified_retry` requires

`verified_retry` is not "try again and hope". It requires backend-specific proof:
transactional rollback evidence, or matching checkpoints and verified
postconditions for durable statements. `verified_retry` does not ignore conflicts
or infer ownership from equivalent values.

Two important limits:

- On MySQL/MariaDB, ownership inserts must match the driver's affected-row count.
  A crash with only a `PENDING` ownership insert cannot be reconciled from
  equivalent target values alone, because those values may belong to an
  application writer.
- A partially completed MySQL/MariaDB rollback can stay blocked when it already
  removed or changed rows the original identity proof needed. Verified checkpoints
  alone do not bypass that proof; inspect and repair, because dbwarden will not
  guess the missing before-image.

## Step 4: Convergence with `check --data` and `diff --data`

Convergence compares current desired declarations with actual state. It never
regenerates or replays old migration code.

`check --data` succeeds only when schema, pending migration SQL, and declared data
all converge. It reports each declaration separately and redacts row values by
default:

```bash
dbwarden check --database primary --data
```

```text
Safety Check - default
No schema changes detected.
```

`diff --data` compares declared data against the live database and surfaces
per-declaration issues:

```bash
dbwarden diff --database primary --data
```

```text
app/models:Country.Data.managed_rows: Target table does not exist
app/models:Currency.Data.managed_rows: Target table does not exist
app/models:User.Data.managed_rows: Target table does not exist
app/models:User.Data.derive.slug: Target table does not exist
app/models:User.Data.validations: Target table does not exist
```

Both commands add managed-row, derived-value, validation, transition, merge,
archive, and capture checks to the usual schema drift checks:

- managed-row keys and owned values inside scope;
- transformation violations inside domain;
- expected transition target identities and coverage of retained sources;
- merge group values and contribution provenance;
- archive and capture receipts and current postimages.

A missing or changed evidence record reports an *unverifiable* declaration rather
than assuming convergence. Preserved sources allow source-to-target comparison;
dropped sources require authenticated retained edges and matching applied journal
evidence.

## Step 5: Reserved tables and the edit rule

Reserved ownership, edge, capture, archive, merge, and journal tables are excluded
from schema drift. They are part of the database and must be backed up with the
execution journal; removing them can make rollback or convergence unverifiable.

After any declaration change, re-run `data render`, `transition plan`, and bundle
audit. Once a migration exists, edit the live declaration and generate a new
migration:

```bash
dbwarden data render --database primary --format operations
dbwarden data transition plan SplitLegacyPeople --database primary --format json
dbwarden data transition audit primary__0002_split_legacy_people --database primary
```

```text
primary__0002_split_legacy_people.data.py
assignments: DATA_SPEC, DATA_EXECUTION_HMAC
```

Do not edit frozen data files or migration SQL. The manifest verifies them, so
hand edits fail closed rather than silently diverging.

## Recap

- `UNKNOWN_REQUIRES_RECONCILIATION` blocks automatic replay after a possible
  durable effect; it is how nontransactional backends stay safe.
- Inspect with `data transition reconcile <migration_id>`, then record
  `--apply --decision verified_retry` or `abandon` only if the state permits.
- `verified_retry` requires transactional rollback evidence or matching checkpoints
  and verified postconditions.
- `check --data` and `diff --data` compare desired declarations with actual managed
  rows, derived values, validations, transitions, merges, archive, and capture.
- Reserved internal tables are excluded from drift but are part of your backup.
- Re-render, re-plan, and re-audit after declaration changes; never edit frozen files.

## What's next

Automate the review and safety gates in your pipeline:
[14. Safety and CI](./14-safety-and-ci.md).
