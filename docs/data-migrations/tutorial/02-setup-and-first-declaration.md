# 2. Setup and your first declaration

This page creates the project, configures dbwarden, declares a `Country` seed, and
runs the full generate-apply-verify loop.

## What you'll learn

- The project layout a data migration expects
- How `dbwarden.py` points at models, data declarations, snapshots, and the registry
- How to declare managed rows with `rows(...)`
- The difference between `make-migrations` and `make-data-migration`
- Why data operations are gated by severity and when `--force` is required

## Prerequisites

- [Page 1](./01-why-and-mental-model.md) read, or equivalent vocabulary.
- Python 3.12+, `dbwarden`, and SQLAlchemy installed.

## Step 1: Lay out the project

Create an empty project with a models package and a data-declaration directory:

```text
myapp/
├── dbwarden.py          # marks the project root and registers the database
├── app/
│   ├── __init__.py
│   ├── models.py        # SQLAlchemy models + Data declarations
│   └── data/            # modules with DataTransition declarations (later pages)
│       └── __init__.py
└── data/
    └── currencies.csv   # used in page 4
```

`data_paths` must point at a directory that exists. An empty package with an
`__init__.py` is enough for now.

## Step 2: Configure `dbwarden.py`

The file named `dbwarden.py` marks the project root. This example keeps everything
zero-dependency by using SQLite:

```python
from dbwarden import DbwardenDatabase


class Primary(DbwardenDatabase):
    database_name = "primary"
    default = True

    database_type = "sqlite"
    database_url_sync = "sqlite:///./app.db"

    # Python files or directories scanned for SQLAlchemy models. A model may
    # declare managed rows, transformations, and validations with an inner
    # `Data(DataMeta)` class, so those declarations are discovered here.
    model_paths = ["app"]

    # Optional: modules with DataTransition declarations.
    data_paths = ["app/data"]

    # Compiled data snapshots and the historical schema registry.
    data_snapshot_dir = ".dbwarden/data"
    snapshot_registry = ".dbwarden/snapshots/registry.json"
```

The same options exist on the function API:

```python
from dbwarden import database_config

database_config(
    database_name="primary",
    default=True,
    database_type="sqlite",
    database_url_sync="sqlite:///./app.db",
    model_paths=["app"],
    data_paths=["app/data"],
    data_snapshot_dir=".dbwarden/data",
    snapshot_registry=".dbwarden/snapshots/registry.json",
)
```

`model_paths` and `data_paths` name Python files or directories, not dotted module
names. Missing configured paths fail discovery, so create `app/data/` before you
run any command.

## Step 3: Declare your first managed rows

Create `app/models.py`:

```python
from sqlalchemy import Column, String
from sqlalchemy.orm import declarative_base

from dbwarden.data import DataMeta, rows

Base = declarative_base()


class Country(Base):
    __tablename__ = "countries"

    code = Column(String(2), primary_key=True)
    name = Column(String(100), nullable=False)

    class Data(DataMeta):
        managed_rows = rows(
            key="code",
            rows=[
                {"code": "UY", "name": "Uruguay"},
                {"code": "AR", "name": "Argentina"},
                {"code": "BR", "name": "Brazil"},
            ],
            owned_columns=["name"],
            rollback="restore_previous",
        )
```

- `key="code"` identifies each row. If you omit `key`, dbwarden uses the model's
  primary key.
- `owned_columns=["name"]` says this declaration owns the `name` column. `code` is
  the key, and any other column would belong to the application.
- `rollback="restore_previous"` records that the generated migration can restore
  the prior value.

## Step 4: Initialize the project

```bash
dbwarden init
```

```text
Created/updated configuration file: /home/you/myapp/dbwarden.py
dbwarden migrations directory created: /home/you/myapp/migrations/primary
```

`init` creates `migrations/<database>/` and is safe to run again.

## Step 5: Generate the migration

```bash
dbwarden make-migrations "seed countries"
```

```text
Generated: primary__0001_seed_countries.sql (2 ops, max severity INFO)
```

Two operations were planned: the `CREATE TABLE` for `countries` and the managed-row
write. The three paired files are under `migrations/primary/`.

There are two generators:

- `dbwarden make-migrations "<description>"` compiles schema and data into one
  dependency-ordered migration. Use it when tables or columns must change with the
  data.
- `dbwarden make-data-migration "<description>"` compiles data operations only. It
  never creates or alters tables, so the target tables must already exist when the
  migration is applied. Use it for data-only releases.

Applying a data-only migration before its tables exist fails at execution with a
data-operation error, which is why this tutorial uses `make-migrations` for the
first pass.

## Step 6: Apply under the severity gate

```bash
dbwarden migrate --force
```

```text
[APPLIED] Completed migration: primary__0001_seed_countries.sql (version: 0001)
Migrations completed successfully: 1 migrations applied.
```

Every data operation carries a severity:

| Severity | Example |
|---|---|
| `INFO` | A managed-row seed; a validation-only declaration |
| `WARN` | A derived write or backfill; a reversible managed-row revision |
| `CRITICAL` | Dropping a source, overwriting owned values, or an irreversible archive |

`--force` acknowledges `WARN` and `CRITICAL` operations. A brand-new seed such as
this one is `INFO`, so plain `dbwarden migrate` would also succeed here. Keep
`--force` in the loop because the project gains a derived write on
[page 5](./05-expressions.md), which is `WARN`, and the destructive revision on
[page 3](./03-managed-rows.md), which is `CRITICAL`.

## Step 7: Verify convergence

```bash
dbwarden check --data
```

```text
No schema changes detected.
```

`check --data` compares the schema, any pending migration, and the declared data.
It succeeds only when all three converge. The rows themselves are not printed by
default:

```bash
python3 -c "import sqlite3; print(sqlite3.connect('app.db').execute('SELECT code, name FROM countries ORDER BY code').fetchall())"
```

```text
[('AR', 'Argentina'), ('BR', 'Brazil'), ('UY', 'Uruguay')]
```

## Recap

- A dbwarden project needs `dbwarden.py`, a models package, and any configured
  `data_paths` directory to exist.
- Managed rows are declared with `rows(key=..., rows=..., owned_columns=..., rollback=...)`.
- `make-migrations` unifies schema and data; `make-data-migration` is data-only and
  assumes its tables already exist.
- Data operations are severity-gated; `--force` acknowledges `WARN`/`CRITICAL`.
- `check --data` proves that schema, pending SQL, and declared data converge.

## What's next

Learn the full managed-row surface - keys, ownership, missing-row policies, scope,
and rollback - in [3. Managed rows](./03-managed-rows.md).
