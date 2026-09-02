# `seed`

Manage seed data for a database.

## Subcommands

- `seed create`: create a new file seed (legacy)
- `seed apply`: apply pending seeds (file + code seeds)
- `seed list`: list seeds and their status
- `seed rollback`: roll back applied seeds
- `seed export`: export code seeds to ROC SQL files for stateless production application

---

## `seed create`

Create a new file-based seed file (SQL or Python). For new projects, prefer [code seeds](../seeds.md#code-seeds-recommended) instead.

### Usage

```bash
$ dbwarden seed create "seed initial data" --database primary
$ dbwarden seed create "populate lookup tables" --database primary --type python
```

### Options

- `--database`, `-d`: target database handle
- `--type`: `sql` (default) or `python`
- `--verbose`, `-v`

---

## `seed apply`

Apply pending seeds. Both file seeds and [code seeds](../seeds.md#code-seeds-recommended) are discovered and applied.

### Usage

```bash
$ dbwarden seed apply --database primary
$ dbwarden seed apply --database primary --version 0003
$ dbwarden seed apply --database primary --dry-run
$ dbwarden seed apply --all
```

### Options

- `--database`, `-d`
- `--all`, `-a`: apply across all configured databases
- `--version`: apply up to this seed version
- `--dry-run`: preview without executing
- `--verbose`, `-v`

---

## `seed list`

List seeds and their applied status. Includes both file seeds and code seeds.

### Usage

```bash
$ dbwarden seed list --database primary
$ dbwarden seed list --all
$ dbwarden seed list --prune              # clean up orphaned tracking records
```

### Options

- `--database`, `-d`
- `--all`, `-a`
- `--prune`: remove tracking records for seed files that no longer exist on disk
- `--verbose`, `-v`

---

## `seed rollback`

Reverse applied seeds and then remove their tracking records. SQL file seeds require a `-- rollback` section; Python file and procedural code seeds require `reverse(connection, session)`. Seeds without reverse logic are irreversible and rollback fails without changing tracking.

### Usage

```bash
$ dbwarden seed rollback --database primary
$ dbwarden seed rollback --database primary --count 2
$ dbwarden seed rollback --database primary --to-version 0003
```

### Options

- `--database`, `-d`
- `--all`, `-a`: rollback on all databases
- `--count`, `-c`: number of seeds to roll back
- `--to-version`, `-t`: roll back to this seed version
- `--verbose`, `-v`

See also: [Seed Management](../seeds.md)

---

## `seed export`

Export code seeds to ROC (runs-on-change) SQL files for stateless application. The generated file contains `INSERT ... ON CONFLICT` statements rendered in the target database dialect. ROC files are re-applied when their content checksum changes.

### Usage

```bash
$ dbwarden seed export --database primary
$ dbwarden seed export --all
$ dbwarden seed export --database clickhouse --output-dir ./seeds
```

### Options

- `--database`, `-d`: target database handle
- `--all`, `-a`: export seeds for all configured databases
- `--output-dir`, `-o`: output directory (default: `seeds/`)

### Behavior

- **Row-based seeds** (`rows = [...]`): each row is rendered as an `INSERT` statement with `ON CONFLICT` matching the seed's `__seed_on_conflict__`
- **Procedural seeds** (`forward(connection, session)`): cannot be exported because executing arbitrary code under a different database backend could produce incorrect SQL
- Seeds are ordered by FK dependency (topological sort) so foreign-key-safe insert order is preserved

### Dialect requirement

Exporting requires the same dialect packages as connecting to that database. For ClickHouse, install `clickhouse-sqlalchemy`. Missing packages produce a clear error at export time.

### Non-handled problems

- Removed rows are not deleted (no purge on re-export)
- Procedural seeds must be applied directly to their configured database
