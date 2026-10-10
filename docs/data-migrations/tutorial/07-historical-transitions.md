# 7. Historical transitions

A historical transition moves rows out of a table that no longer exists in your
current models. `DataTransition` makes that move explicit: one pinned source
snapshot, one or more current targets, and a declared policy for coverage,
overlap, completion, and rollback.

## What you'll learn

- Why a historical source must be pinned to an exact schema snapshot
- Where snapshot IDs come from and how to read them from the registry
- The staging pattern the example uses to pin a snapshot that only exists after apply
- How to declare a `DataTransition` with `source`, `source_identity`, and `into(...)` targets
- How coverage, overlap, and completion policies describe the move
- How to split one legacy table into two normalized tables and verify it with `check --data`

## Prerequisites

- Completed pages 1–6 (managed rows, static sources, expressions, validations).
- Python 3.12+, `dbwarden` on your `PATH`, and SQLAlchemy 2.0.
- The runnable [transitions example](../../../examples/data-migrations/transitions/).

## Step 1: The problem: one table becomes two

Suppose an old release kept everything about a person in one denormalized table:

```text
legacy_people(id, full_name, email)
```

The new schema normalizes that into two tables, with the person's contact tier
derived from the email domain:

```text
legacy_people(id, full_name, email)
        │  SplitLegacyPeople
        ├──▶ person(id, name)
        └──▶ customer(person_id, tier)
```

The legacy table is gone from your SQLAlchemy models, so it is *historical*: it is
not part of the schema you now declare, but its rows still need to reach the new
tables. A normal managed-row or transformation declaration cannot describe this,
because it only ever reads and writes current tables. A `DataTransition` can,
provided it knows the exact structure the source had when the rows were written.

## Step 2: Pin the source with `historical_table()`

`historical_table()` names a source and the snapshot that defines its columns:

```python
from dbwarden.data import historical_table

legacy = historical_table("legacy_people", snapshot="primary__0001_legacy_schema")
```

The `snapshot` argument is the important part. Current SQLAlchemy models describe
only the *current* schema; they cannot tell you that `legacy_people` once had a
`full_name` and an `email` column. The pinned snapshot does. Generation freezes
the concrete snapshot ID, database, backend, schema checksum, and parents into the
migration bundle, and execution never searches again or substitutes another
snapshot.

You may omit `snapshot=` while authoring to ask for the newest compatible snapshot
on one unambiguous registry lineage. Complication is easy to hit (multiple heads,
several newest candidates, a missing table, invalid lineage), and the failure mode
is a hard error asking for `snapshot=`. Pin it explicitly unless the lineage is
genuinely trivial. An explicit ID is recommended and required when compatible
lineage is ambiguous.

## Step 3: See where snapshot IDs come from

You do not invent a snapshot ID. Every applied migration registers a schema
snapshot under `.dbwarden/snapshots/registry.json`, with the schema state stored
under `.dbwarden/schemas/`. The registry read is a plain JSON load:

```bash
python3 - <<'PY'
import json

registry = json.load(open(".dbwarden/snapshots/registry.json"))
print("Snapshot id:", registry["snapshots"][-1]["snapshot_id"])
PY
```

```text
Snapshot id: primary__0001_legacy_schema
```

The ID is `<database>__<version>_<slug>` for the migration that created it. The
example uses this exact mechanism: it applies the legacy schema first, then reads
the ID back and pins it into the transition.

## Step 4: Stage the schema revisions and template the transition

There is a circularity to solve. The transition needs a snapshot ID that only
exists *after* the legacy migration is applied, but the transition declaration
must exist *before* the data migration is generated. The example breaks the cycle
with staging:

| Path | Purpose |
|------|---------|
| `stage/legacy/models.py` | Pre-migration schema (`legacy_people`). |
| `stage/final/models.py` | Post-migration schema (`person`, `customer`). |
| `stage/transitions/split_people.py.tmpl` | Transition template with a `__SNAPSHOT__` placeholder. |

Script `01` copies the legacy model into `app/models.py`, generates and applies
it, then seeds rows. Script `02` copies the final model, reads the snapshot ID from
the registry, substitutes it into the template with `sed`, and writes the real
declaration to `app/data/split_people.py`. That directory is discovered because
the project config sets `data_paths=["app/data"]`.

## Step 5: Run script 01 to create and seed the legacy schema

```bash
cd transitions
bash scripts/01-legacy-schema.sh
```

The script runs `dbwarden init`, then `make-migrations "legacy schema"`, then
`migrate --force` (schema operations are not WARN/CRITICAL here, but the flag is
harmless), and finally seeds the rows the split will move:

```text
=== 01: Legacy schema ===
Migrations completed successfully: 1 migrations applied.
Seeded 3 legacy_people rows
Snapshot id: primary__0001_legacy_schema
```

## Step 6: Declare the transition

The materialized declaration is short. The template before `sed` substitution is:

