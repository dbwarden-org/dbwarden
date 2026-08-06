# Multi-Database

Configure and manage multiple databases in a single project.

## When to Use Multiple Databases

Common scenarios:
- **Microservices** - Each service has its own database
- **Read/Write Split** - Primary for writes, replica for reads
- **Domain Separation** - Transactions, analytics, logs in separate databases
- **Legacy Integration** - New and old databases coexist
- **Multi-Tenancy** - One database per tenant

## Basic Setup

Configure each database with `database_config()`:

```python
# dbwarden.py
from dbwarden import database_config

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
    database_type="postgresql",
    database_url_sync="postgresql://localhost/analytics",
    model_paths=["app.models.analytics"],
)

# Logging database
logging = database_config(
    database_name="logging",
    database_type="postgresql",
    database_url_sync="postgresql://localhost/logs",
    model_paths=["app.models.logging"],
)
```

## Model Organization

### Pattern 1: Separate Modules

```
app/
  models/
    primary/
      __init__.py
      user.py
      order.py
    analytics/
      __init__.py
      event.py
      metric.py
    logging/
      __init__.py
      audit_log.py
```

Configuration:

```python
primary = database_config(
    database_name="primary",
    model_paths=["app.models.primary"],
    ...
)

analytics = database_config(
    database_name="analytics",
    model_paths=["app.models.analytics"],
    ...
)

logging = database_config(
    database_name="logging",
    model_paths=["app.models.logging"],
    ...
)
```

### Pattern 2: Shared Base Classes

```python
# app/models/base.py
from sqlalchemy.orm import DeclarativeBase

class PrimaryBase(DeclarativeBase):
    pass

class AnalyticsBase(DeclarativeBase):
    pass

# app/models/primary/user.py
from app.models.base import PrimaryBase

class User(PrimaryBase):
    __tablename__ = "users"
    ...

# app/models/analytics/event.py
from app.models.base import AnalyticsBase

class Event(AnalyticsBase):
    __tablename__ = "events"
    ...
```

## CLI Usage

### Target Specific Database

```bash
# Migrate primary
$ dbwarden migrate --database primary

# Migrate analytics
$ dbwarden migrate --database analytics

# Status for logging
$ dbwarden status --database logging
```

### Target All Databases

```bash
# Migrate all
$ dbwarden migrate --all

# Status for all
$ dbwarden status --all

# Rollback all
$ dbwarden rollback --all
```

### Default Database

The database with `default=True` is used when `--database` is omitted:

```bash
# These are equivalent when primary is default:
$ dbwarden migrate
$ dbwarden migrate --database primary
```

## Migration Directories

Each database has its own migration directory:

```
migrations/
  primary/
    0001_create_users.sql
    0002_create_orders.sql
  analytics/
    0001_create_events.sql
    0002_create_metrics.sql
  logging/
    0001_create_audit_logs.sql
```

Configure custom directories:

```python
primary = database_config(
    database_name="primary",
    migrations_dir="migrations/primary",  # Custom path
    ...
)
```

## Independent Migration Histories

Each database maintains its own migration history:

```bash
# Check primary history
$ dbwarden history --database primary
Applied Migrations (primary)
  0001_create_users (2024-01-15 10:30:00)
  0002_create_orders (2024-01-16 11:00:00)

# Check analytics history
$ dbwarden history --database analytics
Applied Migrations (analytics)
  0001_create_events (2024-01-15 10:35:00)
```

Migrations are **completely independent** - you can migrate one database without affecting others.

## Dev Mode with Multiple Databases

Configure dev mode for each database:

```python
primary = database_config(
    database_name="primary",
    default=True,
    database_type="postgresql",
    database_url_sync="postgresql://localhost/main",
    dev_database_type="sqlite",
    dev_database_url="sqlite:///./dev_primary.db",
    model_paths=["app.models.primary"],
)

analytics = database_config(
    database_name="analytics",
    database_type="postgresql",
    database_url_sync="postgresql://localhost/analytics",
    dev_database_type="sqlite",
    dev_database_url="sqlite:///./dev_analytics.db",
    model_paths=["app.models.analytics"],
)
```

