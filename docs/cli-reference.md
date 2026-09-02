# CLI Reference

Pure command lookup for dbwarden CLI.

## Syntax

```bash
$ dbwarden [GLOBAL_OPTIONS] COMMAND [ARGS] [COMMAND_OPTIONS]
```

## Global options

| Option | Description |
|---|---|
| `--dev` | Use `dev_database_url` and `dev_database_type` for selected database |
| `--strict-translation` | Fail on unsupported/lossy dev SQLite translation |
| `--disable-skip` | Do not skip databases configured with `skip_if_missing` |
| `--debug` | Enable DEBUG-level logging (shows per-file model scanning on `make-migrations`) |
| `--debug-level <LEVEL>` | Set an exact log level: `trace`, `debug`, `info`, `warning`, `error`, `critical`, or `5`/`10`/`20`/`30`/`40`/`50` |
| `--log-level <COMPONENT:LEVEL>` | Per-component log level (repeatable). Example: `--log-level snapshot:debug` |
| `--json`/`-j` | Emit structured JSON for display commands and JSON-formatted logs |
| `--help` | Show help |

`--debug`/`--debug-level` set the logging severity and compose with the
per-command `--verbose`/`-v` flag, which controls INFO-level verbosity. Use both
(`--debug` plus `-v`) for DEBUG diagnostics with verbose INFO output. If both
`--debug` and `--debug-level` are given, `--debug-level` wins. `trace` (or `5`)
is below `debug` and additionally logs per-statement SQL during migration
commands.

`--json` switches display commands (`status`, `history`, `database list`,
`settings show`, `config`, `version`, `lock-status`) to machine-readable JSON
and also formats the command's log output as newline-delimited JSON (the same
effect as `DBWARDEN_LOG_JSON=true`). Commands with their own format option
(`check`, `check-db`, `check-impact`, `diff`, `plugin list`, `plugin info`)
honor the global flag when no explicit format is given.

## Configuration

### `settings show`

```bash
$ dbwarden settings show
$ dbwarden settings show primary
$ dbwarden settings show --all
```

### `database list`

```bash
$ dbwarden database list
```

## Migration authoring

### `make-migrations`

```bash
$ dbwarden make-migrations "create users table" --database primary
$ dbwarden make-migrations --verbose --database primary
$ dbwarden --debug make-migrations --database primary
$ dbwarden --debug-level warning make-migrations --database primary
$ dbwarden make-migrations --plan --database primary
$ dbwarden make-migrations --rename users.username:email --database primary
$ dbwarden make-migrations --rename-table users:accounts --database primary
$ dbwarden make-migrations --safe-type-change --database primary
```

Options:

- `--database`/`-d`: Target database
- `--plan`: Print migration plan JSON without writing files
- `--sql`: Output raw migration SQL to stdout without writing files
- `--offline`: Use model state file instead of live database (run `export-models` first)
- `--verbose`/`-v`: Verbose output
- `--debug`/`--debug-level <LEVEL>`: Global options (see Global options table). DEBUG shows every model file scanned during discovery.
- `--rename`: Repeatable. Declare a column rename in format `table.old_name:new_name`.
- `--rename-table`: Repeatable. Declare a table rename in format `old_table:new_table`.
- `--safe-type-change`: Multi-step safe type change strategy.
- `--concurrent`/`--no-concurrent`: Use concurrent index creation (default: on).
- `--clickhouse-engine-recreate`: Allow automatic ClickHouse table rebuild on engine change.
- `--drop-preserved-clickhouse-table` / `--keep-preserved-clickhouse-table`: Drop or keep the preserved old ClickHouse table after engine-recreate swap.
- `--postgres-auto-using`: Emit active `USING` clause on PostgreSQL `ALTER COLUMN TYPE` (default: commented out).
- `--type`/`-t`: Output prefix: `versioned` (default), `ra`/`runs_always`, or `roc`/`runs_on_change`.
- `--perf`: Log SQL-generation phase timing.

See [make-migrations](commands/make-migrations.md) for full documentation including rename detection, column-level changes, schema snapshots, and plan format.

### `new`

