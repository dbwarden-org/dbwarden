---
description: Set up DBWarden in your project, install dependencies, initialize the project,
  define your first database, and verify that the configuration loads correctly.
---

# Setup

This guide shows the initial project setup for DBWarden. By the end, you will have DBWarden installed, a project-local config file, and one verified database entry.

## Requirements

- Python 3.12 or higher
- A project that uses SQLAlchemy models, or plans to
- A supported backend: PostgreSQL, MySQL, MariaDB, SQLite, or ClickHouse

## Install DBWarden

Install the base package:

```bash
uv add dbwarden
```

Optional dependency groups:

| Group | Command | Use case |
|---|---|---|
| `fastapi` | `uv add "dbwarden[fastapi]"` | FastAPI session dependencies and runtime integration |
| `metrics` | `uv add "dbwarden[metrics]"` | Prometheus metrics |
| `sandbox` | `uv add "dbwarden[sandbox]"` | Sandbox migration testing |

You can combine them:

```bash
uv add "dbwarden[fastapi,metrics,sandbox]"
```

## Initialize the Project

```text
$ dbwarden init
Initialized DBWarden project structure
Created migrations directory
Created dbwarden.py
```

`init` creates the local migration layout and a declarative config scaffold. It is safe to run again, DBWarden will not destroy existing `database_config(...)` definitions.

## Create the Configuration File

By default, `dbwarden init` creates `dbwarden.py` with a `DbwardenDatabase` subclass, and that is the simplest place to start. The function alternative, `database_config(...)`, can also live in any discovered Python file inside your project.

The simplest `dbwarden.py` looks like this:

```python
from dbwarden import DbwardenDatabase


class Primary(DbwardenDatabase):
    database_name = "primary"
    default = True
    database_type = "postgresql"
    database_url_sync = "postgresql://user:password@localhost:5432/main"
    model_paths = ["app.models"]
    model_tables = ["users", "posts", "comments"]
    dev_database_type = "sqlite"
    dev_database_url = "sqlite:///./development.db"
```

Copy this into `dbwarden.py`, or use `database_config(...)` in another discovered Python module, then adjust the URLs, `model_paths`, and `model_tables` for your project.

## Step by Step

### Step 1: Import `DbwardenDatabase`

```python
from dbwarden import DbwardenDatabase
```

Define each concrete `DbwardenDatabase` subclass with a unique name and URL. Every configured database becomes part of the validated runtime config. The equivalent `database_config(...)` function API remains supported, including for plugin examples and integrations.

### Step 2: Define `database_name`

```python
database_name="primary"
```

This is the stable name you will use in CLI commands such as:

```bash
$ dbwarden status --database primary
```

### Step 3: Mark the default database

```python
default=True
```

Exactly one configured database must be the default. Commands without `--database` use that entry.

### Step 4: Set the backend type

```python
database_type="postgresql"
```

This controls backend-specific SQL generation, schema inspection, and metadata behavior.

Supported values:

- `postgresql`
- `mysql`
- `mariadb`
- `sqlite`
- `clickhouse`

### Step 5: Set the runtime URL

```python
database_url_sync="postgresql://user:password@localhost:5432/main"
```

This is the database URL used by CLI commands such as `make-migrations`, `migrate`, `status`, and `check`.

If your application also uses async SQLAlchemy sessions, you can define an async URL too:

```python
database_url_async="postgresql+asyncpg://user:password@localhost:5432/main"
```

DBWarden keeps sync and async URLs separate so the CLI and FastAPI runtime can share one config source without forcing a single driver choice.

### Step 6: Point to your models

```python
model_paths=["app.models"]
```

This tells DBWarden where to discover SQLAlchemy models. In multi-database projects, explicit `model_paths` are required.

### Step 7: Filter tables for this database

```python
model_tables=["users", "posts", "comments"]
```

Optional. When set, DBWarden only includes the listed tables from the discovered models. All other discovered tables are ignored.

This is useful when multiple databases share the same `model_paths` but own different subsets of tables:

```python
class Primary(DbwardenDatabase):
    database_name = "primary"
    default = True
    database_type = "postgresql"
    database_url_sync = "postgresql://user:password@localhost:5432/main"
    model_paths = ["app.models"]
    model_tables = ["users", "posts", "comments"]

class Analytics(DbwardenDatabase):
    database_name = "analytics"
    database_type = "clickhouse"
    database_url_sync = "clickhouse://default:@localhost:8123/analytics"
    model_paths = ["app.models"]
    model_tables = ["events", "page_views"]

class Logs(DbwardenDatabase):
    database_name = "logs"
    database_type = "mysql"
    database_url_sync = "mysql+pymysql://user:password@localhost:3306/logs"
    model_paths = ["app.models"]
    model_tables = ["audit_log", "error_log"]
```

If `model_tables` is not set, all discovered tables in `model_paths` belong to that database.

### Step 8: Configure a dev database

```python
dev_database_type="sqlite"
dev_database_url="sqlite:///./development.db"
```

This is optional, but recommended. It lets you run commands locally with `--dev`, without touching your main database.

## Verify the Configuration

Run:

```text
$ dbwarden settings show --all
Database: primary
Type: postgresql
Default: true
Sync URL: postgresql://user:password@localhost:5432/main
Model paths: ['app.models']
Model tables: ['users', 'posts', 'comments']
Dev database type: sqlite
Dev database URL: sqlite:///./development.db
```

If this command works, DBWarden can resolve and validate your config.

## Common Problems

### `No configuration found`

DBWarden could not locate a config source. Make sure your project contains at least one discovered file with a concrete `DbwardenDatabase` subclass or a `database_config(...)` call. `dbwarden.py` is the default convention, but it does not have to be the only location.

### `Exactly one default=True required`

If you configure more than one database, only one can be the default.

### `model_paths is required when more than one database is configured`

In multi-database setups, each database must declare the models that belong to it.

Next, continue with [Modeling](modeling.md).
