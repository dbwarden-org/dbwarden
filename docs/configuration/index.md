---
description: 'Learn how to configure DBWarden for single and multi-database setups: connection
  URLs, model discovery, dev mode with SQLite translation, credential management, and production
  patterns.'
---

# Configuration

DBWarden uses Python-based configuration with declarative `DbwardenDatabase`
classes as the default method. The equivalent `database_config()` function
alternative remains supported, and some plugins use it in their examples.

**One configuration source** for migrations, CLI tools, and runtime: no split configs.

## Quick Start

The simplest configuration possible:

```python
# dbwarden.py
from dbwarden import DbwardenDatabase

class Primary(DbwardenDatabase):
    database_name = "primary"
    default = True
    database_type = "sqlite"
    database_url_sync = "sqlite:///./app.db"
```

That's it! **4 parameters** to get started.

Run your first migration:

```bash
$ dbwarden init
$ dbwarden make-migrations "initial schema"
$ dbwarden migrate
```

## Learning Path

### New to DBWarden?
Start here to understand configuration basics:

1. **[Quick Start](quick-start.md)** - Your first configuration in 2 minutes
2. **[Concepts](concepts.md)** - How configuration works
3. **[Connection URLs](connection-urls.md)** - Database connection formats

### Building Your Configuration
Learn specific features:

- **[Model Discovery](model-discovery.md)** - How DBWarden finds your SQLAlchemy models
- **[Dev Mode](dev-mode.md)** - Local development with SQLite
- **[Multi-Database](multi-database.md)** - Configure multiple databases

### Production Ready
Deploy with confidence:

- **[Production Patterns](production-patterns.md)** - Real-world examples
- **[Troubleshooting](troubleshooting.md)** - Common issues and solutions

### Complete Reference
- **[Configuration API](../reference/configuration-api.md)** - Complete function signature and parameters

## Key Features

###  Simple Configuration

Define once, use everywhere:

```python
primary = database_config(
    database_name="primary",
    default=True,
    database_type="postgresql",
    database_url_sync="postgresql://localhost/myapp",
    model_paths=["app.models"],
)
```

###  Dev Mode

Use SQLite locally, PostgreSQL in production:

```python
primary = database_config(
    database_name="primary",
    default=True,
    database_type="postgresql",
    database_url_sync="postgresql://localhost/myapp",
    dev_database_type="sqlite",
    dev_database_url="sqlite:///./dev.db",
)
```

Run commands with `--dev`:

```bash
$ dbwarden --dev migrate
$ dbwarden --dev status
```

###  Multi-Database

Configure as many databases as you need:

```python
# Primary database
primary = database_config(
    database_name="primary",
    default=True,
    database_type="postgresql",
    database_url_sync="postgresql://localhost/main",
    model_paths=["app.models.primary"],
)

# Analytics database
analytics = database_config(
    database_name="analytics",
    database_type="clickhouse",
    database_url_sync="http://localhost:8123/analytics",
    model_paths=["app.models.analytics"],
)
```

###  Security First

Keep credentials out of code:

```python
import os

primary = database_config(
    database_name="primary",
    default=True,
    database_type="postgresql",
    database_url_sync=os.getenv("DATABASE_URL"),
    secure_values=True,  # Hide credentials in output
)
```

###  Validation

DBWarden validates your configuration:

-  Exactly one `default=True`
-  Unique database names
-  No duplicate URLs
-  Required `model_paths` for multi-database
-  Consistent dev mode configuration

## Configuration Loading

DBWarden discovers your configuration automatically:

1. **Looks for `dbwarden.py`** in current directory or parents
2. **Checks `DBWARDEN_CONFIG_MODULE`** environment variable
3. **Scans for declarative classes and `database_config()` calls** in your codebase (full project tree walk)
4. **Looks for `warden.toml`** as an alternative TOML-based config file

`dbwarden.py` is the default convention and the file created by `dbwarden init`. The default class API or the supported `database_config(...)` function alternative can live in any discovered Python file inside your project.

## Common Patterns

### Single Database (Minimal)

```python
from dbwarden import DbwardenDatabase

class Primary(DbwardenDatabase):
    database_name = "primary"
    default = True
    database_type = "postgresql"
    database_url_sync = "postgresql://localhost/myapp"
```

### With Dev Mode (Recommended)

```python
from dbwarden import DbwardenDatabase

class Primary(DbwardenDatabase):
    database_name = "primary"
    default = True
    database_type = "postgresql"
    database_url_sync = "postgresql://localhost/myapp"
    dev_database_type = "sqlite"
    dev_database_url = "sqlite:///./dev.db"
    model_paths = ["app.models"]
```

### Multiple Databases

```python
from dbwarden import DbwardenDatabase

# Primary
class Primary(DbwardenDatabase):
    database_name = "primary"
    default = True
    database_type = "postgresql"
    database_url_sync = "postgresql://localhost/main"
    model_paths = ["app.models.primary"]

# Analytics
class Analytics(DbwardenDatabase):
    database_name = "analytics"
    database_type = "postgresql"
    database_url_sync = "postgresql://localhost/analytics"
    model_paths = ["app.models.analytics"]
```

### Production with Environment Variables

```python
import os
from dbwarden import DbwardenDatabase

class Primary(DbwardenDatabase):
    database_name = "primary"
    default = True
    database_type = "postgresql"
    database_url_sync = os.getenv("DATABASE_URL")
    model_paths = ["app.models"]
    secure_values = True
```

## Why Python Configuration?

**vs TOML/YAML/INI:**
-  Type checking with your IDE
-  Dynamic configuration (loops, conditionals)
-  Environment variable integration
-  No schema mismatches
-  Can compute values

**vs Environment Variables Only:**
-  Version controlled
-  Self-documenting
-  Validation at load time
-  Multiple databases easy
-  Can reference code structures

## What's Next?

Ready to configure your first database? Start here:

- **[Quick Start](quick-start.md)** - Build your first configuration
- **[Concepts](concepts.md)** - Understand how it works
- **[Production Patterns](production-patterns.md)** - Real-world examples

Already familiar with configuration? Jump to:

- **[Connection URLs](connection-urls.md)** - URL format reference
- **[Troubleshooting](troubleshooting.md)** - Common issues
- **[Configuration API](../reference/configuration-api.md)** - Complete reference
