---
description: Migrate from Alembic to dbwarden step by step. Map Alembic concepts to their
  dbwarden equivalents, baseline an existing database, verify convergence, and retire your
  revision scripts without losing history.
---

# Migrating from Alembic

This guide is for teams with an existing Alembic setup who want to switch to dbwarden. The migration is low-risk by design: your SQLAlchemy models stay exactly where they are, your database is never rebuilt, and your Alembic history remains in git. You are replacing the migration workflow, not the schema.

If you are evaluating rather than migrating, read [Why dbwarden](../index.md#why-dbwarden) first. The short version: Alembic maintains schema truth in a chain of revision scripts, dbwarden maintains it in your models and derives plain SQL migrations from them.

## Concept mapping

Every Alembic concept has a dbwarden counterpart. This table is the mental model for the whole guide.

| Alembic | dbwarden |
|---|---|
| `alembic.ini` + `env.py` | `dbwarden.py` config file |
| Revision script (`.py`) | Migration file (`.sql`) with `-- upgrade` / `-- rollback` sections |
| Revision chain as source of truth | SQLAlchemy models as source of truth |
| `alembic revision --autogenerate` | `dbwarden make-migrations "description"` |
| `alembic upgrade head` | `dbwarden migrate` |
| `alembic downgrade -1` | `dbwarden rollback` |
| `alembic stamp <rev>` | `dbwarden migrate --baseline` |
| `alembic current` | `dbwarden status` |
| `alembic history` | `dbwarden history` |
| `alembic upgrade head --sql` (offline mode) | `dbwarden make-migrations --sql`, or full offline generation via `export-models` + `--offline` |
| `alembic_version` table | dbwarden migration table |
| Hand-written revision | `dbwarden new "description"` (manual SQL migration) |

Two Alembic features have no direct equivalent, and you should know that before starting:

- **Python data migrations.** Alembic revisions can run arbitrary Python. dbwarden manual migrations (`dbwarden new`) are SQL. Most backfills express well in SQL; if yours do not, plan for how you will run them (application scripts, one-off jobs) before switching.
- **Revision branching and merging.** dbwarden uses a linear versioned sequence per database. If your team relies on `alembic merge` workflows, the linear model is a real change to your process.

## Prerequisites

- Python 3.12+ and SQLAlchemy 2.0+
- A database whose schema currently matches your models (run your pending Alembic migrations first, and resolve any drift)
- Your SQLAlchemy models importable from the project

## Step 1: Install and configure

```bash
uv add dbwarden
```

Create `dbwarden.py` in your project root. This replaces `alembic.ini` and `env.py`:

```python
from dbwarden import DbwardenDatabase

class Primary(DbwardenDatabase):
    database_name = "primary"
    default = True
    database_type = "postgresql"
    database_url_sync = "postgresql://user:pass@localhost:5432/myapp"
    database_url_async = "postgresql+asyncpg://user:pass@localhost:5432/myapp"
```

The existing `database_config(...)` function API is also supported if your
project or a plugin already uses that form.

Model discovery is automatic. If you want explicit control over which modules are scanned, pass `model_paths`. See [Setup](setup.md) and [Model Discovery](../configuration/model-discovery.md).

Then initialize:

```bash
dbwarden init
```

## Step 2: Generate the baseline migration

Your first `make-migrations` run has no prior dbwarden state, so it generates the full schema from your models:

```bash
dbwarden make-migrations "baseline from alembic"
```

Review the generated `.sql` file. It should describe the schema you already have. If something looks wrong here, your models and your database disagree, and it is far better to learn that now than later. Fix the models (or the database) and regenerate before continuing.

## Step 3: Baseline the existing database

Your database already has the schema, so the baseline migration must be recorded as applied without executing:

```bash
dbwarden migrate --baseline
```

This marks the migration as applied in dbwarden's migration table. Nothing runs against the database. This is the equivalent of `alembic stamp head` for a fresh setup.

## Step 4: Verify convergence

```bash
dbwarden status
dbwarden check
dbwarden diff
```

`status` should show no pending migrations. `diff` compares your models against the live database and should report no differences. If `diff` is clean, dbwarden and reality agree, and the migration is effectively done.

For extra confidence, make a trivial model change (add a nullable column), run `dbwarden make-migrations`, inspect the generated SQL and its rollback, then revert the change and delete the generated file. That exercise shows you the full loop before you rely on it.

## Step 5 (optional): Set up offline generation for CI

If your CI previously needed a database service for `alembic revision --autogenerate` checks, you can drop it entirely:

```bash
dbwarden export-models --database primary
git add .dbwarden/model_state.primary.json
```

The state file is named after the database, so a database called `primary` produces `.dbwarden/model_state.primary.json`.

From then on, any machine can generate migrations without a database connection:

```bash
dbwarden make-migrations "description" --offline
```

See [Cookbook: Offline & CI](../cookbook/04-offline-ci.md). Do not delete the model state file; it is the offline source of truth. If it is ever lost, restore it from git or regenerate with `export-models`.

## Step 6: Retire Alembic

Once you trust the new workflow:

- Remove `alembic.ini`, `env.py`, and the `versions/` directory from the active workflow. They stay in git history, which is where migration archaeology belongs.
- Remove `alembic` from your dependencies and any `alembic upgrade head` calls from deploy scripts, replacing them with `dbwarden migrate`.
- The `alembic_version` table in your database is inert. Drop it when convenient, or leave it; dbwarden does not touch it.

If you prefer a transition period, both tools can coexist: they track state in separate tables. Generate the same change with both and compare the SQL until you are confident. Keep the overlap short, since two sources of truth defeats the point.

## Workflow changes to communicate to your team

- **Renames are explicit.** Where autogenerate guessed (usually as drop-and-create), dbwarden requires `--rename table.old:new` or `--rename-table old:new`. A rename never becomes silent data loss, but your team must know the flags exist.
- **Rollback is a contract.** Generated migrations include an executable `-- rollback` section, and placeholder rollbacks are refused. Irreversible changes must be declared with `-- dbwarden: irreversible`. See [Rollback Generation](../correctness/rollback-generation.md).
- **Table metadata moves into `class Meta`.** Comments, advanced indexes, and backend-specific options that previously lived in revision scripts or raw SQL are declared on the model. See [Modeling](modeling.md).
- **Destructive changes can be checked first.** `dbwarden check-impact` reports which code still references a column or table you are about to drop. Make it part of review for destructive migrations.
