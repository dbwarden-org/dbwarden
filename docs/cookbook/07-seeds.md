# 7. Seeds

## What You'll Learn

- How to define code seeds using the `Seed` base class
- How to create and apply file-based SQL/Python seeds (legacy)
- How to list, apply, and roll back seeds
- How to auto-apply seeds after migrations

## Prerequisites

- Completed [Section 3](03-apply-and-inspect.md) (migrations applied, tables exist)
- `examples/core/` project
- Seed plugin installed: `dbwarden plugin add dbwarden-seeds`

## Step 1: Define a Code Seed

Code seeds are the recommended way to seed data. They live alongside your models and keep seed logic close to the schema it populates.

Create a seed file in your models directory (e.g. `models/seeds.py`):

```python
from dbwarden.seed import Seed

class CountrySeed(Seed):
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

Notice:

- **No `version`**: versions are auto-assigned (`C0001`, `C0002`, ...)
- **Model instances** in `rows`: your IDE gives full autocompletion on column names
- **Keyword arguments required**: SQLAlchemy 2.0's `DeclarativeBase` does not accept positional args; always use `Model(col=val)` syntax
- **`__seed_database__`**: route the seed to the correct database

### Logic-Based Seeds

Define a `generate(session)` method for programmatic data:

```python
class PermissionSeed(Seed):
    __seed_database__ = "primary"
    __seed_description__ = "load permissions"

    model = Permission

    @staticmethod
    def generate(session):
        for resource in ["users", "orders"]:
            for action in ["read", "write", "delete"]:
                session.add(Permission(name=f"{resource}:{action}"))
```

## Step 2: Apply Seeds

```bash
$ dbwarden seed apply --database primary
```

Output:

```
Applying code seed C0001: initial countries
```

Code seeds and file seeds are both discovered and applied together. Each seed version is tracked in `_dbwarden_seeds` and can only be applied once until rolled back.

## Step 3: List Applied Seeds

```bash
$ dbwarden seed list --database primary
```

Output:

```
Seeds for database 'primary':
  C0001  initial countries                 applied  2025-01-15 10:30:00  (code seed)
```

## Step 4: Auto-Apply Seeds After Migrations

Configure seeds to be applied automatically after `dbwarden migrate`:

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

Now running `dbwarden migrate` will also apply any pending seeds.

Or apply seeds once without changing config:

```bash
$ dbwarden migrate --apply-seeds
```

## Step 5: Traditional File Seeds (Legacy)

For complex multi-statement SQL, you can still use file-based seeds.

### Create a SQL Seed

```bash
$ dbwarden seed create "initial admin users" --database primary
```

This creates `seeds/V0001__initial_admin_users.sql`. Fill it with data:

```sql
INSERT INTO users (email, username, full_name, is_active, created_at)
VALUES ('admin@example.com', 'admin', 'Admin User', 1, CURRENT_TIMESTAMP);

INSERT INTO users (email, username, full_name, is_active, created_at)
VALUES ('moderator@example.com', 'moderator', 'Moderator User', 1, CURRENT_TIMESTAMP);
```

### Apply and List

```bash
$ dbwarden seed apply --database primary
$ dbwarden seed list --database primary
```

Output:

```
Seeds for database 'primary':
  C0001  initial countries                 applied  2025-01-15 10:30:00  (code seed)
  V0001  initial_admin_users              applied  2025-01-15 10:31:00
```

### Python File Seeds

```bash
$ dbwarden seed create "generate sample data" --database primary --type python
```

Creates `seeds/V0002__generate_sample_data.py` with a `seed(connection, session)` function.

## Step 6: Roll Back a Seed

```bash
$ dbwarden seed rollback --database primary --count 1
```

Seed rollback removes the tracking record, allowing the seed to be re-applied. It does **not** reverse the data changes; that is your responsibility if needed.

After rollback:

```
Seeds for database 'primary':
  C0001  initial countries                 applied  2025-01-15 10:30:00  (code seed)
  V0001  initial_admin_users              pending
```

## Step 7: Prune Orphaned Records

Remove tracking records for seed files that no longer exist on disk:

```bash
$ dbwarden seed list --prune
```

## Key Takeaways

- **Code seeds (`Seed` base class) are the recommended approach**: no manual versions, full IDE support, stays in sync with models
- `auto_apply_seeds: True` or `dbwarden migrate --apply-seeds` applies seeds automatically after migrations
- File seeds (`.sql` / `.py`) are still available for complex multi-statement SQL
- `seed list --prune` cleans up orphaned tracking records
- Seed rollback removes the tracking record; it does not undo data

## Related Documentation

- [Seeds Reference](../seeds.md)
- [`seed` command](../commands/seed.md)
- [CLI Reference: Seed Management](../cli-reference.md#seed-management)

## Next

[Section 8: Multi-Database](08-multi-database.md)
