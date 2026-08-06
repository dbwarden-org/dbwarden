# 1. Project Setup

## What You'll Learn

- How to initialize a DBWarden project with `dbwarden init`
- How configuration is structured via `database_config()`
- How to inspect your loaded configuration

## Prerequisites

- Python 3.12+ with `uv add dbwarden sqlalchemy`
- The `examples/core/` directory (see [Cookbook Index](index.md))

## Step 1: Initialize the Project

```bash
cd examples/core/
bash scripts/01-setup.sh
```

The `dbwarden init` command creates the directory structure DBWarden expects:

```
migrations/
  primary/          # Migration files for the 'primary' database
```

It also writes a starter `dbwarden.py` if one doesn't exist. In our case, we already have one with our configuration.

## Step 2: Understanding the Configuration

Our `examples/core/dbwarden.py`:

```python
from dbwarden import database_config

primary = database_config(
    database_name="primary",
    default=True,
    database_type="sqlite",
    database_url_sync="sqlite:///./app.db",
    model_paths=["app"],
    model_tables=["users", "posts"],
)
```

Each parameter has a specific role:

| Parameter | Value | Purpose |
|-----------|-------|---------|
| `database_name` | `"primary"` | Logical name used in `--database primary` CLI flags |
| `default` | `True` | Used when no `--database` flag is given |
| `database_type` | `"sqlite"` | Dialect for SQL generation and connection |
| `database_url_sync` | `"sqlite:///./app.db"` | Synchronous connection URL |
| `model_paths` | `["app"]` | Python module paths to scan for SQLAlchemy models |
| `model_tables` | `["users", "posts"]` | Optional table-name filter for this database |

The return value `primary` is a `DatabaseHandle` object. It's also used later for FastAPI dependency injection: the same object provides `primary.async_session` and `primary.sync_session`.

## Step 3: Viewing the Configuration

```text
$ dbwarden config
Configuration:
  Databases:
    primary (default):
      Type: sqlite
      Sync URL: sqlite:///./app.db
      Model Paths: app
      Migrations Dir: migrations/primary
```

This confirms DBWarden has discovered and loaded your configuration. The `(default)` marker means `--database` can be omitted when targeting this database.

## What Happens Under the Hood

When you import `dbwarden` and call `database_config()`:

1. The function call is registered in DBWarden's internal registry
2. On first CLI command, DBWarden discovers `dbwarden.py` via AST scanning
3. It imports the module and executes each `database_config()` call
4. It validates uniqueness, default rules, and model path resolution
5. The resolved configuration is cached for the session

## Key Takeaways

- `dbwarden init` creates the directory skeleton: run it once per project
- `dbwarden config` shows what DBWarden actually resolved (useful for debugging)
- `database_config()` is the single entry point for all configuration
- `model_paths` controls which Python modules are scanned for models
- We chose SQLite here so the example runs with zero external services

## Next

[Section 2: Models & Migrations](02-models-and-migrations.md)
