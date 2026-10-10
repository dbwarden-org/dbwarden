# 6. Validation

A validation states a predicate that must hold for every row of a table. It runs
after data writes during a migration and again during `check --data`, so the same
rule guards apply and convergence.

## What you'll learn

- How to declare a validation with `validate()`
- When validations run and how `check --data` reports failures
- Why both `false` and `null` results fail
- Why validation-only declarations need no rollback policy

## Prerequisites

- [Page 5](./05-expressions.md) completed: the `User` model has a derived `slug`.

## Step 1: Add a validation

Add a `validations` list to `User.Data`:

```python
from dbwarden.data import DataMeta, col, derive, func, rows, validate


class User(Base):
    ...
    class Data(DataMeta):
        managed_rows = rows(key="code", rows=[{"code": "UY", "name": "Uruguay"}])

        transformations = [
            derive("slug", func.lower(func.trim(col("name"))), rollback="clear"),
        ]

        validations = [
            validate(col("email").is_not(None), message="email is required"),
        ]
```

`validate(expression, *, message="Data validation failed")` runs its expression
against every target row. The `message` is what a report shows when it fails.

## Step 2: Generate and apply

```bash
dbwarden make-migrations "validate user emails"
```

```text
Generated: primary__0004_validate_user_emails.sql (1 ops, max severity INFO)
```

A validation-only change is `INFO`, so it applies without `--force` in this step:

```bash
dbwarden migrate --force
```

```text
Migrations completed successfully: 1 migrations applied.
```

A validation does not change rows, so it produces no rollback SQL and needs no
rollback policy. On rollback, the validation is simply not checked.

## Step 3: Check convergence

```bash
dbwarden check --data
```

```text
No schema changes detected.
```

`check --data` re-evaluates each validation against the live table, in addition to
managed-row and derived-value convergence. Values are redacted by default; a
failure is reported per declaration.

## Step 4: Make one fail

The example rule (`email IS NOT NULL`) cannot fail while the column is `NOT NULL`.
To see a real failure, tighten the rule and insert a row that breaks it:

```python
validations = [
    validate(col("email").is_not(None), message="email is required"),
    validate(col("email") != "", message="email must not be empty"),
]
```

Generate and apply the new migration, then insert a row that is valid by
`NOT NULL` but violates the rule:

```bash
python3 -c "import sqlite3; c = sqlite3.connect('app.db'); c.execute(\"INSERT INTO users (id, email, name, slug) VALUES (2, '', 'Grace Hopper', 'grace hopper')\"); c.commit()"
dbwarden check --data
```

```text
                                                            Safety Check - default
┏━━━━━━━━━━┳━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━┓
┃ Severity ┃ Change     ┃ Table                            ┃ Column ┃ Message                 ┃ Required Flag ┃ Migration Files ┃
┡━━━━━━━━━━╇━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━┩
│ ERROR    │ data_drift │ app/models:User.Data.validations │        │ email must not be empty │               │                 │
└──────────┴──────────┴──────────────────────────────────┴────────┴─────────────────────────┴───────────────┴─────────────────┘
Data convergence failed; --force cannot bypass data drift
```

The report names the declaration and the message. `--force` cannot suppress it:
data drift is a hard failure, distinct from the severity acknowledgements that
govern generation.

## Step 5: False and null both fail

A validation holds only when its expression evaluates to `TRUE`. A row where the
expression is `false` **or** `null` fails. That is deliberate: a predicate that is
unknown for a row is not a passing predicate. For example, on a nullable column,
`col("x") == "ok"` fails rows where `x` is `NULL`; use
`col("x").is_not(None) & (col("x") == "ok")` if that is what you mean.

## Recap

- `validate(expression, message=...)` asserts a predicate over every target row.
- Validations run after data writes and during `check --data`.
- Both `false` and `null` results fail; only `TRUE` passes.
- Validation-only declarations change no rows, so they need no rollback policy.
- A failing validation is reported as `ERROR` data drift that `--force` cannot
  bypass.

## What's next

Move pinned legacy rows into current models with historical transitions:
[7. Historical transitions](./07-historical-transitions.md).
