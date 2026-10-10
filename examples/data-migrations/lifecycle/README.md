# Declarative data migrations: lifecycle

Exercise capture rollback, archive on missing rows, batching, and the
rollback / reapply / convergence flow.

```text
01 base schema   item(id, source, value), country(code, name)
02 capture       item.value  <- derive(source + 1, rollback="capture", batch)
                 country     <- managed_rows [UY, AR]
03 archive       country     <- managed_rows [UY], on_missing="archive"
04 lifecycle     rollback --count 2  ->  migrate --reapply-data  ->  check --data
```

## Prerequisites

- Python 3.12+
- `dbwarden` on your `PATH` (e.g. `uv add dbwarden sqlalchemy`). The
  scripts also honour `DBWARDEN` and `PYTHON` overrides.

## Quick start

```bash
bash scripts/01-base-schema.sh
bash scripts/02-capture-and-seed.sh
bash scripts/03-archive.sh
bash scripts/04-rollback-reapply.sh
```

Run the scripts in order; each builds on the state left by the previous one.

## How staging works

The example keeps alternate model stages under `stage/`; each script copies
the next one over `app/models.py` and generates a migration:

| Path | Purpose |
|------|---------|
| `stage/base/models.py` | Plain `item` and `country` tables. |
| `stage/capture/models.py` | Adds the capture transformation and managed rows. |
| `stage/archive/models.py` | Switches the managed row to `on_missing="archive"`. |

`app/models.py` in the repository already holds the final (archive) models;
the scripts overwrite it as they replay history. Declarations live on the
models, so `app/data/` only holds its package marker.

## What each stage demonstrates

**Capture rollback**: `derive("value", col("source") + 1, rollback="capture")`
records the typed preimage before the write. `rollback --count 2` restores
`value` to its original `source` value.

**Batching**: `execution=batch(size=1, key=["id"])` walks the rows in
ascending keyset chunks. Three seeded rows run as three chunks in one
statement transaction.

**Archive**: after revision `02` owns `UY` and `AR`, revision `03` drops
`AR` from the declaration. `on_missing="archive"` with
`archive_to=archive_table("country_archive")` and `acknowledge_archive=True`
moves the retired row instead of dropping it. Rolling back `03` restores it.

## Key declarations

```python
class Item(Base):
    class Data(DataMeta):
        transformations = [
            derive("value", col("source") + 1, rollback="capture",
                   execution=batch(size=1, key=["id"])),
        ]

class Country(Base):
    class Data(DataMeta):
        managed_rows = rows(
            key="code",
            rows=[{"code": "UY", "name": "Uruguay"}],
            owned_columns=["name"],
            on_missing="archive",
            scope=col("code") != literal(""),
            archive_to=archive_table("country_archive"),
            acknowledge_archive=True,
            rollback="restore_previous",
        )
```

## Commands demonstrated

| Script | Commands |
|--------|----------|
| `01-base-schema.sh` | `init`, `make-migrations`, `migrate --force` |
| `02-capture-and-seed.sh` | `make-migrations`, `migrate --force`, `check --data` |
| `03-archive.sh` | `make-migrations`, `migrate --force`, `check --data` |
| `04-rollback-reapply.sh` | `rollback --count 2`, `migrate --force --reapply-data`, `check --data` |

`--force` acknowledges the WARN/CRITICAL data operations. `--reapply-data`
is required because ordinary migrate never silently replays a rolled-back
data migration.
