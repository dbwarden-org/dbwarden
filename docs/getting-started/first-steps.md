---
description: 'Get started with DBWarden: initialize your project, define your database configuration,
  and learn the migration workflow from model changes to applied SQL.'
---

# First Steps

This walkthrough is the foundation of the DBWarden workflow.

The goal is not just to run commands, but to understand why each step exists and how it fits the migration lifecycle.

## Step 1: Initialize the project

```bash
$ dbwarden init
```

This creates:

- a migrations directory structure
- a Python configuration scaffold (`dbwarden.py`)

Why it matters: DBWarden expects a project-local migration layout and config source so migration behavior is deterministic per repository.

## Step 2: Define one explicit database entry

```python
from dbwarden import database_config


primary = database_config(
    database_name="primary",
    default=True,
    database_type="postgresql",
    database_url_sync="postgresql://user:password@localhost:5432/main",
    model_paths=["app.models"],
    model_tables=["users"],
)
```

Why it matters: DBWarden resolves migration targets from explicit typed entries, not inferred environment state.

## Step 3: Add SQLAlchemy models

DBWarden uses model metadata to generate migration SQL. A minimal model example:

```python
from sqlalchemy import DateTime, Integer, String
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from datetime import datetime


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
```

Why it matters: model metadata is the input to `make-migrations`.

## Step 4: Generate migration SQL

```bash
$ dbwarden make-migrations -d "create users table" --database primary
```

DBWarden creates a versioned SQL file under `migrations/primary/`.

Why it matters: this file is now part of your code review process and deployment artifact.

## Step 5: Review the generated migration

Open the file and validate both sections:

```sql
-- upgrade

-- rollback
```

Why it matters: rollback quality determines recovery quality.

## Step 6: Apply migrations

```bash
$ dbwarden migrate --database primary
```

During execution DBWarden:

1. resolves config and target database
2. acquires migration lock
3. executes pending SQL
4. stores migration record and checksum
5. releases lock

## Step 7: Verify the result

```bash
$ dbwarden status --database primary
$ dbwarden history --database primary
```

Use status to confirm pending/applied counts and history to confirm execution order.

## Common first-run issues

- `No configuration found`: ensure your project has one discovered config source with `database_config(...)`
- `Database '<name>' not found`: ensure `--database` matches configured `database_name`
- `No SQLAlchemy models found`: set `model_paths` explicitly in config

## Next Steps

- [Configuration](../configuration/index.md)
- [Your First Migration](first-migration.md)
- [Developing Locally](developing-locally.md)
