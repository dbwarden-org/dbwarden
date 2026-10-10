# Recap

You have gone from declaring data beside a model to generating, applying,
reconciling, and gating it. This page collects the deciding tables and the
command cheat-sheet, and points you back to the reference material.

## What you'll learn

- Which declaration fits which job
- The commands you will use most often
- Where to go for depth: the reference, the design spec, and the runnable examples

## Choosing an approach

Declarative data migrations are versioned migration operations with canonical
identity, ownership, rollback policy, guards, a durable journal, and frozen
source artifacts. That is more machinery than a seed file — use it when a data
change belongs to the same release contract as a schema change.

| Approach | Declares | Identity and ownership | Rollback | Use when |
|---|---|---|---|---|
| Seeds | Optional initial content, loaded ad hoc | None tracked | Re-run/truncate by hand | Initial or optional content outside the release contract |
| Managed rows | A finite set of owned rows (`rows(...)`) | Key columns; owned columns recorded separately | `restore_previous` or `irreversible` | Reference data that belongs with the schema |
| Transformations | A derived target column (`derive(...)`) | One column inside its declared domain | `clear`, `recompute`, `capture`, or `irreversible` | Deterministic backfills at release time |
| Validations | Predicates that must hold (`validate(...)`) | None (read-only) | None needed | Assert data quality during apply and `check --data` |
| Transitions | Moving historical rows into current models (`DataTransition`, `historical_table`, `into`) | Source and target identity edges | `restore_preserved_source` or `irreversible` | Splits, renames, and one-shot migrations |
| Merges | Many historical sources into one target (`merge_sources`, `aggregate`, `winner`) | Grouping keys and contribution edges | `restore_preserved_source` | Consolidating overlapping ledgers deterministically |
| Archive | Moving retired rows to a named table (`archive_table`, `acknowledge_archive`) | Archive receipt per row | Verified, receipt-bound reversal | Retiring rows without deletion |
| Capture | Recording a typed preimage before a write (`rollback="capture"`) | Ledger bound to postimage | Restores only unchanged postimages | Reversible overwrites of existing values |

Rules of thumb:

- Removing a managed-row declaration does not silently delete its rows. Declare
  the deletion (or archive) and generate its migration.
- A revision that changes merge grouping, sources, or field rules needs a new
  declaration; do not reuse the old one.
- Once a migration exists, edit the live declaration and generate a new migration.
  Never edit frozen data files or migration SQL.

## Command cheat-sheet

Generation and review:

```bash
dbwarden make-migrations "…"                         # schema + data in one migration
dbwarden make-data-migration "…"                     # data only, schema excluded
dbwarden make-migrations --offline --param NAME=VALUE
dbwarden make-migrations --split-at-severity WARN --strict-pending
dbwarden data render --format operations|text|json|mapping|sql
dbwarden data describe --format text|json|markdown
dbwarden data docs --output docs/data --diagrams mermaid
dbwarden data transition validate  <name>
dbwarden data transition plan      <name> --format sql|json
dbwarden data transition dry-run   <name> --probes
dbwarden data transition audit     <migration_id>
dbwarden data transition new       <name>   # scaffold a draft
```

Apply, rollback, and reapply:

```bash
dbwarden migrate --dry-run --data
dbwarden migrate --force
dbwarden migrate --baseline --to-version 0012
dbwarden rollback --count 2
dbwarden migrate --force --reapply-data
```

Safety and CI:

```bash
dbwarden export-models
dbwarden check --data
dbwarden diff --data
dbwarden migrate --max-severity INFO        # exits 3 on a deferral
dbwarden make-migrations --split-at-severity WARN
```

Reconcile after an uncertain effect:

```bash
dbwarden data transition reconcile <migration_id>
dbwarden data transition reconcile <migration_id> --apply --decision verified_retry|abandon
```

## Where to go next

- [Reference: Declarative data migrations](../reference.md) — the full surface of
  declarations, commands, artifacts, and recovery.
- [Design specification: Declarative data migrations](../../design/declarative-data-migrations.md)
  — the normative contract for identity, planning, journals, and rollback.
- Runnable examples under
  [`examples/data-migrations/`](../../../examples/data-migrations/):
  [basics](../../../examples/data-migrations/basics/),
  [transitions](../../../examples/data-migrations/transitions/),
  [merges](../../../examples/data-migrations/merges/), and
  [lifecycle](../../../examples/data-migrations/lifecycle/).
- New to dbwarden? Start with [Get Started](../../getting-started/first-steps.md)
  and [Your First Migration](../../getting-started/first-migration.md).
- Related correctness material:
  [safety-scoped migrations](../../correctness/safety-scoped-migrations.md) and
  [CI/CD patterns](../../advanced/ci-cd-patterns.md).

## Recap

- Pick the declaration that matches the job; the ownership and rollback columns
  above tell you what each one guarantees.
- Generation compiles to frozen artifacts; apply consumes them; convergence and
  reconciliation verify the result.
- Keep the review gates — `check --data`, `migrate --dry-run --data`, and severity
  caps — between generation and a single serialized deploy job.
