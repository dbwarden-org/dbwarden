# 1. Why and the mental model

Declarative data migrations describe the rows and transformations your data should
have, next to the models that define the schema, and compile them into reviewed,
versioned migration artifacts.

## What you'll learn

- Why data changes deserve the same versioned, reviewed treatment as schema changes
- When to reach for a declarative data migration instead of a seed or one-off SQL
- The path from a declaration to frozen artifacts to execution
- Why apply never re-imports your current declarations
- What "ownership" means, and where the trust boundary sits

## Prerequisites

- Familiarity with SQLAlchemy models and the dbwarden migration basics, such as
  [Your First Migration](../../getting-started/first-migration.md).
- Python 3.12+, `dbwarden`, and SQLAlchemy installed.

## The problem with data changes by hand

Schema changes have a workflow: you declare the model, generate SQL, review it,
commit it, and apply it under a lock. Data changes usually do not. They arrive as a
backfill script, a `INSERT ... ON CONFLICT` block in a migration file, or a seed
file somebody reruns by hand. That leaves four gaps:

- **No identity.** Nothing records which rows the change owns, so a later run
  cannot tell "our row, safe to update" from "an application row, hands off."
- **No rollback.** A forward-only backfill has no reverse, or a reverse that
  silently overwrites later application edits.
- **No review contract.** The values that will be written live in code, not in a
  checksummed artifact an operator can approve.
- **No convergence check.** After deploy, nothing proves the data actually matches
  the declaration.

Declarative data migrations close all four gaps by giving data the same
generation, review, ownership, rollback, and convergence rules as schema.

## Seeds, one-off SQL, or declarations?

| Approach | Best for | Not for |
|---|---|---|
| Seed file | Optional initial content, loaded ad hoc | Data that belongs with a specific schema release |
| One-off SQL / manual migration | Repairs, hotfixes, custom `USING` expressions | Repeatable, reviewed reference data |
| Declarative data migration | Data that ships with a schema change and must be owned, reviewed, and reversible | Arbitrary procedural logic, network calls, or per-row Python |

The deciding question is whether the data change belongs to the release contract.
If yes, declare it. If it is genuinely one-off, keep it separate.

## The mental model

A declaration compiles once. Execution consumes only the compiled result.

```text
app/models.py                     dbwarden (compile)                migrations/primary/
┌───────────────────┐            ┌───────────────────────┐         ┌────────────────────────────┐
│ SQLAlchemy model  │            │ discovery → validation │         │ 0001_seed.sql    (review)  │
│   class Data(...)  │──compile──▶│ → resolution          │─publish▶│ 0001_seed.plan.json (contract)│
│ managed_rows      │            │ → canonical spec      │         │ 0001_seed.data.py (frozen) │
│ transformations   │            │   (typed, checksummed)│         └──────────────┬─────────────┘
│ validations       │            └───────────────────────┘                        │ migrate (under lock)
└───────────────────┘                                                             ▼
                                                                       ┌────────────────────┐
                                                                       │ live database      │
                                                                       └────────────────────┘
```

Read it left to right:

1. **Declaration.** You declare intent beside the model: which rows to own, which
   column to derive, which predicate must hold. The declaration is Python, but it
   is not arbitrary code — it is a small typed surface.
2. **Compilation.** dbwarden discovers declarations, validates them, resolves
   static sources and snapshots, and produces a *canonical data specification*: a
   backend-independent, checksummed description of the work.
3. **Frozen artifacts.** Generation writes three paired files (`.sql`,
   `.plan.json`, `.data.py`) bound by a manifest of names, lengths, and checksums.
4. **Execution.** `migrate` verifies the bundle, acquires the migration lock, and
   runs the frozen SQL. It does not import your current declarations.

## Why the artifacts are frozen

Apply never re-imports `app/models.py`. The declarations you review are compiled
once into the bundle; the bundle is what runs. This matters because:

- Review means something: the SQL and plan you approved are the exact statements
  that execute.
- Live code can keep changing without changing what a pending migration does.
- A checksum mismatch fails closed: a hand-edited SQL file or a stale plan is
  rejected rather than silently applied.

If you change a declaration after generating, you generate a *new* migration
rather than editing the old one. Frozen files are never edited by hand.

## Ownership: declared columns vs application columns

Every mutating declaration names the columns it owns. For managed rows, that is
`owned_columns`; for a derivation, it is the single target column. Everything else
belongs to the application:

- Only owned columns are written. A column outside `owned_columns` is never
  changed, even if it is part of the same row.
- Rolling back checks the current owned value first. If application code edited it
  after apply, rollback stops instead of overwriting the newer value.
- Removing a declaration does not delete its rows. Deletion is its own reviewed
  change with an explicit scope and acknowledgement.

Ownership is what makes a data migration safe to apply more than once in the
middle of a live system.

## The trust boundary

Live declarations are trusted project Python. dbwarden imports them to discover
them, so import side effects run before validation. The compiler pipeline is a
correctness and mutation-safety boundary — it rejects unsupported expressions,
ambiguous ownership, and unsafe operations — but it is not a sandbox. Code review,
process, credentials, and database permissions still bound what a declaration can
do.

## A first look at the file set

One data migration writes three files:

```text
primary__0001_seed_countries_currencies_and_slugs.sql         # reviewable upgrade + rollback SQL
primary__0001_seed_countries_currencies_and_slugs.plan.json   # typed operations, data spec, manifest
primary__0001_seed_countries_currencies_and_slugs.data.py     # frozen canonical declaration data
```

- The `.sql` file is what an operator reads and what executes.
- The `.plan.json` file is the machine-readable contract and the integrity manifest.
- The `.data.py` file is human-readable audit data and is never an execution input.

## Recap

- Hand-written data changes lack identity, rollback, a review contract, and a
  convergence check; declarative data migrations supply all four.
- Use a declaration when the data change belongs to the same release contract as
  the schema; keep genuine one-offs separate.
- Declarations compile to a canonical spec and then to frozen `.sql`,
  `.plan.json`, and `.data.py` artifacts.
- Apply consumes frozen artifacts and never imports current declarations.
- Declarations own specific columns; the application owns everything else, and
  rollback refuses to overwrite later edits.

## What's next

Put the model into practice and stand up the project:
[2. Setup and your first declaration](./02-setup-and-first-declaration.md).
