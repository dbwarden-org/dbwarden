# Model Discovery

Learn how DBWarden discovers your SQLAlchemy models for migration generation.

## What Is Model Discovery?

Model discovery is the process where DBWarden:
1. Imports Python modules
2. Finds SQLAlchemy model classes
3. Extracts table metadata
4. Uses metadata to generate migrations

## The `model_paths` Parameter

### Basic Usage

```python
primary = database_config(
    database_name="primary",
    default=True,
    database_type="postgresql",
    database_url_sync="postgresql://localhost/myapp",
    model_paths=["app.models"],  #  Discover models here
)
```

### What Gets Discovered

DBWarden looks for classes that inherit from:
- `DeclarativeBase` (SQLAlchemy 2.0+)
- `declarative_base()` return value (SQLAlchemy 1.4)

**Example models:**

```python
# app/models.py
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy import String, Integer

class Base(DeclarativeBase):
    pass

class User(Base):  #  Discovered
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email: Mapped[str] = mapped_column(String(255))

class Order(Base):  #  Discovered
    __tablename__ = "orders"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
```

## When Is It Required?

### Single Database (Optional)

For single-database projects, `model_paths` is optional:

```python
primary = database_config(
    database_name="primary",
    default=True,
    database_type="sqlite",
    database_url_sync="sqlite:///./app.db",
    # No model_paths - DBWarden scans entire codebase
)
```

DBWarden will scan your entire codebase for models.

Even for single-database projects, specifying `model_paths` makes discovery faster and more predictable.

### Multiple Databases (Required)

For multi-database projects, `model_paths` is **required** for each database:

```python
primary = database_config(
    database_name="primary",
    default=True,
    database_type="postgresql",
    database_url_sync="postgresql://localhost/main",
    model_paths=["app.models.primary"],  #  Required
)

analytics = database_config(
    database_name="analytics",
    database_type="postgresql",
    database_url_sync="postgresql://localhost/analytics",
    model_paths=["app.models.analytics"],  #  Required
)
```

**Why required?** To prevent ambiguity about which models belong to which database.

## Discovery Algorithm

### Step 1: Import Modules

DBWarden imports each module in `model_paths`:

```python
model_paths=["app.models", "app.legacy.models"]
```

Becomes:

```python
import app.models
import app.legacy.models
```

### Step 2: Recursive Discovery

For each imported module, DBWarden recursively imports submodules:

```
app/
  models/
    __init__.py       #  Imported
    user.py           #  Imported
    order.py          #  Imported
    admin/
      __init__.py     #  Imported
      admin_user.py   #  Imported
```

### Step 3: Find Model Classes

For each module, DBWarden inspects all classes and finds those inheriting from `DeclarativeBase`.

### Step 4: Extract Metadata

For each model class, DBWarden extracts:
- Table name
- Columns (name, type, constraints)
- Indexes
- Foreign keys
- Check constraints
- Unique constraints

## Module Path Examples

### Single Module

```python
model_paths=["app.models"]
```

