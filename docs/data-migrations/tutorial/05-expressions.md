# 5. Expressions

A transformation owns one target column and fills it with a deterministic
expression. Expressions are typed ASTs - not Python callbacks and not raw SQL - so
dbwarden can validate them and lower them to each backend.

## What you'll learn

- How to declare a derivation with `derive()` and a rollback policy
- The expression building blocks: `col`, `literal`, `param`, `case`, `cast`,
  `mapping`, and `func.<name>`
- The allowed functions and the operators you can combine
- How to declare a domain with `when`, `on_unmatched`, and `max_rows`
- The four rollback modes: `clear`, `recompute`, `capture`, and `irreversible`
- How bound parameters work with `--param`

## Prerequisites

- [Page 4](./04-static-sources.md) completed: `countries` and `currencies` exist.
- The `User` model is about to be added.

## Step 1: Declare a derivation

Add a `User` model with managed rows and one transformation:

```python
from sqlalchemy import Integer
from dbwarden.data import DataMeta, col, derive, func, rows


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True)
    email = Column(String(255), nullable=False)
    name = Column(String(200), nullable=False)
    slug = Column(String(200), nullable=True)

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
```

`derive(target, expression, *, rollback, ...)` owns exactly `slug` inside its
domain:

```text
derive(
    target,
    expression,
    *,
    rollback,
    when=None,
    on_unmatched="error",
    max_rows=None,
    category=None,
    settings=None,
    execution=None,
)
```

`rollback` is required. `slug` is nullable, which is what `rollback="clear"`
needs - clearing sets the owned column back to `NULL`.

## Step 2: Build expressions

Expressions are constructed from a small set of nodes:

| Node | Purpose |
|---|---|
| `col(name, table=None)` | A column reference. `col("users.name")` qualifies the table. |
| `literal(value)` | A typed literal (`int`, `str`, `bool`, `None`, `Decimal`, `date`, `datetime`). |
| `param(name, type=None)` | A bound parameter resolved at generation. |
| `case((cond, value), ..., else_=...)` | Conditional branches with an explicit fallback. |
| `cast(value, type_)` | An explicit cast (`cast(col("n"), "INTEGER")`). |
| `mapping(value, {key: result}, else_=...)` | A static value map. |
| `func.<name>(...)` | An allowlisted function. |

## Step 3: The function allowlist

| Function | Form | Notes |
|---|---|---|
| `lower`, `upper`, `trim` | `func.lower(value)` | Case and whitespace normalization. |
| `concat` | `func.concat(a, b, ...)` | Two or more arguments. |
| `coalesce` | `func.coalesce(a, b, ...)` | First non-null argument. |
| `json_extract` | `func.json_extract(value, "$.path")` | Literal path beginning with `$`; returns a scalar. |
| `split_part` | `func.split_part(value, delim, index)` | One-based literal index from 1 to 64. |
| `date_trunc` | `func.date_trunc(value, "day", "UTC")` | PostgreSQL only; requires pinned settings (Step 6). |

`json_extract` decodes JSON strings, renders numbers as text, renders booleans as
`true` or `false`, and returns SQL null for JSON null, a missing path, an object, or
an array. `split_part` returns an empty string for an absent component and keeps
SQL null as null. Arbitrary functions, Python callbacks, raw SQL, volatile values,
and unsupported casts fail compilation.

## Step 4: Operators

Arithmetic `+ - * / %`, comparisons `== != < <= > >=`, null tests
`col("x").is_(None)` / `col("x").is_not(None)`, membership
`col("x").in_([...])`, and unary negation all build expressions. Combine predicates
with `&` (and), `|` (or), and `~` (not):

```python
derive(
    "slug",
    col("name"),
    when=(col("email").is_not(None) & (col("email") != "")),
    rollback="clear",
)
```

Python's `and`, `or`, and `not` cannot build expressions - an expression used as a
Python boolean raises `TypeError`. Use the operator forms instead.

