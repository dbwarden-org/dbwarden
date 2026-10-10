# Declarative data migrations

Runnable example projects for dbwarden's declarative data migrations. Each
directory is self-contained, targets SQLite, and can be replayed from a
clean checkout.

See [`docs/data-migrations/reference.md`](../../docs/data-migrations/reference.md)
for the full reference.

| Directory | What It Covers |
|-----------|----------------|
| `basics/` | Managed rows, CSV-backed rows, derived columns, and validation. |
| `transitions/` | Splitting a legacy table into two targets with `DataTransition`, `historical_table`, and `on_complete="drop"`. |
| `merges/` | Merging two historical ledgers with `merge_sources`, `aggregate("sum", ...)`, `winner(...)`, and batched execution. |
| `lifecycle/` | Capture rollback, archive-on-missing, batching, rollback, reapply, and convergence. |

## Running an example

```bash
cd transitions
uv add dbwarden sqlalchemy      # or: pip install -r requirements.txt
bash scripts/01-legacy-schema.sh
bash scripts/02-split-people.sh
```

The scripts call `dbwarden` from your `PATH` and read the database with the
standard-library `sqlite3` module. Override the tools with `DBWARDEN=...`
and `PYTHON=...` if they are not on your path.

## The snapshot-id problem

`make-migrations` records a schema snapshot when a migration is applied. A
`historical_table(...)` declaration must pin that exact snapshot id, but the
id does not exist until the first migration has run. These examples solve
that with a **staging** pattern:

1. Alternate model revisions live under `stage/` (for example
   `stage/legacy/models.py` and `stage/final/models.py`).
2. The first script copies the pre-migration model into `app/models.py`,
   generates and applies it, then seeds rows.
3. The next script reads the snapshot id from
   `.dbwarden/snapshots/registry.json`, substitutes it into a declaration
   template (`.py.tmpl`) with a placeholder, and writes the real
   declaration into `app/data/` before generating the data migration.

Because the snapshot id is dynamic, the transition/merge declarations are
kept as templates and materialized at run time. See each example's README
for the specific stage layout.

## Conventions

- `dbwarden.py` registers a single SQLite `primary` database and points
  `model_paths` at `app/` and `data_paths` at `app/data/`.
- Generated `migrations/`, `.dbwarden/`, and `*.db` are ignored by git and
  recreated by the scripts.
- Data operations are `WARN`/`CRITICAL`, so `migrate` is run with `--force`.
  Rolling back then replaying a data migration requires
  `migrate --reapply-data`.
