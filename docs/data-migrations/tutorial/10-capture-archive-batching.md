# 10. Capture, archive, and batching

Three lifecycle concerns show up once data migrations own real rows: undoing an
overwrite with the exact old value, retiring rows without deleting them, and
keeping a long write from holding the database forever. Capture, archive, and
batch each handle one of them.

## What you'll learn

- How `rollback="capture"` records a typed preimage and why rollback checks the postimage
- How `archive_table(...)` moves missing rows and completed sources without deleting them
- When archive requires `acknowledge_archive=True`, and how transitions archive unmatched rows
- How `batch(...)` splits a statement into ascending keyset chunks
- What `max_duration`, `statement_timeout`, `lock_timeout`, and `max_replication_lag` mean, and which backends honor them
- How to roll back and reapply a data migration that used all three

## Prerequisites

- Completed [9. Merges](./09-merges.md).
- The runnable [lifecycle example](../../../examples/data-migrations/lifecycle/).

## Step 1: Capture a typed preimage

Some backfills overwrite values that already existed. `rollback="clear"` can zero a
nullable column, and `rollback="recompute"` can restore a previous frozen
expression, but neither recovers the *actual* value an application wrote. Capture
does:

```python
class Data(DataMeta):
    transformations = [
        derive("value", col("source") + 1, rollback="capture",
               execution=batch(size=1, key=["id"])),
    ]
```

Capture requires a target primary key. Apply stores the preimage in the same
consistency boundary as the write, so it describes the row as it was immediately
before this migration touched it. The store distinguishes a missing row, a SQL
null, and a typed value.

Rollback then checks that the current value still matches the *generated
postimage* before restoring. If application code changed it after apply, rollback
stops for reconciliation rather than clobbering the later value.

`check --data` verifies the authenticated capture receipt and the recorded
postimage. It does not reevaluate the expression or its domain against the
post-write row: a legitimate one-shot expression such as
`derive("value", col("value") + 1, rollback="capture")` would be misclassified
otherwise.

## Step 2: Know what capture does not promise

| `rollback` | What it restores | Requirement |
|---|---|---|
| `clear` | Sets the owned column back to null | Target column must be nullable |
| `recompute` | Restores the previous frozen desired expression | A prior declaration with compatible ownership |
| `capture` | Restores the authenticated typed preimage | Target has a primary key |
| `irreversible` | Nothing; rollback refuses | — |

Generic inverse reconstruction is unsupported. If you cannot name how to reverse a
write, declare `irreversible` and mean it.

## Step 3: Archive retired rows instead of deleting them

A managed-row revision can drop a row from its declaration and archive it rather
than leaving it or deleting it:

