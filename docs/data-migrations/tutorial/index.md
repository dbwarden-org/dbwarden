# Declarative data migrations: tutorial

This tutorial takes you from no data migrations to a small SQLite application that
seeds reference data, derives a column, validates it, and later moves legacy rows,
all declared beside your SQLAlchemy models.

Think of a declarative data migration as a *reviewed change to data that ships with
a schema release*. It is versioned, owned, reversible where declared, and frozen
before it runs. That is a different contract from a seed file or a one-off
backfill script.

The tutorial runs entirely on SQLite. PostgreSQL, MySQL, MariaDB, and ClickHouse
are covered at the contract level in the [reference](../reference.md), and their
execution evidence comes from the harness integration suite rather than from this
tutorial.

## What you'll learn

- Why data changes deserve the same versioned, reviewed treatment as schema changes
- How declarations compile to frozen `.sql`, `.plan.json`, and `.data.py` artifacts
- How to declare managed rows inline and from static CSV or JSON sources
- How to derive a column with a typed expression and a rollback policy
- How to validate data during apply and with `check --data`

## Who this is for

You already use SQLAlchemy and the dbwarden migration basics (models, `dbwarden
init`, `make-migrations`, `migrate`). You do not need any prior exposure to data
migrations. If SQLAlchemy models and dbwarden are new to you, start with
[Your First Migration](../../getting-started/first-migration.md) first.

## What you'll build

One project with three tables:

```text
app/models.py
  Country.Data.managed_rows   ──▶ countries   (rows written inline in Python)
  Currency.Data.managed_rows  ──▶ currencies  (rows loaded from data/currencies.csv)
  User.Data.transformations   ──▶ users.slug  (lower(trim(name)))
  User.Data.validations       ──▶ users.email (must not be null)
```

The runnable version of this project lives at
[`examples/data-migrations/basics/`](../../../examples/data-migrations/basics/).
Later pages in the series extend it toward historical transitions and operations.

## Step 1: Get the example onto your disk

Copy the basics example into a scratch directory. dbwarden's config discovery does
not pick up configuration files that live inside the repository's `examples/` tree,
so run the CLI from the copy:

```bash
cp -r examples/data-migrations/basics ~/dbwarden-data-tutorial
cd ~/dbwarden-data-tutorial
```

Alternatively, start from nothing and add `dbwarden` and SQLAlchemy yourself:

```bash
uv add dbwarden sqlalchemy
```

The example ships runnable scripts that wrap the commands shown in this tutorial:

```text
scripts/01-generate-and-apply.sh   # init, generate, apply, verify
scripts/02-inspect.sh              # render, describe, diff the data
```

## Step 2: Run the example

From the scratch directory:

```bash
bash scripts/01-generate-and-apply.sh
```

```text
=== 01: Generate and apply the data migration ===
Generated: primary__0001_seed_countries_currencies_and_slugs.sql (8 ops, max severity WARN)
Migrations completed successfully: 1 migrations applied.
countries:  [('AR', 'Argentina'), ('BR', 'Brazil'), ('UY', 'Uruguay')]
currencies: [('EUR', 'Euro'), ('USD', 'US Dollar'), ('UYU', 'Uruguayan Peso')]
users:      [(1, '  Ada Lovelace  ', 'ada lovelace')]
```

## Chapters

Each chapter is self-contained and builds on the previous one.

- [1. Why and the mental model](./01-why-and-mental-model.md) — the problem, the
  declaration-to-artifact pipeline, and the vocabulary.
- [2. Setup and your first declaration](./02-setup-and-first-declaration.md) —
  project layout, `dbwarden init`, a `Country` seed, and the severity gate.
- [3. Managed rows](./03-managed-rows.md) — the full `rows()` API: keys, ownership,
  `on_missing`, scope, and rollback.
- [4. Static sources](./04-static-sources.md) — loading rows from CSV or JSON at
  compile time.
- [5. Expressions](./05-expressions.md) — `derive()` and the typed expression AST.
- [6. Validation](./06-validation.md) — `validate()` and `check --data`.
- [7. Historical transitions](./07-historical-transitions.md) — moving pinned
  snapshot rows into current models.
- [8. Splits and priority](./08-splits-and-priority.md) — one source into many
  targets, with overlap and priority rules.
- [9. Merges](./09-merges.md) — many sources into one target with explicit
  grouping and field rules.
- [10. Capture, archive, and batching](./10-capture-archive-batching.md) —
  typed preimages, archive destinations, and ordered batches.
- [11. Review and generate](./11-review-and-generate.md) — inspecting compiled
  semantics and freezing artifacts.
- [12. Apply, roll back, and reapply](./12-apply-rollback-reapply.md) — the
  execution lifecycle and the journal.
- [13. Reconcile and convergence](./13-reconcile-and-convergence.md) — recovering
  from uncertain effects and proving convergence.
- [14. Safety and CI](./14-safety-and-ci.md) — severity gates and an offline
  pipeline.

## Recap

- Declarative data migrations are versioned, reviewed data changes that compile to
  frozen artifacts and run under the migration lock.
- The tutorial builds one small SQLite app, one declaration at a time.
- Run the commands from a copy of the example, not from inside the repository.
- The [reference](../reference.md) is the full surface; the
  [design specification](../../design/declarative-data-migrations.md) is the
  normative contract.

## What's next

Start with the reasoning before the syntax:
[1. Why and the mental model](./01-why-and-mental-model.md).