```python
from app.models import Customer, Person
from dbwarden.data import DataTransition, func, historical_table, into

legacy = historical_table("legacy_people", snapshot="__SNAPSHOT__")


class SplitLegacyPeople(DataTransition):
    source = legacy
    source_identity = [legacy.id]

    # One legacy row fans out into two tables (person + customer).
    overlap = "fan_out"

    # Drop the legacy table when the copy completes.
    on_complete = "drop"
    acknowledge_drop = True
    rollback = "irreversible"

    targets = [
        into(
            Person,
            map={Person.id: legacy.id, Person.name: legacy.full_name},
            key=[Person.id],
            on_conflict="ignore_if_equivalent",
        ),
        into(
            Customer,
            map={
                Customer.person_id: legacy.id,
                Customer.tier: func.lower(func.split_part(legacy.email, "@", 2)),
            },
            key=[Customer.person_id],
            on_conflict="ignore_if_equivalent",
        ),
    ]
```

Reading it top to bottom:

- `source` is the historical table. It is excluded from final desired-model
  discovery, so no schema is expected for it after the migration.
- `source_identity = [legacy.id]` names the stable, non-null, unique source key.
  Every ledger edge and convergence check is anchored to it.
- Each `into(...)` target names a **current** mapped model and a mapping of
  `{Target.column: source_expression}`. Target `key` columns identify stable rows.
- `on_conflict="ignore_if_equivalent"` accepts a pre-existing target row only when
  every owned value already equals the declared result. Plain ignore does not
  exist. `overwrite` is the other mutating choice, and it requires
  `acknowledge_overwrite=True`.
- `customer.tier` is an expression over the source, not a Python callback:
  `split_part(email, "@", 2)` takes the domain and `lower` normalizes it.

## Step 7: Choose coverage, overlap, completion, and rollback policies

The class attributes above are defaults overridden deliberately. These three
tables are the whole policy surface.

Coverage says how much of the source must be accounted for:

| `coverage` | Meaning |
|---|---|
| `all` (default) | Every source row must match a target. |
| `subset` | Unmatched source rows are permitted; pair with `on_unmatched="ignore"` or `"archive"`. |
| `exactly_once_match` | Each source row must match exactly one raw target predicate. |
| `all_assigned_once` | Each source row has exactly one assignment after priority resolution. |

Overlap says what happens when a source row matches more than one target:

| `overlap` | Meaning |
|---|---|
| `error` (default) | A source row matching multiple targets fails. |
| `fan_out` | One source row may populate several targets (only the listed edges). |
| `priority` | Distinct integer `priority=` per target choose the first match; requires `all_assigned_once`. |

Completion says what happens to the source after the copy:

| `on_complete` | Meaning |
|---|---|
| `keep` | Keep a mapped current-model source. Requires `source_snapshot=...` and `on_unmatched="error"`. |
| `preserve` (default) | Rename the source to its deterministic preservation table. |
| `drop` | Drop the source. Requires `acknowledge_drop=True` and `rollback="irreversible"`. |
| `archive` | Retain the completed source under `archive_to=archive_table(...)`; requires `acknowledge_archive=True`. |

Rollback says how reversal works. The example is `drop`, so rollback is
necessarily `irreversible`:

| `rollback` | Meaning |
|---|---|
| `restore_preserved_source` (default) | Restore the preserved source and remove only verified writes owned by the transition. |
| `capture` | Record overwritten target preimages so they can be restored. |
| `irreversible` | Refuse rollback. |

The example uses `fan_out` because each legacy row intentionally becomes one
`person` row *and* one `customer` row. It drops the source because the split is a
one-shot cutover. The `on_missing="delete"`-style rule for managed rows does not
apply here; a transition owns its source retirement only through `on_complete`.

## Step 8: Materialize the declaration and generate one migration

```bash
bash scripts/02-split-people.sh
```

The script substitutes the snapshot and generates a single unified schema + data
migration. Because the transition owns the `legacy_people` drop, DBWarden does not
emit a duplicate schema `drop_table` operation:

```text
=== 02: Split legacy_people ===
Pinning snapshot: primary__0001_legacy_schema
Generated: primary__0002_split_legacy_people.sql (3 ops, max severity CRITICAL)
```

The severity is `CRITICAL` because dropping a source is destructive.

## Step 9: Apply and verify

The script applies, checks convergence, and inspects the result:

```bash
dbwarden make-migrations "split legacy people"
dbwarden migrate --force
dbwarden check --data
```

```text
Migrations completed successfully: 1 migrations applied.
person:   [(1, 'Ada Lovelace'), (2, 'Alan Turing'), (3, 'Grace Hopper')]
customer: [(1, 'example.com'), (2, 'math.org'), (3, 'navy.mil')]
legacy_people dropped: True
```

`check --data` prints nothing when the schema, pending migration SQL, and declared
data converge. Before applying, `dbwarden data transition validate SplitLegacyPeople`
reports `Data declarations are valid.`, and
`dbwarden data transition describe SplitLegacyPeople` shows the resolved source,
identity, coverage, and per-target conflicts without running anything.

## Recap

- A historical source is pinned to one exact snapshot; execution never rediscovers it.
- Snapshot IDs are registered by applied migrations under `.dbwarden/snapshots/registry.json`.
- The example stages model revisions and a `__SNAPSHOT__` template because the ID only exists after apply.
- `DataTransition` declares `source`, `source_identity`, and `into(...)` targets; coverage, overlap, completion, and rollback state the move.
- One runnable `make-migrations` can carry both the target schema and the row move.

## What's next

Send one source row to different targets with overlap and priorities:
[8. Splits and priority](./08-splits-and-priority.md).
