# 3. Managed rows

Managed rows are the heart of declarative data migrations: a finite set of rows a
declaration owns, keyed by stable columns, with explicit ownership and rollback
rules.

## What you'll learn

- The full `rows()` signature, including defaults
- How keys and `owned_columns` decide what the declaration may touch
- The difference between literal rows and a `source=`
- Missing-row policies: `keep`, `delete`, and `archive`
- How scope and acknowledgement gate deletion
- The `restore_previous` and `irreversible` rollback policies
- Why removing a row from a declaration does not delete it

## Prerequisites

- [Page 2](./02-setup-and-first-declaration.md) completed: a project with the
  `Country` seed applied.

## Step 1: The signature

```text
rows(
    *,
    key=None,
    rows=None,
    source=None,
    owned_columns=None,
    on_missing="keep",
    scope=None,
    acknowledge_delete=False,
    archive_to=None,
    acknowledge_archive=False,
    rollback="irreversible",
    category=None,
)
```

You pass exactly one of `rows=` (a list of literal dicts) or `source=` (a path to a
static CSV or JSON file). Passing both, or neither, fails at import time.

## Step 2: Keys

`key` names the column or columns that identify each row. Omitted, it defaults to
the model's primary key:

```python
class Country(Base):
    code = Column(String(2), primary_key=True)

    class Data(DataMeta):
        managed_rows = rows(rows=[...])  # key defaults to "code"
```

Keys must exist on the target table. A composite key is passed as a list:

```python
rows(key=["country_code", "currency_code"], rows=[...])
```

## Step 3: Ownership

`owned_columns` lists the columns this declaration is responsible for. Omitted, it
defaults to the declared non-key columns. Only owned columns are ever written.

```python
managed_rows = rows(
    key="code",
    rows=[{"code": "UY", "name": "Uruguay"}],
    owned_columns=["name"],          # "code" is the key; nothing else is touched
    rollback="restore_previous",
)
```

Ownership drives three behaviors:

- **Pre-existing rows.** An existing target row is accepted only when every owned
  value already matches the declaration. Columns outside `owned_columns` are left
  alone, so application data on the same row survives.
- **Rollback conflicts.** Rollback checks the current owned value before changing
  it. If application code edited an owned column after apply, rollback stops for
  reconciliation instead of overwriting the newer value.
- **No table ownership.** A declaration owns specific rows and columns, not the
  table. Rows it never claimed are not its responsibility.

## Step 4: Literal rows or a static source

Inline literals keep small reference sets readable:

```python
rows=[{"code": "UY", "name": "Uruguay"}, {"code": "AR", "name": "Argentina"}]
```

For larger sets, point `source=` at a CSV or JSON file. It is read at compile time
and embedded as canonical values in the frozen artifact; apply never rereads the
file. [Page 4](./04-static-sources.md) covers static sources in full.

## Step 5: Missing-row policy

`on_missing` decides what happens to rows this declaration previously owned but no
longer lists:

| Value | Behavior |
|---|---|
| `keep` (default) | Leave the row in place. Removing it from the declaration is a no-op. |
| `delete` | Delete the owned row, but only inside `scope` and only with `acknowledge_delete=True`. |
| `archive` | Move the owned row to `archive_to=archive_table(...)`, with `acknowledge_archive=True`. |

`keep` is the safe default because it can never remove data.

## Step 6: Delete with scope and acknowledgement

Deletion is never implicit. It needs a `scope` predicate, which bounds which rows
the declaration may consider missing, plus `acknowledge_delete=True`. If you are
following the build path, try this step on a copy of the project: the later pages
assume the original three-country declaration.

```python
managed_rows = rows(
    key="code",
    rows=[
        {"code": "UY", "name": "Uruguay"},
        {"code": "BR", "name": "Brazil"},
    ],
    owned_columns=["name"],
    on_missing="delete",
    scope=col("code").is_not(None),
    acknowledge_delete=True,
    rollback="restore_previous",
)
```

Here `scope=col("code").is_not(None)` says "every country row is in scope", so a
previously owned row that is no longer declared gets deleted. Deletion only ever
removes rows this declaration already owns inside the scope; it never adopts every
row with a matching key.

Generate and apply it. Deletion is `CRITICAL`, so plain `migrate` refuses and
`--force` is required:

```bash
dbwarden make-data-migration "remove argentina"
dbwarden migrate --force
```

```text
Generated: primary__0002_remove_argentina.sql (1 ops, max severity CRITICAL)
Migrations completed successfully: 1 migrations applied.
```

The generated upgrade contains a guarded delete:

```sql
DELETE FROM "countries"
WHERE (("code" IS NOT DISTINCT FROM 'AR') AND ("name" IS NOT DISTINCT FROM 'Argentina'))
  AND (("code" IS NOT NULL))
  AND EXISTS (SELECT 1 FROM "_dbwarden_rows_…" WHERE ("code" IS NOT DISTINCT FROM 'AR'));
```

The `EXISTS` clause ties the delete to the ownership ledger: only a row this
declaration created or adopted is eligible.

## Step 7: Rollback policy

`rollback` declares how the generated migration reverses itself:

| Value | Meaning |
|---|---|
| `irreversible` (default) | No safe reverse; rollback refuses the declaration. |
| `restore_previous` | Restore prior owned values, checked against the current value. |

Use `restore_previous` when the generated reverse can prove the row still holds the
postimage it wrote. `irreversible` is the honest default for a change you cannot
safely undo; a comment or an empty rollback section is not a reversal.

## Step 8: Removing a declaration does not delete rows

With the default `on_missing="keep"`, deleting a row from the declaration produces
no plan at all, and the row stays in the database:

```bash
dbwarden make-data-migration "drop argentina from declaration"
```

```text
No new migrations to generate - all models already covered by existing migrations.
```

To actually remove the row, declare the deletion (Step 6): set `on_missing="delete"`,
provide `scope`, and set `acknowledge_delete=True` in the same change. Deleting a
whole declaration works the same way — first declare the deletion and generate its
migration, then remove the declaration.

## Recap

- `rows()` takes exactly one of `rows=` and `source=`; `key` defaults to the
  primary key and `owned_columns` to the non-key columns.
- Ownership limits every write and makes rollback refuse to clobber later edits.
- `on_missing="keep"` (the default) never removes data; `delete` and `archive`
  require `scope` plus an acknowledgement.
- Deletion is `CRITICAL` and requires `--force`; the generated SQL is guarded by the
  ownership ledger.
- `rollback="restore_previous"` reverses safely; `irreversible` refuses rollback.
- Removing a declaration does not delete rows — declare the deletion explicitly.

## What's next

Load managed rows from a file instead of literals:
[4. Static sources](./04-static-sources.md).
