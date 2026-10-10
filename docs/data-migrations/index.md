---
title: Data migrations
description: Declarative data migrations put reviewed changes to data under the same versioned, frozen, and reversible contract as schema changes.
---

# Data migrations

Declarative data migrations describe intended rows and transformations beside your SQLAlchemy models. DBWarden compiles those declarations into a canonical data specification, reviewable SQL, a checksummed plan, and a frozen artifact. Applying a migration uses the frozen artifacts, never the current declarations, so a reviewed data change ships with a schema release exactly like any other migration.

Use data migrations for changes to data that belong to a release: reference rows, deterministic backfills, and one-shot moves of legacy data into normalised tables. Keep one-off repair SQL and ad-hoc loading in [seeds](../seeds.md) or plain migration files.

## Start here

- **[Tutorial](tutorial/index.md)** - a from-scratch, runnable walkthrough that builds a SQLite project one declaration at a time: managed rows, static sources, expressions, validation, historical transitions, splits, merges, capture, archive, batching, and the full apply, rollback, reapply, and reconcile lifecycle.
- **[Reference](reference.md)** - the full surface: every declaration, expression, artifact, execution boundary, and recovery path.
- **[Design specification](../design/declarative-data-migrations.md)** - the artifact and execution contract, capability matrix, and conformance status.

## Runnable examples

The [`examples/data-migrations/`](https://github.com/dbwarden-org/dbwarden/tree/main/examples/data-migrations) directory contains four self-contained SQLite projects used throughout the tutorial: `basics/`, `transitions/`, `merges/`, and `lifecycle/`.

## At a glance

```python
from sqlalchemy import String
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from dbwarden.data import DataMeta, rows

class Base(DeclarativeBase):
    pass

class Country(Base):
    __tablename__ = "countries"
    code: Mapped[str] = mapped_column(String(2), primary_key=True)
    name: Mapped[str] = mapped_column(String, nullable=False)

    class Data(DataMeta):
        managed_rows = rows(
            key="code",
            rows=[{"code": "UY", "name": "Uruguay"}],
            owned_columns=["name"],
            rollback="restore_previous",
        )
```

```console
$ dbwarden make-migrations "seed countries"
$ dbwarden migrate --force
$ dbwarden check --data
```

## How it fits

- **Declarations** live beside models (an inner `Data(DataMeta)` class) or in modules under `data_paths` (`DataTransition` classes).
- **Compilation** turns them into typed operations, a canonical data specification, SQL, and a manifest-bound bundle of `.sql`, `.plan.json`, and `.data.py`.
- **Execution** runs under the normal migration lock, journals every step, and verifies guards and convergence.
- **Recovery** uses the frozen rollback plan, explicit reapply epochs, and reconciliation for uncertain effects.

Data migrations are backend-aware. PostgreSQL, SQLite, MySQL, and MariaDB support the full operation set subject to their execution boundaries; ClickHouse supports a limited subset. See the [Reference](reference.md) for the capability matrix.
