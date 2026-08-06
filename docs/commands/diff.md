# `diff`

Show structural differences between SQLAlchemy models and a live database.
Read-only: no files are written.

## Usage

```bash
$ dbwarden diff --database primary
$ dbwarden diff --database primary --out json
$ dbwarden diff --database primary --out sql
$ dbwarden diff --database primary --offline
```

## Options

| Option | Description |
|--------|-------------|
| `--database`, `-d` | Target database name |
| `--out`, `-o` | Output format: `table` (default), `json`, `sql` |
| `--offline` | Use exported model state file instead of live DB snapshot |
| `--verbose`, `-v` | Enable verbose logging |

## Output formats

### `table` (default)

Displays a Rich table with columns: Operation, Table, Target, Severity.

```text
          Schema Diff           
┏━━━━━━━━━━━━━━┳━━━━━━━┳━━━━━━━━┳━━━━━━━━━┓
┃ Operation    ┃ Table ┃ Target ┃ Severity┃
┡━━━━━━━━━━━━━━╇━━━━━━━╇━━━━━━━━╇━━━━━━━━━┩
│ add_column   │ users │ email  │ INFO    │
│ drop_column  │ users │ name   │ WARNING │
└──────────────┴───────┴────────┴─────────┘
```

### `json`

```json
[
  {"operation": "add_column", "table": "users", "target": "email", "severity": "INFO"},
  {"operation": "drop_column", "table": "users", "target": "name", "severity": "WARNING"}
]
```

### `sql`

Prints the raw migration SQL that would be generated.

## Offline mode

Requires a model state file created by `dbwarden export-models`:

```bash
$ dbwarden export-models --database primary
# Switch to offline machine
$ dbwarden diff --database primary --offline
```

## See also

- [`make-migrations`](./make-migrations.md): generates migration files from diffs
- [`check`](./check.md): safety analyzer for schema changes
- [`check-db`](./check-db.md): inspect live database schema