```python
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

`on_missing="archive"` requires all three of `scope=`, `archive_to=archive_table(...)`,
and `acknowledge_archive=True`. The migration writes or verifies the archive row
before it removes the source row, so an interrupted move never loses data.

Transitions archive in two distinct places, both requiring
`acknowledge_archive=True`:

- unmatched rows under `coverage="subset"`:
  `on_unmatched="archive"` plus `unmatched_archive_to=archive_table(...)`;
- a completed source: `on_complete="archive"` plus `archive_to=archive_table(...)`.

An archive destination cannot reuse an internal name, a desired model, the source
or target itself, or another declaration's archive destination.

## Step 4: Bound long writes with `batch()`

```python
batch(size=1, key=["id"])
```

```text
batch(*, size, key, max_duration=None, statement_timeout=None, lock_timeout=None, max_replication_lag=None)
```

`batch()` splits a generated data statement into ordered chunks:

- **Ascending keyset ranges, never `OFFSET`.** Each chunk reads the next `size`
  rows whose key is greater than the previous chunk's high key.
- **The key is an identity.** For a transition it must be the source identity; for
  a transformation it must be the target primary key. It must be non-null, unique,
  and not modified by the statement.
- **One statement transaction, one ownership receipt.** All chunks belong to the
  statement's existing transaction and ownership record. They do not commit or
  resume independently; a failure rolls back the whole statement, and global
  coverage, postconditions, edge recording, and source retirement run only after
  every chunk succeeds.

The limits are all finite positive seconds:

| Control | Meaning |
|---|---|
| `max_duration` | One cooperative budget across every step and chunk in the declaration attempt. Checked before and after SQL and between chunks. A retry starts a new budget. |
| `statement_timeout` | Interrupts a supported statement after the given time. |
| `lock_timeout` | Bounds how long a statement waits for a lock. |
| `max_replication_lag` | Refuses to continue when observed replication lag exceeds the limit. |

Backend support differs, and unsupported controls fail during planning:

| Backend | Batch limits |
|---|---|
| PostgreSQL | Statement, lock, duration, and replication-lag limits. |
| MariaDB | Its native statement limit; the other admitted controls. |
| MySQL | Rejects a requested DML `statement_timeout` (`MySQL cannot enforce statement_timeout for batched DML; use max_duration`); supports the others. |
| SQLite | Duration, statement interruption, and lock timeout; rejects replication-lag limits (`SQLite has no replication-lag measurement`). |
| ClickHouse | Rejects keyset batching. |

## Step 5: Run scripts 01 and 02 — base schema, capture, seed

```bash
cd lifecycle
bash scripts/01-base-schema.sh
bash scripts/02-capture-and-seed.sh
```

Script `01` creates `item(id, source, value)` and `country(code, name)` and seeds
three items. Script `02` adopts the capture revision — `value` becomes
`col("source") + 1` with capture rollback and `batch(size=1, key=["id"])` — and
declares `UY` and `AR` as managed rows:

```text
=== 02: Capture values and seed countries ===
Generated: primary__0002_capture_values_and_seed_countries.sql (2 ops, max severity WARN)
Migrations completed successfully: 1 migrations applied.
item (value = source + 1): [(1, 7, 8), (2, 10, 11), (3, 20, 21)]
country:                  [('AR', 'Argentina'), ('UY', 'Uruguay')]
```

Three item rows run as three chunks — one per row — inside one statement
transaction, and each overwritten `value` gets a captured preimage.

## Step 6: Run script 03 — archive a retired row

Script `03` drops `AR` from the declaration. With `on_missing="archive"`, the
migration moves `AR` to `country_archive` instead of deleting it or leaving it in
place:

```text
=== 03: Archive the retired country ===
Generated: primary__0003_archive_retired_country.sql (1 ops, max severity CRITICAL)
Migrations completed successfully: 1 migrations applied.
country:         [('UY', 'Uruguay')]
country_archive: [('AR', 'Argentina')]
```

## Step 7: Run script 04 — roll back, reapply, converge

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

Rolling back the archive migration restores `AR` from `country_archive`; rolling
back the capture migration restores the preimages, so `value` returns to the
original `source` values (`3`, `4`, `5`) and the seeded countries are removed with
the revision that created them.

Ordinary `migrate` never silently replays a rolled-back data migration, so
reapplying needs `--reapply-data` (which starts a new linked epoch), and `--force`
is still required because the data operations are `WARN`/`CRITICAL`:

```bash
dbwarden rollback --count 2
dbwarden migrate --force --reapply-data
dbwarden check --data
```

## Recap

- `rollback="capture"` records a typed preimage and restores it only while the current value still matches the recorded postimage.
- `archive_table(...)` with `on_missing="archive"` and `acknowledge_archive=True` moves rows instead of deleting them; transitions archive unmatched rows or a completed source.
- `batch(...)` walks ascending keyset ranges inside one statement transaction and one ownership receipt, bounded by `max_duration`, `statement_timeout`, `lock_timeout`, and `max_replication_lag`.
- Backend support for those limits differs; unsupported combinations fail at planning.
- Rollback restores; `migrate --reapply-data` (with `--force`) explicitly replays.

## What's next

Review, validate, and generate the frozen artifacts for the declarations you have
written: [11. Review and generate](./11-review-and-generate.md).
