# Seed Management

**Requires the `dbwarden-seeds` plugin:** `dbwarden plugin add dbwarden-seeds`. Seed management was previously built into core and now ships as an official plugin. The `dbwarden seed` commands still exist in core, but without the plugin installed each one exits with a message telling you to install it.

dbwarden provides seed data management for populating databases with initial or reference data. Seeds complement migrations by handling data that belongs in version control.

## Overview

There are two ways to define seeds, listed in order of preference:

1. **Code seeds** (recommended): define seeds inline alongside your SQLAlchemy models using the `Seed` base class. Each seed has an immutable explicit version.
2. **File seeds**: traditional `.sql` or `.py` files in a `seeds/` directory, useful for complex multi-statement SQL.

Both are tracked in the `_dbwarden_seeds` table and applied via `dbwarden seed apply`.

---

## Code Seeds (Recommended)

Code seeds live alongside your models in your `model_paths` directories. They are the recommended way to define seed data because they stay in sync with your schema, support IDE autocompletion, and do not require manual version management.

### Seed Base Class

Inherit from `Seed` and set a `model` + `rows`:

```python
from dbwarden.seed import Seed

class CountrySeed(Seed):
    __seed_version__ = "C0001"
    __seed_database__ = "primary"
    __seed_description__ = "initial countries"
    __seed_on_conflict__ = "update"
    __seed_conflict_columns__ = ["code"]

    model = Country
    rows = [
        Country(code="UY", name="Uruguay"),
        Country(code="AR", name="Argentina"),
    ]
```

Key advantages over the old decorator approach:

- **Full IDE autocompletion**: `rows` uses model instances directly, so your editor knows the column names and types
- **Immutable versioning**: declare a permanent `__seed_version__` such as `C0001`; never change or reuse a released version
- **No manual import of `SeedRow`**: though `SeedRow` is still available if you prefer dict-like rows

### Seed Class Reference

| Attribute | Default | Description |
|-----------|---------|-------------|
| `__seed_database__` | `"default"` | Routes the seed to the named database handle configured by a `DbwardenDatabase` class or `database_config(...)`. |
| `__seed_description__` | `""` | Human-readable label shown in `dbwarden seed list` output. |
| `__seed_on_conflict__` | `"ignore"` | What to do when a row with matching columns exists: `"ignore"` (skip silently), `"update"` (overwrite), or `"error"` (raise). |
| `__seed_conflict_columns__` | `None` | List of column names used for conflict detection. Required when `__seed_on_conflict__` is `"update"`. |

### Model Instances in Rows

Because `rows` accepts model instances, you get full autocompletion from your `Mapped` annotations:

```python
from dbwarden.seed import Seed

class RepoSeed(Seed):
    __seed_database__ = "clickhouse"
    __seed_description__ = "Tracked Repos"

    model = Repo
    rows = [
        Repo(name="dbwarden", owner="anomalyco", is_org=True, default_branch="main"),
        Repo(name="vigil", owner="anomalyco", is_org=True, default_branch="master"),
    ]
```

Your editor will suggest `name`, `owner`, `is_org`, `default_branch` etc. as you type.

> SQLAlchemy 2.0's `DeclarativeBase` does not accept positional arguments in the constructor. Always use keyword arguments when instantiating models in `rows`: `Repo(name="dbwarden", ...)` instead of `Repo("dbwarden", ...)`.

### SeedRow (Alternative)

If you prefer dict-like rows, `SeedRow` still works:

```python
from dbwarden.seed import Seed, SeedRow

class CountrySeed(Seed):
    __seed_database__ = "primary"
    __seed_description__ = "initial countries"
    __seed_on_conflict__ = "update"
    __seed_conflict_columns__ = ["code"]

    model = Country
    rows = [
        SeedRow(code="UY", name="Uruguay"),
        SeedRow(code="AR", name="Argentina"),
    ]
```

### `on_conflict` Behavior

| Value | Behavior |
|-------|----------|
| `"ignore"` (default) | Skips existing rows silently |
| `"update"` | Updates existing rows with new values |
| `"error"` | Raises an error on conflict |

### PostgreSQL Schema Resolution

