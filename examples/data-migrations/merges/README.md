# Declarative data migrations: merges

Consolidate two historical ledgers into one account table with a
deterministic many-to-one merge.

```text
legacy_a(id, account, amount, name) ─┐
                                     ├─▶ account(id, total, name)
legacy_b(id, account, amount, name) ─┘
```

Rows are grouped by `account`; `total` is the `aggregate("sum", ...)` of the
contributions and `name` is the `winner(...)` of the highest-priority source.

## Prerequisites

- Python 3.12+
- `dbwarden` on your `PATH` (e.g. `uv add dbwarden sqlalchemy`). The
  scripts also honour `DBWARDEN` and `PYTHON` overrides.

## Quick start

```bash
bash scripts/01-legacy-schema.sh   # create + seed both ledgers
bash scripts/02-consolidate.sh     # generate, apply, and verify the merge
```

Run the scripts in order; `02` relies on the snapshot written by `01`.

## How staging works

The snapshot id only exists after the first migration is applied, but the
merge declaration must reference it before generation. The example keeps
alternate model stages under `stage/` and copies them into place:

| Path | Purpose |
|------|---------|
| `stage/legacy/models.py` | Pre-migration schema (`legacy_a`, `legacy_b`). |
| `stage/final/models.py` | Post-migration schema (`account`). |
| `stage/transitions/consolidate.py.tmpl` | Merge template with a `__SNAPSHOT__` placeholder. |

`01` copies the legacy models into `app/models.py` and applies them. `02`
copies the final model, reads the snapshot id from
`.dbwarden/snapshots/registry.json`, substitutes it into the template with
`sed`, and writes `app/data/consolidate.py`. That file is discovered via
`data_paths=["app/data"]` and compiled into the frozen migration bundle.

`app/models.py` in the repository already holds the final model; the
scripts overwrite it as they replay history.

## Key declarations

```python
left = historical_table("legacy_a", snapshot="primary__0001_legacy_merge_sources")
right = historical_table("legacy_b", snapshot="primary__0001_legacy_merge_sources")

source = merge_sources(
    from_source(left, identity=["id"],
                map={"id": col("account"), "amount": col("amount"), "name": col("name")},
                priority=0),
    from_source(right, identity=["id"],
                map={"id": col("account"), "amount": col("amount"), "name": col("name")},
                priority=1),
    key=["id"],
    values={"total": aggregate("sum", col("amount")), "name": winner(col("name"))},
    allow_empty=False,
)

class ConsolidateAccounts(DataTransition):
    source = source
    targets = [into(Account, map={Account.id: col("id"), Account.total: col("total"),
                                  Account.name: col("name")}, key=[Account.id])]
    execution = batch(size=2, key=["id"])
    on_complete = "preserve"
    rollback = "restore_preserved_source"
```

Because `on_complete="preserve"`, rollback renames the preserved sources
back and removes only the rows the merge owns.

## Commands demonstrated

| Script | Commands |
|--------|----------|
| `01-legacy-schema.sh` | `init`, `make-migrations`, `migrate --force` |
| `02-consolidate.sh` | `make-migrations`, `migrate --force`, `check --data` |
