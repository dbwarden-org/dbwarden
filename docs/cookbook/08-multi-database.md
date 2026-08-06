# 8. Multi-Database & Configuration

DBWarden supports managing multiple databases in a single project; each with its own migration directory, lock, tracking table, and model paths. You can mix PostgreSQL, MySQL, and ClickHouse backends in the same codebase.

For complete documentation see the [Multi-Database Configuration](../configuration/multi-database.md) reference.

## What You'll Learn

- How to configure multiple databases in one project
- How to target specific databases with CLI flags
- How to manage PostgreSQL + MySQL + ClickHouse in the same codebase
- How to use `dbwarden settings` for runtime configuration changes

## Prerequisites

- Docker (for PostgreSQL, MySQL, and ClickHouse containers)
- `examples/multi-database/` directory

## Scenario

A project with three databases:

- **primary** (PostgreSQL): transactional user data
- **legacy** (MySQL): legacy CRM and reporting data
- **analytics** (ClickHouse): page view events for analysis

## Step 1: The Configuration

```python
from dbwarden import database_config

primary = database_config(
    database_name="primary",
    default=True,
    database_type="postgresql",
    database_url_sync="postgresql://user:password@localhost:5432/primary",
    database_url_async="postgresql+asyncpg://user:password@localhost:5432/primary",
    model_paths=["app.models.primary"],
)

legacy = database_config(
    database_name="legacy",
    database_type="mysql",
    database_url_sync="mysql+pymysql://user:password@localhost:3306/legacy",
    model_paths=["app.models.legacy"],
)

analytics = database_config(
    database_name="analytics",
    database_type="clickhouse",
    database_url_sync="http://localhost:8123/analytics",
    model_paths=["app.models.analytics"],
)
```

Key rules:
- Exactly one database must have `default=True` (used when `--database` is omitted)
- Each database must have separate `model_paths` (no overlap by default)
- Each database gets its own migration directory under `migrations/`
- MySQL models use `MyTableMeta` / `MyColumnMeta` for engine, charset, and column metadata

## Step 2: Start the Databases

```bash
cd examples/multi-database
docker compose up -d
```

## Step 3: Initialize and Migrate

```bash
$ dbwarden init
$ dbwarden migrate --all
```

This applies migrations to both databases in sequence. Each has its own lock, its own tracking table, and its own migration history.

## Step 4: Target a Specific Database

```bash
# Generate migrations for primary only
$ dbwarden make-migrations "add user table" --database primary

# Apply to analytics only
$ dbwarden migrate --database analytics

# Check status of one database
$ dbwarden status --database primary
```

## Step 5: Check Status of All Databases

```bash
$ dbwarden status --all
```

Output:

```
Database: primary
  Applied:  1
  Pending:  0
  Status:   up-to-date

Database: analytics
  Applied:  1
  Pending:  0
  Status:   up-to-date
```

## Step 6: Using `dbwarden settings`

The settings commands allow runtime configuration changes without editing `dbwarden.py` directly:

```bash
# View current configuration
$ dbwarden settings show --all

# Set a default database
$ dbwarden settings default-database primary

# Add a new database entry
$ dbwarden settings database-add reporting postgresql://localhost:5432/reporting \
    --type postgresql \
    --model-path app.models.reporting

# Or add a MySQL database
$ dbwarden settings database-add legacy mysql+pymysql://localhost:3306/legacy \
    --type mysql \
    --model-path app.models.legacy

# Remove a database
$ dbwarden settings database-remove reporting

# Rename a database
$ dbwarden settings database-rename analytics analytics_v2
```

Settings commands modify the `dbwarden.py` file directly using AST-based mutation. The changes are permanent and committed to version control.

## Step 7: Dev Mode with Multiple Databases

Each database can independently configure dev mode:

```python
primary = database_config(
    database_name="primary",
    database_type="postgresql",
    database_url_sync="postgresql://localhost/primary",
    dev_database_type="sqlite",
    dev_database_url="sqlite:///./dev_primary.db",
    model_paths=["app.models.primary"],
)

legacy = database_config(
    database_name="legacy",
    database_type="mysql",
    database_url_sync="mysql+pymysql://localhost:3306/legacy",
    dev_database_type="sqlite",
    dev_database_url="sqlite:///./dev_legacy.db",
    model_paths=["app.models.legacy"],
)

analytics = database_config(
    database_name="analytics",
    database_type="clickhouse",
    database_url_sync="http://localhost:8123/analytics",
    dev_database_type="sqlite",
    dev_database_url="sqlite:///./dev_analytics.db",
    model_paths=["app.models.analytics"],
)
```

```bash
# Dev mode for all databases
$ dbwarden --dev migrate --all

# Dev mode for a specific database
$ dbwarden --dev migrate --database analytics
```

## Step 8: Legacy Database with MySQL Metadata

The legacy MySQL database uses `MyTableMeta` and `MyColumnMeta` for MySQL-specific features. Here is a sample model from `app/models/legacy/customer.py`:

```python
from sqlalchemy import Integer, String, TIMESTAMP, Text
from sqlalchemy.orm import Mapped, mapped_column
from dbwarden.databases.mysql import MyTableMeta, MyColumnMeta, my

class Customer(Base):
    __tablename__ = "customers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[str] = mapped_column(TIMESTAMP)

    class Meta(MyTableMeta):
        my_engine = "InnoDB"
        my_charset = "utf8mb4"
        my_collate = "utf8mb4_unicode_ci"
        comment = "Legacy CRM customers"

        class id(MyColumnMeta):
            my = my.field(unsigned=True)

        class created_at(MyColumnMeta):
            my = my.field(on_update="CURRENT_TIMESTAMP")
```

Migrations for the legacy database work identically to other databases:

```bash
# Generate migration for MySQL legacy database
$ dbwarden make-migrations "add customer table" --database legacy

# Apply to legacy only
$ dbwarden migrate --database legacy
```

The generated DDL will target MySQL-native syntax:

```sql
CREATE TABLE IF NOT EXISTS customers (
    id INTEGER UNSIGNED NOT NULL AUTO_INCREMENT,
    name VARCHAR(200) NOT NULL,
    notes TEXT,
    created_at TIMESTAMP NOT NULL ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='Legacy CRM customers';
```

## Key Takeaways

- Multiple `database_config()` calls create independent database targets
- Each database has its own migration directory, lock, and history
- `--database` targets a specific database; `--all` targets every database
- `default=True` controls which database is used when `--database` is omitted
- `settings` commands modify `dbwarden.py` at runtime without manual editing
- Dev mode can be configured independently per database

## Related Documentation

- [Multi-Database Configuration](../configuration/multi-database.md)
- [Dev Mode](../configuration/dev-mode.md)
- [Settings Command](../commands/settings.md)

## Next

[Section 9: FastAPI Integration](09-fastapi-integration.md)
