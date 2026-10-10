# Declarative data migrations: basics

The smallest useful data migration: managed rows declared beside a model,
rows loaded from a static CSV file, a derived column, and a validation rule.
All three compile into one versioned SQL migration and one frozen artifact.

```text
app/models.py
  Country.Data.managed_rows  ──▶ countries   (rows written inline in Python)
  Currency.Data.managed_rows ──▶ currencies  (rows loaded from data/currencies.csv)
  User.Data.transformations  ──▶ users.slug  (lower(trim(name)))
  User.Data.validations      ──▶ users.email (must not be null)
```

## Prerequisites

- Python 3.12+
- `dbwarden` on your `PATH` (e.g. `uv add dbwarden sqlalchemy`, then run
  the scripts from an activated environment). The scripts also honour
  `DBWARDEN` and `PYTHON` overrides.

## Quick start

```bash
bash scripts/01-generate-and-apply.sh   # init, generate, apply, verify
bash scripts/02-inspect.sh              # render, describe, diff the data
```

## What each file is

| Path | Purpose |
|------|---------|
| `dbwarden.py` | Registers the SQLite database and the data paths. |
| `app/models.py` | Models with inner `Data(DataMeta)` declarations. |
| `data/currencies.csv` | Static source read at compile time. |
| `scripts/` | Runnable command sequences. |

## Key declarations

```python
class Country(Base):
    __tablename__ = "countries"
    code = Column(String(2), primary_key=True)
    name = Column(String(100), nullable=False)

    class Data(DataMeta):
        managed_rows = rows(
            key="code",
            rows=[{"code": "UY", "name": "Uruguay"}],
            owned_columns=["name"],
            rollback="restore_previous",
        )
```

```python
class User(Base):
    ...
    class Data(DataMeta):
        managed_rows = rows(
            key="id",
            rows=[{"id": 1, "email": "ada@example.com", "name": "  Ada Lovelace  "}],
            owned_columns=["email", "name"],
            rollback="restore_previous",
        )
        transformations = [
            derive("slug", func.lower(func.trim(col("name"))), rollback="clear"),
        ]
        validations = [
            validate(col("email").is_not(None), message="email is required"),
        ]
```

## Commands demonstrated

| Script | Commands |
|--------|----------|
| `01-generate-and-apply.sh` | `init`, `make-migrations`, `migrate --force`, `check --data` |
| `02-inspect.sh` | `data render`, `data describe`, `diff --data` |