Use dev mode:

```bash
# Dev mode for all databases
$ dbwarden --dev migrate --all

# Dev mode for specific database
$ dbwarden --dev migrate --database analytics
```

## Common Patterns

### Pattern 1: Read/Write Split

```python
primary = database_config(
    database_name="primary",
    default=True,
    database_type="postgresql",
    database_url_sync="postgresql://primary-host/myapp",
    model_paths=["app.models"],
)

replica = database_config(
    database_name="replica",
    database_type="postgresql",
    database_url_sync="postgresql://replica-host/myapp",
    model_paths=["app.models"],  # Same models
    overlap_models=True,          # Allow overlap
)
```

**Note:** Run migrations only against primary; replica replicates automatically.

### Pattern 2: Domain Separation

```python
# Transactions
transactions = database_config(
    database_name="transactions",
    default=True,
    database_type="postgresql",
    database_url_sync="postgresql://localhost/transactions",
    model_paths=["app.models.transactions"],
)

# Analytics
analytics = database_config(
    database_name="analytics",
    database_type="clickhouse",
    database_url_sync="http://localhost:8123/analytics",
    model_paths=["app.models.analytics"],
)

# Audit logs
audit = database_config(
    database_name="audit",
    database_type="postgresql",
    database_url_sync="postgresql://localhost/audit",
    model_paths=["app.models.audit"],
)
```

### Pattern 3: Multi-Tenant

```python
tenants = ["tenant_a", "tenant_b", "tenant_c"]

for tenant in tenants:
    db = database_config(
        database_name=tenant,
        default=(tenant == "tenant_a"),
        database_type="postgresql",
        database_url_sync=f"postgresql://localhost/{tenant}",
        model_paths=["app.models"],  # Same models for all tenants
    )
```

## Validation Rules

### Required: `model_paths`

When you have multiple databases, each **must** specify `model_paths`:

```python
#  Error: model_paths required
analytics = database_config(
analytics = database_config(database_name="analytics", ...)  # Missing model_paths

#  Correct
primary = database_config(
    database_name="primary",
    model_paths=["app.models.primary"],
    ...
)
analytics = database_config(
    database_name="analytics",
    model_paths=["app.models.analytics"],
    ...
)
```

### No Overlap (Default)

Model paths cannot overlap:

```python
#  Error: overlap detected
primary = database_config(
    database_name="primary",
    model_paths=["app.models"],
    ...
)
analytics = database_config(
    database_name="analytics",
    model_paths=["app.models"],  # Same path
    ...
)
```

### Allow Overlap

For read replicas or shared models:

```python
primary = database_config(
    database_name="primary",
    model_paths=["app.models"],
    overlap_models=True,  #  Allow overlap
    ...
)
replica = database_config(
    database_name="replica",
    model_paths=["app.models"],
    overlap_models=True,  #  Allow overlap
    ...
)
```

## Troubleshooting

### "model_paths is required"

**Solution:** Add `model_paths` to all databases:

```python
primary = database_config(
    database_name="primary",
    model_paths=["app.models.primary"],  #  Add this
    ...
)
```

### "model_paths overlap detected"

**Solution 1:** Use separate paths:
```python
model_paths=["app.models.primary"]
model_paths=["app.models.analytics"]
```

**Solution 2:** Allow overlap:
```python
overlap_models=True
```

### Wrong database targeted

**Check default:**
```bash
$ dbwarden settings show  # Shows which is default
```

**Be explicit:**
```bash
$ dbwarden migrate --database analytics  # Specify database
```

## What's Next?

- **[Production Patterns](production-patterns.md)** - Deploy multi-database apps
- **[Troubleshooting](troubleshooting.md)** - Common issues