## Step 5: Domain proofs

A partial expression needs an explicit domain, so dbwarden knows which rows it
applies to:

- `when=` supplies the domain predicate.
- `on_unmatched="error"` fails when a row falls outside the domain; `"keep"` leaves
  those rows untouched.
- `max_rows=` aborts if more rows than the limit would be affected.

A `case` or `mapping` without a fallback also needs a domain that proves it is
complete. These checks happen at compile time.

## Step 6: Rollback modes

| Mode | Reverse behavior |
|---|---|
| `clear` | Set the owned column to `NULL`; requires a nullable target and a current value matching the postimage. |
| `recompute` | Restore a previous frozen expression; requires a prior declaration with compatible ownership and domain. |
| `capture` | Record the typed preimage before the write and restore it; requires a target primary key. |
| `irreversible` | Refuse rollback outright. |

`capture` is covered in depth on [page 9](./10-capture-archive-batching.md). Generic
inverse reconstruction is not supported; if you cannot express the reverse, declare
`irreversible`.

`date_trunc` is a backend-pinned function: it is PostgreSQL-only and requires
`settings` containing nonempty `backend_version`, `timezone`, `collation`,
`search_path`, and `encoding` strings. Those settings become part of the expression
identity and are checked against the server before execution:

```python
derive(
    "day",
    func.date_trunc(col("created_at"), "day", "UTC"),
    rollback="irreversible",
    settings={
        "backend_version": "16",
        "timezone": "UTC",
        "collation": "C",
        "search_path": "public",
        "encoding": "UTF8",
    },
)
```

## Step 7: Parameters

`param("name")` is resolved when you generate, not when you apply. For example, a
derivation can take its value from a parameter:

```python
derive("region", param("region"), rollback="irreversible")
```

Pass values with repeated `--param NAME=VALUE`:

```bash
dbwarden make-migrations "seed reference data" --param region='"EU"' --param min_id=10
```

Values parse as JSON when possible and as strings otherwise. Missing or duplicate
names fail:

```bash
dbwarden make-migrations "seed reference data"
```

```text
Missing bound parameters: region
```

Frozen artifacts store the resolved values, so execution never rereads parameters.

## Step 8: Generate and apply

The derivation is a `WARN` operation, so generate and watch the severity gate:

```bash
dbwarden make-migrations "add users with slugs"
```

```text
Generated: primary__0003_add_users_with_slugs.sql (3 ops, max severity WARN)
```

Plain `migrate` refuses a `WARN` operation:

```bash
dbwarden migrate
```

```text
0003: acknowledgement required. hint: review the plan and pass --force
Migration aborted by preflight checks. hint: resolve the reported errors
```

Acknowledge it after review:

```bash
dbwarden migrate --force
python3 -c "import sqlite3; print(sqlite3.connect('app.db').execute('SELECT id, name, slug FROM users').fetchall())"
```

```text
[(1, '  Ada Lovelace  ', 'ada lovelace')]
```

`name` keeps its original spacing (it is only owned by the managed-row
declaration), while `slug` is the derived `lower(trim(name))`.

## Recap

- `derive(target, expression, rollback=...)` owns one column inside its domain.
- Expressions are typed ASTs built from `col`, `literal`, `param`, `case`, `cast`,
  `mapping`, and the allowlisted `func.<name>` calls.
- Use `&`, `|`, and `~` for predicates; Python `and`/`or`/`not` do not work.
- `when`, `on_unmatched`, and `max_rows` prove and bound a transformation's domain.
- Rollback is `clear`, `recompute`, `capture`, or `irreversible`; `capture` records
  a typed preimage, and `date_trunc` needs pinned backend settings.
- `--param NAME=VALUE` freezes parameter values into the artifact; derivations are
  `WARN` and require `--force`.

## What's next

Assert data quality during apply and convergence:
[6. Validation](./06-validation.md).
