# Troubleshooting

Solutions to common configuration issues.

## "No configuration found"

### Symptom

```
DBWardenConfigError: No configuration found
```

### Causes & Solutions

**Cause 1: No `dbwarden.py` file**

```bash
# Check if file exists
ls dbwarden.py
```

**Solution:** Create `dbwarden.py`:

```bash
$ dbwarden init
```

**Cause 2: Wrong directory**

DBWarden looks in current directory and parents.

**Solution:** Navigate to project root:

```bash
cd /path/to/project
$ dbwarden migrate
```

**Cause 3: No `database_config()` calls**

**Solution:** Add configuration:

```python
# dbwarden.py
from dbwarden import database_config

primary = database_config(
    database_name="primary",
    default=True,
    database_type="sqlite",
    database_url_sync="sqlite:///./app.db",
)
```

**Cause 4: Import error in config file**

```python
# dbwarden.py
from app.models import Base  #  Import fails
```

**Solution:** Fix imports or use lazy loading:

```python
# Don't import models in config file
primary = database_config(
    database_name="primary",
    model_paths=["app.models"],  #  Use model_paths instead
    ...
)
```

## "Exactly one default=True required"

### Symptom

```
ConfigurationError: Exactly one default=True required
```

### Causes & Solutions

**Cause 1: No default database**

```python
#  Wrong
analytics = database_config(
analytics = database_config(database_name="analytics", default=False, ...)
```

**Solution:** Set one database as default:

```python
#  Correct
analytics = database_config(
analytics = database_config(database_name="analytics", default=False, ...)
```

**Cause 2: Multiple defaults**

```python
#  Wrong
analytics = database_config(
analytics = database_config(database_name="analytics", default=True, ...)
```

**Solution:** Only one default:

```python
#  Correct
analytics = database_config(
analytics = database_config(database_name="analytics", ...)  # default=False implied
```

## "Duplicate database_name"

### Symptom

```
ConfigurationError: Duplicate database_name 'primary'
```

### Cause

Same `database_name` used twice:

```python
primary = database_config(
primary = database_config(database_name="primary", ...)  #  Duplicate
```

### Solution

Use unique names:

```python
analytics = database_config(
analytics = database_config(database_name="analytics", ...)  #  Different name
```

## "No SQLAlchemy models found"

### Symptom

```
Warning: No SQLAlchemy models found
```

### Causes & Solutions

**Cause 1: Wrong `model_paths`**

```python
#  Wrong
model_paths=["models"]  # Not on PYTHONPATH
```

**Solution:** Use correct Python path:

```python
#  Correct
model_paths=["app.models"]
```

**Cause 2: Models not imported**

```python
# app/models/__init__.py
#  Wrong - models not imported
from sqlalchemy.orm import DeclarativeBase

class Base(DeclarativeBase):
    pass
```

**Solution:** Import models:

```python
# app/models/__init__.py
#  Correct
from sqlalchemy.orm import DeclarativeBase
from app.models.user import User
from app.models.order import Order

class Base(DeclarativeBase):
    pass
```

**Cause 3: Missing `model_paths`**

**Solution:** Add `model_paths`:

```python
primary = database_config(
    database_name="primary",
    model_paths=["app.models"],  #  Add this
    ...
)
```

**Cause 4: Circular imports**

```python
# app/models/user.py
from app.models.order import Order  #  Circular

# app/models/order.py
from app.models.user import User  #  Circular
```

**Solution:** Use TYPE_CHECKING:

```python
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.models.order import Order
```

## "model_paths is required"

### Symptom

```
ConfigurationError: model_paths is required when more than one database is configured
```

### Cause

Multiple databases without `model_paths`:

```python
#  Wrong
analytics = database_config(
analytics = database_config(database_name="analytics", ...)  # No model_paths
```

### Solution

Add `model_paths` to all databases:

```python
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

## "model_paths overlap detected"

### Symptom

```
ConfigurationError: model_paths overlap detected between 'primary' and 'analytics'
```

### Cause

Same model paths for different databases:

```python
#  Wrong
primary = database_config(
    database_name="primary",
    model_paths=["app.models"],
    ...
)
analytics = database_config(
    database_name="analytics",
    model_paths=["app.models"],  #  Same path
    ...
)
```

### Solutions

**Solution 1: Use separate paths**

```python
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

**Solution 2: Allow overlap (if intentional)**

```python
#  Correct for read replicas
primary = database_config(
    database_name="primary",
    model_paths=["app.models"],
    overlap_models=True,
    ...
)
replica = database_config(
    database_name="replica",
    model_paths=["app.models"],
    overlap_models=True,
    ...
)
```

