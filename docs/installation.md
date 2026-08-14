# Installation

This guide covers installing DBWarden in your project and verifying it works correctly.

## Requirements

- Python 3.10 or higher
- A project that uses SQLAlchemy for database models
- uv or another Python package manager

## Install using uv

```bash
uv add dbwarden
```

### Development dependencies

To also install testing and linting tools:

```bash
uv add "dbwarden[dev]"
```

### Optional dependency groups

The `[postgres]` extra is the most commonly used. Install it if you are targeting PostgreSQL.

| Group                  | Command | Provides |
|------------------------|-------|-------|
| `postgres`             | `uv add "dbwarden[postgres]"` | PostgreSQL driver (`psycopg2-binary`) |
| `mysql`                | `uv add "dbwarden[mysql]"` | MySQL/MariaDB driver (`pymysql`) |
| `clickhouse`           | `uv add "dbwarden[clickhouse]"` | ClickHouse driver (`clickhouse-connect`) |
| `fastapi`              | `uv add "dbwarden[fastapi]"` | FastAPI session dependencies, health router, migration router, metrics router, Redis lock |
| `metrics`              | `uv add "dbwarden[metrics]"` | Prometheus metrics endpoint (`prometheus-client`) |
| `sandbox`              | `uv add "dbwarden[sandbox]"` | Sandbox migration testing via testcontainers |

Combine groups as needed:

```bash
uv add "dbwarden[postgres,mysql,fastapi]"
```

## Database drivers

DBWarden uses SQLAlchemy under the hood. The recommended way to install drivers is via the extras above:

```bash
# PostgreSQL
uv add "dbwarden[postgres]"

# MySQL/MariaDB
uv add "dbwarden[mysql]"

# ClickHouse
uv add "dbwarden[clickhouse]"

# SQLite comes bundled with Python
```

You can also install drivers directly if you prefer:

```bash
uv add psycopg2-binary    # PostgreSQL
uv add pymysql            # MySQL / MariaDB
uv add clickhouse-connect # ClickHouse
```

## Verify installation

After installing, confirm DBWarden is available:

```bash
$ dbwarden version
```

You should see output:

```
0.9.4
```

## Initialize in your project

Create the DBWarden structure in your project directory:

```bash
$ dbwarden init
```

This creates:

- a `migrations/` directory structure
- a `dbwarden.py` config scaffold (or discovers your existing config source)

## What happens during init

When you run `init`, DBWarden:

1. Creates `migrations/` if missing
2. Creates `dbwarden.py` (or updates existing config source) with declarative class scaffolding
3. Does not overwrite existing `database_config(...)` definitions you have added

You can run `init` safely on an existing project - it is idempotent.

## Quick configuration

After init, configure your first database by editing `dbwarden.py` (or your existing config file):

```python
from dbwarden import DbwardenDatabase


class Primary(DbwardenDatabase):
    database_name = "primary"
    default = True
    database_type = "postgresql"
    database_url_sync = "postgresql://user:password@localhost:5432/mydb"
    dev_database_type = "sqlite"
    dev_database_url = "sqlite:///./development.db"
```

The `dev_database_*` fields are optional but recommended - they enable fast local iterations with `--dev`.
The function alternative, `database_config(...)`, remains supported, and some
plugins use it in examples or integration code.

## Verify configuration loads

```bash
$ dbwarden settings show --all
```

You should see your database entry printed with type and URL.

## Common installation issues

**Command not found after uv add**

- Ensure your virtual environment is activated
- Try removing and re-adding: `uv remove dbwarden && uv add dbwarden`

**Import errors or missing module warnings**

- Upgrade uv and reinstall: `uv add --upgrade dbwarden`

**Database driver errors**

- Install the appropriate driver for your target database (see Database drivers section above)

## Upgrading

To update to a newer version:

```bash
uv add --upgrade dbwarden
```

Or with poetry:

```bash
poetry update dbwarden
```

Check the release notes when upgrading major versions - there may be configuration or workflow changes.