```bash
$ dbwarden new "manual hotfix" --database primary
$ dbwarden new "backfill" --database primary --version 0042
$ dbwarden new "seed data" --database primary --type ra
```

Options: `--database`, `--version`, `--type`/`-t`

### `generate-models`

```bash
$ dbwarden generate-models --output ./models/ --database primary
$ dbwarden generate-models --database primary --single-file
$ dbwarden generate-models --database primary --tables users,posts
$ dbwarden generate-models --database primary --exclude-tables logs,audit
```

Options: `--output`/`-o` (default `models`), `--tables`, `--exclude-tables`, `--clickhouse-engines`, `--relationships`, `--dialect`, `--single-file`, `--base`, `--database`/`-d`

### `export-models`

```bash
$ dbwarden export-models --database primary
$ dbwarden export-models --database primary --output .dbwarden/model_state.json
```

Exports current model definitions to a JSON state file for offline migration diffs.

> **Important:** The model state file is used for offline migration generation. It is auto-generated and committed to version control. If accidentally deleted, restore it from git or regenerate it by running `dbwarden export-models --database <db>` against a live database. Without it, offline commands like `make-migrations --offline` will not work, but online operations are unaffected.

Options: `--output`/`-o` (default `.dbwarden/model_state.json`), `--database`/`-d`

### `diff`

```bash
$ dbwarden diff --database primary
$ dbwarden diff --database primary --out json
$ dbwarden diff --database primary --out sql
$ dbwarden diff --database primary --offline
```

Read-only model-vs-database comparison. No files are written.

Options: `--database`/`-d`, `--out`/`-o` (`table`, `json`, `sql`), `--offline`, `--verbose`/`-v`

### `check-impact`

```bash
$ dbwarden check-impact 0042 --database primary
$ dbwarden check-impact 0042 --database primary --out json
$ dbwarden check-impact 0042 --database primary --scan-path app/
$ dbwarden check-impact path/to/primary__0042_add_bio.plan.json
```

Scans your codebase for references to schema elements affected by a migration.

| Option | Description |
|--------|-------------|
| `migration` | Migration version (e.g. `0042`) or plan file path (required) |
| `--out`/`-o` | Output format: `text` (default) or `json` |
| `--scan-path` | Directory to scan for affected code (default: `.`) |
| `--deep` | Enable deep introspection (imports models live) |
| `--verbose`/`-v` | Include INFO-level operations in the scan |
| `--database`/`-d` | Target database name |

## Migration execution

### `migrate`

```bash
$ dbwarden migrate --database primary
$ dbwarden migrate --all
$ dbwarden migrate --database primary --to-version 0010
$ dbwarden migrate --database primary --count 2
$ dbwarden migrate --database primary --with-backup
$ dbwarden migrate --database primary --baseline --to-version 0005
```

Options:

- `--database`, `--all`
- `--to-version`, `--count`
- `--baseline`
- `--with-backup`, `--backup-dir`
- `--dry-run` (show what would be applied without executing)
- `--sandbox` (apply in a temporary sandbox database)
- `--apply-seeds` (apply pending seeds after migrations, overrides config)
- `--defer-snapshots` (write one final schema snapshot instead of one after every migration)
- `--verbose`
- `--perf` (log per-SQL-statement timing breakdowns)

### `rollback`

```bash
$ dbwarden rollback --database primary
$ dbwarden rollback --database primary --count 2
$ dbwarden rollback --database primary --to-version 0007
```

Options: `--database`, `--count`, `--to-version`, `--verbose`, `--perf`

### `downgrade`

```bash
$ dbwarden downgrade --to 0005 --database primary
```

Options: `--to` (required), `--database`, `--verbose`, `--perf`

### `make-rollback`

```bash
$ dbwarden make-rollback migrations/primary__0005_add_table.sql
```

Generates a `.rollback.sql` file for the given migration file.

### `snapshot`

```bash
$ dbwarden snapshot users --database primary
```

Outputs the DDL schema of the specified table.

## Seed management

### `seed create`

```bash
$ dbwarden seed create "seed initial data" --database primary
$ dbwarden seed create "populate lookup tables" --database primary --type python
```

Options: `--database`, `--type` (`sql` or `python`, default `sql`), `--verbose`

