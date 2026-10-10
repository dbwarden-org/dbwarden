# Declarative data migrations: transitions

Move a legacy table into two normalised tables with a `DataTransition`. Two
routing strategies are shown over the same source:

```text
legacy_people(id, full_name, email)
        │  SplitLegacyPeople  (overlap="fan_out", on_complete="drop")
        ├──▶ person(id, name)
        └──▶ customer(person_id, tier)

        │  RouteLegacyPeople  (overlap="priority", on_complete="preserve")
        ├──▶ customer(person_id, tier)   example.com addresses, priority 10
        └──▶ person(id, name)            everyone else, priority 20
```

Both transitions pin the schema snapshot that still contains `legacy_people`,
map source columns into each target with `into(..., map=, key=,
on_conflict=)`, and derive `customer.tier` from the email domain. The fan-out
split sends every row to both tables and drops the source; the priority route
sends each row to exactly one table and preserves the source.

## Prerequisites

- Python 3.12+
- `dbwarden` on your `PATH` (e.g. `uv add dbwarden sqlalchemy`,
  then run the scripts from an activated environment). The scripts also
  honour `DBWARDEN` and `PYTHON` overrides.

## Quick start

```bash
bash scripts/01-legacy-schema.sh   # create + seed the legacy schema
bash scripts/02-split-people.sh    # generate, apply, and verify the fan-out split
```

`02` and `03` are alternatives. Run `01` first, then run **either**:

```bash
bash scripts/02-split-people.sh    # overlap="fan_out", on_complete="drop"
bash scripts/03-route-people.sh    # overlap="priority", on_complete="preserve"
```

Each script relies on the snapshot written by `01`.

## How staging works

The snapshot id only exists after the first migration is applied, but the
transition declaration must reference it before generation. The example
keeps alternate model stages under `stage/` and copies them into place:

| Path | Purpose |
|------|---------|
| `stage/legacy/models.py` | Pre-migration schema (`legacy_people`). |
| `stage/final/models.py` | Post-migration schema (`person`, `customer`). |
| `stage/transitions/split_people.py.tmpl` | Fan-out transition template with a `__SNAPSHOT__` placeholder. |
| `stage/transitions/route_people.py.tmpl` | Priority-routed transition template. |

`01` copies the legacy model into `app/models.py` and applies it. `02` or `03`
copies the final model, reads the snapshot id from
`.dbwarden/snapshots/registry.json`, substitutes it into the matching template
with `sed`, and writes the declaration into `app/data/`. That file is discovered
via `data_paths=["app/data"]` and compiled into the frozen migration bundle.

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

```python
class RouteLegacyPeople(DataTransition):
    source = legacy
    source_identity = [legacy.id]
    coverage = "all_assigned_once"
    overlap = "priority"
    on_complete = "preserve"
    rollback = "restore_preserved_source"
    targets = [
        into(Customer, map={Customer.person_id: legacy.id,
                            Customer.tier: func.lower(func.split_part(legacy.email, "@", 2))},
             key=[Customer.person_id], where=IS_EXAMPLE, priority=10,
             on_conflict="ignore_if_equivalent"),
        into(Person, map={Person.id: legacy.id, Person.name: legacy.full_name},
             key=[Person.id], priority=20, on_conflict="ignore_if_equivalent"),
    ]
```

## Commands demonstrated

| Script | Commands |
|--------|----------|
| `01-legacy-schema.sh` | `init`, `make-migrations`, `migrate --force` |
| `02-split-people.sh` | `make-migrations`, `migrate --force`, `check --data` |
| `03-route-people.sh` | `data transition validate`, `make-migrations`, `migrate --force`, `check --data` |
