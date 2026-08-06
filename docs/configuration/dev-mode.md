# Dev Mode

Use SQLite for local development and PostgreSQL in production with the same codebase.

## What Is Dev Mode?

Dev mode lets you configure **two database URLs**:
- **Production URL** - Used by default
- **Dev URL** - Used when you pass `--dev` flag

```python
primary = database_config(
    database_name="primary",
    default=True,
    database_type="postgresql",
    database_url_sync="postgresql://localhost/myapp",    # Production
    dev_database_type="sqlite",
    dev_database_url="sqlite:///./dev.db",           # Development
)
```

Run commands with `--dev`:

```bash
$ dbwarden --dev migrate        # Uses SQLite
$ dbwarden --dev status         # Uses SQLite
$ dbwarden migrate              # Uses PostgreSQL
```

## Why Use Dev Mode?

### Speed

SQLite is **much faster** for local development:
- No network latency
- No authentication overhead
- File-based, not server-based
- Instant startup

### Simplicity

No PostgreSQL server required:
- No Docker setup
- No installation
- No configuration
- Works on all platforms

### Safety

Can't accidentally affect production:
- Dev database is a local file
- Each developer has their own database
- Easy to reset (`rm dev.db`)
- No shared state

### Portability

Easy to share between developers:
- One config file works everywhere
- No server setup instructions
- Fresh developers can start immediately

## Basic Setup

### Step 1: Configure Dev Database

```python
# dbwarden.py
from dbwarden import database_config

primary = database_config(
    database_name="primary",
    default=True,
    database_type="postgresql",
    database_url_sync="postgresql://localhost/myapp",
    dev_database_type="sqlite",                    #  Add this
    dev_database_url="sqlite:///./dev.db",         #  Add this
    model_paths=["app.models"],
)
```

### Step 2: Use Dev Mode

```bash
# Development workflow
$ dbwarden --dev make-migrations "create users"
$ dbwarden --dev migrate
$ dbwarden --dev status

# Production workflow
$ dbwarden make-migrations "create users"
$ dbwarden migrate
$ dbwarden status
```

## Dev Mode Workflow

### Daily Development

```bash
# Morning: pull latest code
git pull

# Run migrations against dev database
$ dbwarden --dev migrate

# Work on features...
# Add new models

# Generate migration
$ dbwarden --dev make-migrations "add orders table"

# Test migration
$ dbwarden --dev migrate

# Verify
$ dbwarden --dev status

# Commit
git add migrations/primary/0002_add_orders_table.sql
git commit -m "Add orders table"
```

### Testing Rollbacks

```bash
# Apply migration
$ dbwarden --dev migrate

# Test rollback
$ dbwarden --dev rollback

# Re-apply
$ dbwarden --dev migrate
```

### Fresh Start

Reset your dev database anytime:

```bash
# Delete dev database
rm dev.db

# Re-run migrations
$ dbwarden --dev migrate
```

## Production Workflow

Dev mode only affects **local development**. Production uses the main URL:

### CI/CD Pipeline

```yaml
# .github/workflows/deploy.yml
- name: Run migrations
  run: dbwarden migrate  # No --dev flag
  env:
    DATABASE_URL: ${{ secrets.DATABASE_URL }}
```

### Production Deployment

```bash
# Staging
$ dbwarden migrate --database primary

# Production
$ dbwarden migrate --database primary
```

Dev mode is **never used** in CI/CD or production.

## SQLite Limitations

### What Works

Most features work in SQLite:
-  Tables, columns, indexes
-  Primary keys, foreign keys
-  Unique constraints
-  Basic data types
-  Transactions

### What Doesn't Work

Some PostgreSQL features aren't available in SQLite:
-  Advanced types (JSONB, arrays, enums)
-  Partial indexes
-  Generated columns (in older SQLite)
-  Multiple schemas
-  Concurrent writes

### Translation

DBWarden **doesn't translate** SQL between databases. Your migrations should work on both SQLite and PostgreSQL.

**Approach 1:** Write portable SQL

```sql
--  Works on both
CREATE TABLE users (
    id INTEGER PRIMARY KEY,
    email VARCHAR(255) NOT NULL
);

--  PostgreSQL-specific
CREATE TABLE users (
    id SERIAL PRIMARY KEY,
    email VARCHAR(255) NOT NULL,
    metadata JSONB
);
```

**Approach 2:** Use PostgreSQL for dev too

