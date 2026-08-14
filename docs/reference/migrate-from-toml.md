# Migrate from TOML

If your project currently uses `warden.toml` for DBWarden configuration, this guide walks through transitioning to Python-based configuration with the default `DbwardenDatabase` API. The equivalent `database_config(...)` function remains supported.

## Why migrate

The Python configuration model offers:

- **Type validation** - catches misconfigurations at import time, not runtime
- **Runtime flexibility** - use environment variables, conditional logic, and expressions in your config
- **IDE support** - autocomplete, type hints, and inline documentation
- **Consistency** - same Python codebase powers your app and your migrations

## Before: TOML configuration

```toml
default = "primary"

[database.primary]
database_type = "postgresql"
sqlalchemy_url = "postgresql://user:password@localhost:5432/main"
migrations_dir = "migrations/primary"
model_paths = ["app.models.api"]
```

## After: Python configuration

```python
from dbwarden import database_config


primary = database_config(
    database_name="primary",
    default=True,
    database_type="postgresql",
    database_url_sync="postgresql://user:password@localhost:5432/main",
    migrations_dir="migrations/primary",
    model_paths=["app.models.api"],
)
```

## What changed in field names

| TOML field | Python field | Notes |
|------------|--------------|-------|
| `default` | `default=True` | Boolean flag per entry instead of top-level |
| `database.<name>.database_type` | `database_type` | Passed directly to function |
| `database.<name>.sqlalchemy_url` | `database_url_sync` | Note: renamed for clarity |
| `database.<name>.migrations_dir` | `migrations_dir` | Same concept, different syntax |
| `database.<name>.model_paths` | `model_paths` | List syntax in Python |
| `database.<name>.dev_database_type` | `dev_database_type` | Optional dev swap entries |
| `database.<name>.dev_database_url` | `dev_database_url` | Optional dev swap entries |

## Migration checklist

### Step 1: Identify current databases

Check your existing `warden.toml`:

```bash
$ dbwarden database list
```

Note each database entry and its configuration.

### Step 2: Create new config source

Create your new config file (or update an existing config file that will contain `database_config(...)` calls).

Typical options:

- `dbwarden.py` (recommended for new projects)
- `app/core/config.py` (if you already have one)
- Any Python file that DBWarden can discover

### Step 3: Map each database entry

For each database in your TOML config, create a corresponding `database_config(...)` call.

**Example transformation:**

```python
# From TOML:
# [database.primary]
# database_type = "postgresql"
# sqlalchemy_url = "postgresql://user:password@localhost:5432/main"
# dev_database_type = "sqlite"
# dev_database_url = "sqlite:///./development.db"

# To Python:
from dbwarden import database_config


primary = database_config(
    database_name="primary",
    default=True,
    database_type="postgresql",
    database_url_sync="postgresql://user:password@localhost:5432/main",
    dev_database_type="sqlite",
    dev_database_url="sqlite:///./development.db",
)
```

### Step 4: Verify configuration loads

```bash
$ dbwarden settings show --all
```

You should see all your migrated databases listed with correct types and URLs.

### Step 5: Test core commands

Confirm the migration works by running key commands:

```bash
$ dbwarden status --database primary
$ dbwarden history --database primary
```

### Step 6: Remove TOML file

Once verified, delete your old `warden.toml`:

```bash
rm warden.toml
```

DBWarden now uses only your Python configuration source.

## Advanced migration patterns

### Using environment variables

```python
import os


DATABASE_URL = os.getenv("DATABASE_URL")


primary = database_config(
    database_name="primary",
    default=True,
    database_type="postgresql",
    database_url_sync=DATABASE_URL,
    dev_database_type="sqlite",
    dev_database_url="sqlite:///./development.db",
)
```

This replaces the old pattern of reading URL from environment via TOML (which TOML cannot do natively).

### Using conditional configuration

```python
import os


ENV = os.getenv("ENVIRONMENT", "development")


primary = database_config(
    database_name="primary",
    default=True,
    database_type="postgresql",
    database_url_sync="postgresql://user:password@localhost:5432/main",
    dev_database_type="sqlite",
    dev_database_url="sqlite:///./development.db" if ENV == "development" else None,
)
```

This is impossible with TOML but natural with Python.

### Adding `secure_values` for sensitive URLs

If your configuration uses variables or expressions for credentials:

```python
import os


DB_USER = os.getenv("DB_USER")
DB_PASS = os.getenv("DB_PASS")


primary = database_config(
    database_name="primary",
    default=True,
    database_type="postgresql",
    database_url_sync=f"postgresql://{DB_USER}:{DB_PASS}@localhost:5432/main",
    secure_values=True,
)
```

With `secure_values=True`, display commands show the expression rather than resolved credentials.

## Common issues during migration

### "Exactly one default=True required"

Ensure exactly one entry has `default=True`. Unlike TOML's top-level `default` field, you must set this on exactly one entry.

### "model_paths is required when more than one database is configured"

When using multiple databases, each needs explicit `model_paths` to keep model discovery boundaries clear.

### "Duplicate database_name" or "Duplicate database_url"

Python loads all `database_config(...)` calls, so ensure each call has a unique `database_name` and no duplicate URLs.

### TOML-specific features not supported

Some TOML features don't map 1:1 to Python:

- Inline tables → use separate key-value dictionary in Python (more verbose but explicit)
- Multiline strings → use triple-quoted strings in Python
- Arrays of tables → use separate `model_paths=[...]` list entries

If you used advanced TOML features, manually translate those to equivalent Python constructs.

## Verification commands after migration

After completing the migration, run these to confirm everything works:

```bash
# Confirm all databases visible
$ dbwarden settings show --all

# Confirm status works
$ dbwarden status --database <name>

# Confirm history works
$ dbwarden history --database <name>

# If using --dev, confirm it works
$ dbwarden --dev status --database <name>
```

## Rollback if needed

If migration causes issues, you can always:

1. Recreate `warden.toml` with the original configuration
2. Run DBWarden version that supports TOML (pre-0.5)

However, the Python-based approach is the recommended direction (0.9+) and offers significant benefits.