## "model_tables overlap detected"

### Symptom

```
ConfigurationError: model_tables overlap detected: table 'users' in 'primary' is also in 'analytics'
```

### Cause

Two databases have `model_tables` lists that share table names:

```python
#  Wrong
primary = database_config(
    database_name="primary",
    model_paths=["app.models"],
    model_tables=["users", "posts"],
    ...
)
analytics = database_config(
    database_name="analytics",
    model_paths=["other_models"],
    model_tables=["users"],  # 'users' already owned by primary
    ...
)
```

### Solutions

**Solution 1: Remove duplicate table name**

```python
#  Correct
analytics = database_config(
    database_name="analytics",
    model_paths=["other_models"],
    model_tables=["analytics_events"],  # No overlap with primary
    ...
)
```

**Solution 2: Allow overlap (if intentional)**

```python
#  Correct for shared tables
analytics = database_config(
    database_name="analytics",
    model_paths=["other_models"],
    model_tables=["users", "analytics_events"],
    overlap_models=True,  # Allow overlap
    ...
)
```

## "dev_database_url is required"

### Symptom

```
ConfigurationError: dev_database_url is required when dev_database_type is set
```

### Cause

Set `dev_database_type` without `dev_database_url`:

```python
#  Wrong
primary = database_config(
    database_name="primary",
    dev_database_type="sqlite",
    # Missing dev_database_url
    ...
)
```

### Solution

Add both dev parameters:

```python
#  Correct
primary = database_config(
    database_name="primary",
    dev_database_type="sqlite",
    dev_database_url="sqlite:///./dev.db",  #  Add this
    ...
)
```

## Connection Errors

### "could not connect to server"

**Cause:** Database server not running or unreachable.

**Solutions:**

1. **Check database is running:**

```bash
# PostgreSQL
sudo systemctl status postgresql

# Docker
docker ps | grep postgres
```

2. **Check connection URL:**

```python
# Verify host, port, credentials
database_url_sync="postgresql://user:pass@localhost:5432/myapp"
```

3. **Test connection:**

```bash
# PostgreSQL
psql -h localhost -U user -d myapp

# MySQL
mysql -h localhost -u user -p myapp
```

### "authentication failed"

**Cause:** Wrong credentials.

**Solutions:**

1. **Check credentials:**

```bash
# PostgreSQL
psql -h localhost -U user -d myapp
```

2. **Verify environment variable:**

```bash
echo $DATABASE_URL
```

3. **URL encode special characters:**

```python
from urllib.parse import quote_plus

password = "p@ss:word"
encoded = quote_plus(password)  # "p%40ss%3Aword"
```

### "database does not exist"

**Cause:** Database not created.

**Solution:** Create database:

```sql
-- PostgreSQL
CREATE DATABASE myapp;

-- MySQL
CREATE DATABASE myapp CHARACTER SET utf8mb4;
```

## Import Errors

### "ModuleNotFoundError"

**Cause:** Python can't find module.

**Solutions:**

1. **Check PYTHONPATH:**

```bash
export PYTHONPATH=/path/to/project:$PYTHONPATH
```

2. **Install package:**

```bash
uv add -e .  # Editable install
```

3. **Verify import:**

```bash
python -c "import app.models"
```

## Performance Issues

### Slow configuration loading

**Cause:** Large codebase scan.

**Solution:** Specify `model_paths`:

```python
#  Slow - scans everything
primary = database_config(

#  Fast - targeted scan
primary = database_config(
    database_name="primary",
    model_paths=["app.models"],
    ...
)
```

### Slow imports

**Cause:** Heavy imports in config file.

**Solution:** Avoid imports in `dbwarden.py`:

```python
#  Slow
from app.models import Base
from app.services import setup

#  Fast
from dbwarden import database_config

db = database_config(
```

## Debugging Tips

### Enable verbose output

```bash
$ dbwarden --verbose migrate
```

### Check configuration

```bash
# Show all configuration
$ dbwarden settings show

# Show specific database
$ dbwarden settings show --database primary
```

### Test imports

```bash
python -c "import dbwarden; print('OK')"
python -c "from dbwarden import database_config; print('OK')"
```

### Verify database connection

```bash
$ dbwarden check-db
$ dbwarden check-db --database primary
```

## What's Next?

- **[Configuration API Reference](../reference/configuration-api.md)** - Complete parameter docs
- **[Quick Start](quick-start.md)** - Start fresh with correct setup