When a model uses `pg_schema` in its Meta (via `PGTableMeta` or `PGViewMeta`), code seeds automatically qualify the table name with that schema:

```python
from dbwarden.databases.pgsql import PGTableMeta

class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    class Meta(PGTableMeta):
        pg_schema = "app"

class UserSeed(Seed):
    __seed_database__ = "primary"
    __seed_description__ = "initial users"

    model = User
    rows = [User(email="alice@example.com", name="Alice")]
```

The generated INSERT becomes:

```sql
INSERT INTO app.users (email, name) VALUES ('alice@example.com', 'Alice')
```

The schema is resolved in this order: `Meta.pg_schema`, then `Meta.backend_table.schema`, then `__table__.schema`. The seed tracking table (default `_dbwarden_seeds`) stays in the schema set by the connection's `search_path` (config-level `pg_schema`).

### Logic-Based Seeds

Define `forward(connection, session)` and `reverse(connection, session)` for procedural data, analogous to Django's `RunPython`:

```python
class PermissionSeed(Seed):
    __seed_version__ = "C0002"
    __seed_database__ = "primary"
    __seed_description__ = "load permissions"
    __seed_on_conflict__ = "ignore"

    model = Permission

    @staticmethod
    def forward(connection, session):
        for resource in ["users", "orders"]:
            for action in ["read", "write", "delete"]:
                session.add(Permission(name=f"{resource}:{action}"))

    @staticmethod
    def reverse(connection, session):
        session.execute("DELETE FROM permissions")
```

### `@seed_data` Decorator (Deprecated)

The old decorator still works but is deprecated in favour of the `Seed` base class:

```python
from dbwarden.seed import seed_data, SeedRow

@seed_data(
    database="primary",
    description="initial countries",
    on_conflict="update",
    conflict_columns=["code"],
)
class CountrySeed:
    model = Country
    rows = [SeedRow(code="UY", name="Uruguay")]
```

The deprecated decorator does not provide the required immutable version contract; use `Seed` subclasses for new code seeds.

### Discovery and Ordering

Code seeds are discovered through the same `model_paths` scan as models. They declare immutable versions in the `C` namespace (`C0001`, `C0002`, ...) and duplicate versions are rejected. Pending detection compares versions against the `_dbwarden_seeds` tracking table.

---

## File Seeds (Traditional)

File seeds live in a `seeds/` directory and are useful for complex multi-statement SQL or when you need to hand-craft seed files.

### Directory Structure

```
seeds/
  V0001__seed_initial_users.sql
  V0002__seed_lookup_tables.sql
  V0003__seed_sample_data.py
```

Each file follows the naming convention:

```
V<4-digit-version>__<description>.<sql|py>
```

### Creating File Seeds

```bash
$ dbwarden seed create "seed initial users" --database primary
```

Creates a file like `seeds/V0001__seed_initial_users.sql`:

```sql
-- INSERT statements go here
```

### Python File Seeds

```bash
$ dbwarden seed create "generate sample data" --database primary --type python
```

Creates `seeds/V0001__generate_sample_data.py` with `forward(connection, session)` and `reverse(connection, session)` functions.

Both functions receive a raw SQLAlchemy `Connection` and an ORM `Session` bound to the same transaction:

```python
# Using raw connection
def forward(connection, session):
    for i in range(100):
        connection.execute(
            "INSERT INTO users (name) VALUES (:name)",
            {"name": f"user_{i}"},
        )

# Using ORM session
def forward(connection, session):
    for i in range(100):
        session.add(User(name=f"user_{i}"))
    session.flush()
```

---

## Applying Seeds

Apply all pending seeds (file + code seeds are both discovered):

```bash
$ dbwarden seed apply --database primary
```

Apply a specific version:

```bash
$ dbwarden seed apply --database primary --version 0003
```

Apply to all databases:

```bash
$ dbwarden seed apply --all
```

### Dry Run

Preview what would be applied without executing:

```bash
$ dbwarden seed apply --database primary --dry-run
```

### Auto-Apply After Migrations

Configure seeds to be applied automatically after each `dbwarden migrate`:

```python
database_config(
    database_name="primary",
    default=True,
    database_type="sqlite",
    database_url_sync="sqlite:///./app.db",
    model_paths=["models"],
    auto_apply_seeds=True,
)
```

Or apply seeds once after a migration without changing config:

```bash
$ dbwarden migrate --apply-seeds
```

---

## Listing Seeds

```bash
$ dbwarden seed list --database primary
```

Output:

```
Seeds for database 'primary':
  V0001  seed_initial_users                   applied  2025-06-01 10:00:00
  C0001  initial countries                    pending   (code seed)
```

List across all databases:

```bash
$ dbwarden seed list --all
```

### Pruning Orphaned Records

Remove tracking records for seed files that no longer exist on disk:

```bash
$ dbwarden seed list --prune
```

---

## Rolling Back Seeds

Rollback executes the seed's inverse operation and removes its tracking record only after that succeeds. SQL files require a `-- rollback` section; Python and procedural code seeds require `reverse(connection, session)`. Missing reverse logic makes a seed irreversible.

```bash
# Rollback the most recent seed
$ dbwarden seed rollback --database primary

# Rollback a specific number
$ dbwarden seed rollback --database primary --count 2

# Rollback to a specific version
$ dbwarden seed rollback --database primary --to-version 0002
```

---

## Seed Tracking Table

dbwarden tracks applied seeds in `_dbwarden_seeds` (configurable via `seed_table`):

| Column | Description |
|--------|-------------|
| `version` | 4-digit seed version (`V0001`) or code seed ID (`C0001`) |
| `description` | Human-readable description |
| `filename` | File path or code seed identifier |
| `seed_type` | `sql`, `python`, or `code` |
| `checksum` | SHA-256 hash of file/class source |
| `applied_at` | Timestamp of application |

The tracking table is created automatically on first seed apply. Each version can only be applied once until rolled back.

### Checksum Drift

When a seed file has been modified since it was last applied, dbwarden raises an error and blocks seed operations:

```
SeedError: Seed V0001 has been modified since it was applied (checksum mismatch).
```

This helps detect accidental changes to already-applied seeds.

---

## Exporting Seeds for Production

Code seeds require your full application environment to execute. For Dockerized deployments where you don't want to copy the application code into a container just to seed data, use `dbwarden seed export` to produce stateless ROC (runs-on-change) SQL files.

```bash
$ dbwarden seed export --database clickhouse
```
This writes `seeds/ROC__clickhouse__code_seeds.sql` containing `INSERT ... ON CONFLICT` statements rendered in the target database dialect. In production, apply with:
```bash
$ dbwarden seed apply --database clickhouse
```

Because the file is ROC, updating the code seed and re-exporting produces a new content checksum, which triggers re-application. The `ON CONFLICT DO UPDATE` clause handles updating existing rows; no need to delete and recreate.

**Non-handled problems:**

- Rows removed from a code seed are not automatically deleted in the target database
- Procedural `forward()` seeds cannot be exported, because executing arbitrary code against SQLite would not reliably reproduce behavior on the configured backend

**Dialect requirement:** Exporting requires the same dialect packages as connecting to that database. For ClickHouse, install `clickhouse-sqlalchemy`. Missing packages produce a clear error at export time.

## Seeds and Migrations

Seeds are independent from migrations. You can:

- Apply migrations without seeds
- Apply seeds without migrations
- Mix both in your workflow

The `dbwarden status` command and the FastAPI `GET /status` endpoint report both pending migrations and pending seeds.

---

## Seeds in FastAPI

The `DBWardenRouter` includes seed status in its `GET /status` response:

```json
{
  "databases": {
    "primary": {
      "status": "ok",
      "connected": true,
      "pending_migrations": 0,
      "applied_migrations": 5,
      "pending_seeds": 2,
      "applied_seeds": 1,
      "lock_active": false,
      "error": null
    }
  }
}
```

FastAPI integration ships separately as the `dbwarden-fastapi` plugin, so its reference lives with the plugin: [dbwarden-fastapi](https://github.com/dbwarden-org/dbwarden-fastapi).

See also: [Cookbook: Seeds](../cookbook/07-seeds.md)