### `seed apply`

```bash
$ dbwarden seed apply --database primary
$ dbwarden seed apply --database primary --version 0003
$ dbwarden seed apply --database primary --dry-run
$ dbwarden seed apply --all
```

Options: `--database`, `--all` (`-a`), `--version`, `--dry-run`, `--verbose`

### `seed list`

```bash
$ dbwarden seed list --database primary
$ dbwarden seed list --all
$ dbwarden seed list --prune
```

Options: `--database`, `--all`, `--prune`, `--verbose`

### `seed rollback`

```bash
$ dbwarden seed rollback --database primary
$ dbwarden seed rollback --database primary --count 2
$ dbwarden seed rollback --database primary --to-version 0003
$ dbwarden seed rollback --all
```

Options: `--database`, `--count`, `--to-version`, `--all`, `--verbose`

### `seed export`

```bash
$ dbwarden seed export --database primary
$ dbwarden seed export --all
$ dbwarden seed export --database clickhouse --output-dir ./seeds
```

Export code seeds to ROC SQL files for stateless production application.

Options: `--database`/`-d`, `--all`/`-a`, `--output-dir`/`-o` (default `seeds/`)

## Inspection and diagnostics

### `status`

```bash
$ dbwarden status --database primary
$ dbwarden status --all
$ dbwarden status --all-environments
```

### `history`

```bash
$ dbwarden history --database primary
```

### `check-db`

```bash
$ dbwarden check-db --database primary
$ dbwarden check-db --database primary --out json
```

Output formats: `txt`, `json`, `yaml`, `sql`

### `check`

```bash
$ dbwarden check --database primary
$ dbwarden check --database primary --force
$ dbwarden check --database primary --out json
```

Output formats: `txt`, `json`

## Locking

### `lock-status`

```bash
$ dbwarden lock-status --database primary
```

### `unlock`

```bash
$ dbwarden unlock --database primary
```

## Plugin management

See the [Plugins guide](plugins/index.md) for the trust model and development docs.

### `plugin list`

```bash
$ dbwarden plugin list
$ dbwarden plugin list --format json
```

Shows discovered plugins with tier, trust/load state, registered hooks, object handlers, and lock status.

Options: `--format`/`-f` (`table` or `json`, default `table`)

### `plugin info`

```bash
$ dbwarden plugin info dbwarden-fastapi
$ dbwarden plugin info dbwarden-fastapi --format json
```

Shows entry point, tier, trust/load state, hooks, official repository, approved minimum version, and lockfile provenance. Exits `1` if the plugin is not found.

Options: `--format`/`-f` (`table` or `json`, default `table`)

### `plugin add`

```bash
$ dbwarden plugin add dbwarden-fastapi
$ dbwarden plugin add dbwarden-fastapi --version 0.2.0 --uv
$ dbwarden plugin add dbwarden-example --dry-run
```

Installs a plugin. Official plugins are provenance-verified and fail closed if verification is unavailable; community plugins are installed but not trusted (run `plugin trust` next).

Options: `--uv` (use `uv add` instead of pip), `--version` (pin an exact version), `--dry-run` (print the plan without installing)

### `plugin remove`

```bash
$ dbwarden plugin remove dbwarden-example
$ dbwarden plugin remove dbwarden-example --dry-run --uv
```

Uninstalls the distribution and removes its consent and lockfile entries.

Options: `--uv` (use `uv remove` instead of pip), `--dry-run` (print the plan without uninstalling)

### `plugin trust`

```bash
$ dbwarden plugin trust dbwarden-example
```

Records consent for the installed version of a community plugin in `.dbwarden/consent.toml`. Consent is version-specific.

### `plugin untrust`

```bash
$ dbwarden plugin untrust dbwarden-example
```

Revokes consent for a community plugin.

## Utility

### `recover-model-state`

```bash
$ dbwarden recover-model-state --database primary
```

Recovers a deleted model state file by replaying migrations in a sandbox.

Options: `--database`/`-d`

### `config`

```bash
$ dbwarden config
```

### `version`

```bash
$ dbwarden version
```

For worked command examples, see the [Cookbook & Examples](cookbook/index.md).
