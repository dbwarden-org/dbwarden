# Declarative data migrations: transitions

Split a legacy table into two normalised tables with a `DataTransition`,
then drop the source.

```text
legacy_people(id, full_name, email)
        │  SplitLegacyPeople  (overlap="fan_out", on_complete="drop")
        ├──▶ person(id, name)
        └──▶ customer(person_id, tier)
```

The transition pins the schema snapshot that still contains `legacy_people`,
maps source columns into each target with `into(..., map=, key=,
on_conflict=)`, and derives `customer.tier` from the email domain.

## Prerequisites

- Python 3.12+
- `dbwarden` on your `PATH` (e.g. `uv add dbwarden sqlalchemy`,
  then run the scripts from an activated environment). The scripts also
  honour `DBWARDEN` and `PYTHON` overrides.

## Quick start

```bash
bash scripts/01-legacy-schema.sh   # create + seed the legacy schema
bash scripts/02-split-people.sh    # generate, apply, and verify the split
```

Run the scripts in order; `02` relies on the snapshot written by `01`.

## How staging works

The snapshot id only exists after the first migration is applied, but the
transition declaration must reference it before generation. The example
keeps alternate model stages under `stage/` and copies them into place:

| Path | Purpose |
|------|---------|
| `stage/legacy/models.py` | Pre-migration schema (`legacy_people`). |
| `stage/final/models.py` | Post-migration schema (`person`, `customer`). |
| `stage/transitions/split_people.py.tmpl` | Transition template with a `__SNAPSHOT__` placeholder. |

`01` copies the legacy model into `app/models.py` and applies it. `02`
copies the final model, reads the snapshot id from
`.dbwarden/snapshots/registry.json`, substitutes it into the template with
`sed`, and writes `app/data/split_people.py`. That file is discovered via
`data_paths=["app/data"]` and compiled into the frozen migration bundle.

`app/models.py` in the repository already holds the final models; the
scripts overwrite it as they replay history.

## Key declarations

```python
legacy = historical_table("legacy_people", snapshot="primary__0001_legacy_schema")

class SplitLegacyPeople(DataTransition):
    source = legacy
    source_identity = [legacy.id]
    overlap = "fan_out"
    on_complete = "drop"
    acknowledge_drop = True
    rollback = "irreversible"
    targets = [
        into(Person, map={Person.id: legacy.id, Person.name: legacy.full_name},
             key=[Person.id], on_conflict="ignore_if_equivalent"),
        into(Customer, map={Customer.person_id: legacy.id,
                            Customer.tier: func.lower(func.split_part(legacy.email, "@", 2))},
             key=[Customer.person_id], on_conflict="ignore_if_equivalent"),
    ]
```

## Commands demonstrated

| Script | Commands |
|--------|----------|
| `01-legacy-schema.sh` | `init`, `make-migrations`, `migrate --force` |
| `02-split-people.sh` | `make-migrations`, `migrate --force`, `check --data` |