Discovers models from:
- `app/models.py` (if it's a file)
- `app/models/__init__.py` (if it's a package)
- `app/models/*.py` (all submodules)

### Multiple Modules

```python
model_paths=["app.models", "app.legacy"]
```

Discovers models from both `app.models` and `app.legacy`.

### Nested Modules

```python
model_paths=["app.models.api", "app.models.admin"]
```

Discovers models from:
- `app/models/api.py` or `app/models/api/*.py`
- `app/models/admin.py` or `app/models/admin/*.py`

### Absolute vs Relative

**Absolute (recommended):**
```python
model_paths=["app.models"]  # From project root
```

**Not supported:**
```python
model_paths=["./models"]  # Relative paths don't work
model_paths=["models"]     # May work if on PYTHONPATH
```

## Common Patterns

### Pattern 1: Single Module

```
app/
  models.py    # All models in one file
```

```python
model_paths=["app.models"]
```

### Pattern 2: Module Package

```
app/
  models/
    __init__.py
    user.py
    order.py
    product.py
```

```python
model_paths=["app.models"]
```

### Pattern 3: Multi-Database

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
```

```python
# Primary database
model_paths=["app.models.primary"]

# Analytics database  
model_paths=["app.models.analytics"]
```

### Pattern 4: Legacy + New

```
app/
  models/      # New models
    __init__.py
    user.py
  legacy/      # Legacy models
    models.py
```

```python
model_paths=["app.models", "app.legacy.models"]
```

## Model Path Validation

### No Overlap (Default)

By default, model paths cannot overlap between databases:

```python
#  Error: overlap detected
primary = database_config(
    database_name="primary",
    model_paths=["app.models"],
)

analytics = database_config(
    database_name="analytics",
    model_paths=["app.models"],  # Same path!
)
```

### Allow Overlap

If models genuinely belong to multiple databases:

```python
primary = database_config(
    database_name="primary",
    model_paths=["app.shared"],
    overlap_models=True,  #  Allow overlap
)

analytics = database_config(
    database_name="analytics",
    model_paths=["app.shared"],
    overlap_models=True,  #  Allow overlap
)
```

Both databases will include the same tables. Make sure this is intentional.

## Troubleshooting

### "No SQLAlchemy models found"

**Symptom:** DBWarden can't find your models.

**Causes:**

1. **Models not imported**

```python
# app/models/__init__.py
#  Wrong - models not imported
from sqlalchemy.orm import DeclarativeBase

class Base(DeclarativeBase):
    pass

#  Correct - import models
from app.models.user import User
from app.models.order import Order
```

2. **Wrong module path**

```python
#  Wrong
model_paths=["models"]  # Not on PYTHONPATH

#  Correct
model_paths=["app.models"]
```

3. **Circular imports**

```python
# app/models/user.py
from app.models.order import Order  #  Circular import

# app/models/order.py
from app.models.user import User  #  Circular import
```

**Solution:** Use forward references:

```python
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.models.order import Order
```

### "model_paths is required"

**Symptom:** Error when running commands with multiple databases.

**Cause:** Multiple databases configured without `model_paths`.

**Solution:** Add `model_paths` to each database:

```python
primary = database_config(
    database_name="primary",
    model_paths=["app.models.primary"],  #  Add this
    ...
)

analytics = database_config(
    database_name="analytics",
    model_paths=["app.models.analytics"],  #  Add this
    ...
)
```

### "model_paths overlap detected"

**Symptom:** Two databases have overlapping model paths.

**Cause:** Same path used for multiple databases.

**Solution 1:** Use separate paths:

```python
primary = database_config(
    database_name="primary",
    model_paths=["app.models.primary"],  # Different path
    ...
)

analytics = database_config(
    database_name="analytics",
    model_paths=["app.models.analytics"],  # Different path
    ...
)
```

**Solution 2:** Allow overlap (if intentional):

```python
primary = database_config(
    database_name="primary",
    model_paths=["app.shared"],
    overlap_models=True,  #  Allow overlap
    ...
)

analytics = database_config(
    database_name="analytics",
    model_paths=["app.shared"],
    overlap_models=True,  #  Allow overlap
    ...
)
```

### Import Errors

**Symptom:** `ModuleNotFoundError` or `ImportError` when running commands.

**Cause:** DBWarden tries to import module but it doesn't exist.

**Solution:** Verify the module path:

```bash
python -c "import app.models"  # Test import
```

If import fails, fix your module structure or PYTHONPATH.

## Performance Considerations

### Slow Discovery

If discovery is slow, reduce the search space:

**Before (slow):**
```python
model_paths=["app"]  # Scans entire app
```

**After (fast):**
```python
model_paths=["app.models"]  # Only scans models
```

### Import Side Effects

Models should be pure:

```python
#  Bad - side effects on import
class User(Base):
    __tablename__ = "users"
    ...

print("User model loaded!")  # Side effect

#  Good - no side effects
class User(Base):
    __tablename__ = "users"
    ...
```

## Advanced: Dynamic Model Paths

You can compute `model_paths` dynamically:

```python
import os

environment = os.getenv("ENV", "dev")

if environment == "production":
    model_paths = ["app.models.production"]
else:
    model_paths = ["app.models.dev"]

primary = database_config(
    database_name="primary",
    default=True,
    database_type="postgresql",
    database_url_sync="...",
    model_paths=model_paths,
)
```

## What's Next?

- **[Dev Mode](dev-mode.md)** - Local development workflows
- **[Multi-Database](multi-database.md)** - Organize multi-database models
- **[Troubleshooting](troubleshooting.md)** - Common configuration issues