```python
primary = database_config(
    database_name="primary",
    default=True,
    database_type="postgresql",
    database_url_sync="postgresql://localhost/myapp",
    dev_database_type="postgresql",                         # Same as prod
    dev_database_url="postgresql://localhost/myapp_dev",    # Different database
)
```

## Environment-Based Configuration

### Automatic Dev Mode

Use environment variables to automatically detect dev:

```python
import os

is_dev = os.getenv("ENV", "dev") in ["dev", "development", "local"]

if is_dev:
    database_url = "sqlite:///./dev.db"
    database_type = "sqlite"
else:
    database_url = os.getenv("DATABASE_URL")
    database_type = "postgresql"

primary = database_config(
    database_name="primary",
    default=True,
    database_type=database_type,
    database_url_sync=database_url,
)
```

Run commands:

```bash
# Dev
ENV=dev dbwarden migrate

# Production
ENV=production dbwarden migrate
```

## Multiple Dev Databases

If you have multiple databases, configure dev mode for each:

```python
# Primary database
primary = database_config(
    database_name="primary",
    default=True,
    database_type="postgresql",
    database_url_sync="postgresql://localhost/main",
    dev_database_type="sqlite",
    dev_database_url="sqlite:///./dev_primary.db",
    model_paths=["app.models.primary"],
)

# Analytics database
analytics = database_config(
    database_name="analytics",
    database_type="postgresql",
    database_url_sync="postgresql://localhost/analytics",
    dev_database_type="sqlite",
    dev_database_url="sqlite:///./dev_analytics.db",
    model_paths=["app.models.analytics"],
)
```

Run against all dev databases:

```bash
$ dbwarden --dev migrate --all
$ dbwarden --dev status --all
```

## Common Patterns

### Pattern 1: SQLite for Dev, PostgreSQL for Prod (Recommended)

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

**Pros:**
- Fast local iteration
- No server setup
- Easy to reset

**Cons:**
- SQL must be portable
- Some features unavailable in dev

### Pattern 2: PostgreSQL for Both

```python
primary = database_config(
    database_name="primary",
    default=True,
    database_type="postgresql",
    database_url_sync="postgresql://prod-host/myapp",
    dev_database_type="postgresql",
    dev_database_url="postgresql://localhost/myapp_dev",
)
```

**Pros:**
- Identical environments
- Use all PostgreSQL features
- Catches more bugs in dev

**Cons:**
- Requires PostgreSQL server locally
- Slower than SQLite

### Pattern 3: Dynamic Based on Environment

```python
import os

environment = os.getenv("ENV", "dev")

if environment == "production":
    database_url = os.getenv("DATABASE_URL")
    database_type = "postgresql"
elif environment == "staging":
    database_url = os.getenv("STAGING_DATABASE_URL")
    database_type = "postgresql"
else:
    database_url = "sqlite:///./dev.db"
    database_type = "sqlite"

primary = database_config(
    database_name="primary",
    default=True,
    database_type=database_type,
    database_url_sync=database_url,
)
```

## Testing with Dev Mode

### Unit Tests

Use SQLite for fast unit tests:

```python
# tests/conftest.py
import pytest
from sqlalchemy import create_engine
from app.models import Base

@pytest.fixture(scope="function")
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    yield engine
    Base.metadata.drop_all(engine)
```

### Integration Tests

Use `--dev` for integration tests:

```bash
# Run integration tests
$ dbwarden --dev migrate
pytest tests/integration/
```

## Troubleshooting

### "SQL syntax error in SQLite"

**Cause:** Using PostgreSQL-specific SQL.

**Solution:** Make SQL portable or use PostgreSQL for dev:

```python
dev_database_type="postgresql"
dev_database_url="postgresql://localhost/myapp_dev"
```

### "dev_database_url is required"

**Cause:** Set `dev_database_type` without `dev_database_url`.

**Solution:** Add both:

```python
dev_database_type="sqlite"
dev_database_url="sqlite:///./dev.db"  #  Add this
```

### Dev database not updating

**Cause:** Forgot `--dev` flag.

**Solution:** Use `--dev`:

```bash
$ dbwarden --dev migrate  #  Add --dev
```

## What's Next?

- **[Multi-Database](multi-database.md)** - Multiple databases with dev mode
- **[Production Patterns](production-patterns.md)** - Deploy to production
- **[Troubleshooting](troubleshooting.md)** - Common issues
