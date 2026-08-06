# Quick Start

Configure your first database in **2 minutes**.

## Prerequisites

You should have:
- Python 3.10+ installed
- DBWarden installed (`uv add dbwarden`)
- A database to connect to (or use SQLite)

## Step 1: Initialize

Create project structure:

```bash
$ dbwarden init
```

This creates:
- `migrations/` directory
- `dbwarden.py` configuration file

## Step 2: Your First Configuration

Open `dbwarden.py` and add:

```python
from dbwarden import database_config

primary = database_config(
    database_name="primary",
    default=True,
    database_type="sqlite",
    database_url_sync="sqlite:///./app.db",
)
```

That's it! **4 required parameters**:
- `database_name` - What to call this database
- `default` - Is this the default?
- `database_type` - What kind of database?
- `database_url_sync` - How to connect? (sync URL for CLI/migrations)

Start with SQLite for the simplest setup. Switch to PostgreSQL later.

## Step 3: Test the Configuration

Verify DBWarden can read your config:

```bash
$ dbwarden settings show
```

You'll see:

```
Database Configuration
════════════════════════════════════════

primary (default)
  Type: sqlite
  URL: sqlite:///./app.db
  Migrations: migrations/primary
```

## Step 4: Add Model Paths (Optional)

If you have SQLAlchemy models, tell DBWarden where they are:

```python
primary = database_config(
    database_name="primary",
    default=True,
    database_type="sqlite",
    database_url_sync="sqlite:///./app.db",
    model_paths=["app.models"],  #  Add this
)
```

DBWarden will discover models from `app.models` and its submodules.

## Step 5: Upgrade to PostgreSQL

When you're ready for PostgreSQL:

```python
primary = database_config(
    database_name="primary",
    default=True,
    database_type="postgresql",
    database_url_sync="postgresql://user:password@localhost:5432/myapp",
    model_paths=["app.models"],
)
```

## Step 6: Add Dev Mode (Recommended)

Keep SQLite for local dev, use PostgreSQL in production:

```python
primary = database_config(
    database_name="primary",
    default=True,
    database_type="postgresql",
    database_url_sync="postgresql://user:password@localhost:5432/myapp",
    dev_database_type="sqlite",
    dev_database_url="sqlite:///./dev.db",
    model_paths=["app.models"],
)
```

Now you can run commands against SQLite locally:

```bash
$ dbwarden --dev migrate
$ dbwarden --dev status
```

And against PostgreSQL in production:

```bash
$ dbwarden migrate
$ dbwarden status
```

## What Just Happened?

### `database_config` registered your database

When Python loads `dbwarden.py`, it executes `database_config()` which:
1. Validates your parameters
2. Registers the database in DBWarden's internal registry
3. Sets up migration directories

### DBWarden Can Now Find Your Database

All CLI commands now know about your database:

```bash
$ dbwarden make-migrations "create users"
$ dbwarden migrate
$ dbwarden status
$ dbwarden history
```

## Common First-Time Issues

### "No configuration found"

**Cause:** DBWarden can't find `dbwarden.py`

**Solution:** Ensure you're in the project directory and `dbwarden.py` exists.

### "No SQLAlchemy models found"

**Cause:** DBWarden can't discover your models

**Solution:** Add `model_paths` to your config:

```python
model_paths=["app.models"]
```

### "Exactly one default=True required"

**Cause:** Multiple databases without one marked as default

**Solution:** Set one database to `default=True`

## Complete Minimal Example

```python
# dbwarden.py
from dbwarden import database_config

primary = database_config(
    database_name="primary",
    default=True,
    database_type="sqlite",
    database_url_sync="sqlite:///./app.db",
    model_paths=["app.models"],
)
```

## Complete Production Example

```python
# dbwarden.py
import os
from dbwarden import database_config

primary = database_config(
    database_name="primary",
    default=True,
    database_type="postgresql",
    database_url_sync=os.getenv("DATABASE_URL"),
    dev_database_type="sqlite",
    dev_database_url="sqlite:///./dev.db",
    model_paths=["app.models"],
    secure_values=True,
)
```

## What's Next?

- **[Concepts](concepts.md)** - Understand how configuration works
- **[Connection URLs](connection-urls.md)** - Learn URL formats for different databases
- **[Dev Mode](dev-mode.md)** - Deep dive into dev workflows
- **[Production Patterns](production-patterns.md)** - Real-world examples
