# 4. Static sources

Static sources let you keep a reference set in a CSV or JSON file. dbwarden reads
the file once at compile time, type-checks it, and embeds the canonical values in
the frozen artifact.

## What you'll learn

- How to declare managed rows with `source=`
- Where source files may live and how paths are resolved
- How CSV values are converted using column types
- What JSON sources reject (duplicate keys, non-finite numbers)
- Why checksums follow normalized values, not file formatting

## Prerequisites

- [Page 3](./03-managed-rows.md) completed: your project has the `Country` seed.
- A project with `data_paths` configured, as on
  [page 2](./02-setup-and-first-declaration.md).

## Step 1: Add a source file

Create `data/currencies.csv` at the project root:

```csv
code,name
USD,US Dollar
EUR,Euro
UYU,Uruguayan Peso
```

The path in `source=` is relative to the project root (the directory with
`dbwarden.py`). It must stay inside the project: a path that escapes the project
root, or a symlink that resolves outside it, is rejected at compile time. Static
sources containing secrets are unsupported.

## Step 2: Declare the model with `source=`

Add a `Currency` model to `app/models.py`:

```python
class Currency(Base):
    __tablename__ = "currencies"

    code = Column(String(3), primary_key=True)
    name = Column(String(100), nullable=False)

    class Data(DataMeta):
        managed_rows = rows(
            key="code",
            source="data/currencies.csv",
            owned_columns=["name"],
            rollback="restore_previous",
        )
```

Everything you learned about managed rows still applies: `key` and
`owned_columns` behave the same, and `on_missing`, `scope`, and `rollback` are
unchanged. The only difference is where the values come from.

## Step 3: Generate and apply

```bash
dbwarden make-migrations "add currencies from CSV"
```

```text
Generated: primary__0002_add_currencies_from_csv.sql (2 ops, max severity INFO)
```

Two operations were planned: the `CREATE TABLE` for `currencies` and the managed-row
insert. Apply it and confirm the rows:

```bash
dbwarden migrate --force
python3 -c "import sqlite3; print(sqlite3.connect('app.db').execute('SELECT code, name FROM currencies ORDER BY code').fetchall())"
```

```text
[('EUR', 'Euro'), ('USD', 'US Dollar'), ('UYU', 'Uruguayan Peso')]
```

The CSV file is not needed at apply time. Execution uses the canonical values
frozen into the migration, so you can change or delete the file afterward without
altering a pending migration.

## Step 4: Inspect the frozen values

`data describe` shows the declaration and its canonical values with
`--show-managed-values`:

```bash
dbwarden data describe --format markdown --show-managed-values
```

````text
### `app/models:Currency.Data.managed_rows`

Rollback: `restore_previous`. Proof: `guarded`.

Table: `currencies`. Key: `code`.
Owned columns: `name`. Missing rows: `keep`.
Input: `csv`; path: `data/currencies.csv`; checksum: `14b859166349952ca861a72a101a554d6720e115e00edcd91f612853c0058b81`.

```json
[
  { "code": "EUR", "name": "Euro" },
  { "code": "USD", "name": "US Dollar" },
  { "code": "UYU", "name": "Uruguayan Peso" }
]
```
````

The `path` is provenance only. The `checksum` is what the bundle actually binds.

## Step 5: Type conversion and normalization

CSV fields are text; dbwarden converts each value using the target column's type.
A value written to an `INTEGER` column becomes an integer, a `BOOLEAN` value
becomes a boolean, and so on. A value that cannot be converted fails compilation
rather than being written as the wrong type.

The checksum is computed over the normalized, typed values, not the raw bytes of
the file. Reordering rows, switching between LF and CRLF line endings, or
requoting a field does not change it. Confirm by reordering the CSV and re-running
`data describe` — the checksum stays
`14b859166349952ca861a72a101a554d6720e115e00edcd91f612853c0058b81`.

`source=` also accepts JSON. A JSON array of objects works like the CSV:

```json
[
  {"code": "USD", "name": "US Dollar"},
  {"code": "EUR", "name": "Euro"}
]
```

JSON sources reject duplicate keys and non-finite numbers (`NaN`, `Infinity`), and
canonicalization rejects duplicate normalized keys, so the same logical row cannot
appear twice.

## Recap

- `source=` loads a CSV or JSON file at compile time and embeds canonical values;
  apply never reads the file.
- Source paths are relative to the project root and must stay inside the project;
  symlink escapes are rejected.
- CSV values are converted using column types, and bad conversions fail compilation.
- Checksums follow normalized, typed values, not file formatting, so whitespace and
  row order do not matter.
- JSON sources reject duplicate keys and non-finite numbers.

## What's next

Compute a column from other columns with a typed expression:
[5. Expressions](./05-expressions.md).
