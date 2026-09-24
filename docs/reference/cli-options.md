# CLI option inventory

This page lists every built-in command, argument, and option from the current CLI.
For workflows and exit codes, see the [CLI guide](../cli-reference.md).
Plugin commands are added at startup by installed, approved plugins; inspect
their help separately. Global options precede the command name.

Regenerate with `python scripts/check-docs.py --cli-reference`.

## `dbwarden`

dbwarden - Professional database migration system for SQLAlchemy models

All commands support the --verbose / -v flag for detailed output and the
--debug / --debug-level flags for DEBUG-level diagnostics.

| Argument or option | Type | Default | Description |
|---|---|---|---|
| --dev | boolean | False | Use development database settings (dev_database_url/dev_database_type) |
| --strict-translation | boolean | False | Fail when a type/default cannot be translated for target backend |
| --disable-skip | boolean | False | Do not skip databases configured with skip_if_missing. |
| --debug | boolean | False | Enable DEBUG-level logging |
| --debug-level | str | None | Exact log level: trace, debug, info, warning, error, critical, or 5/10/20/30/40/50 |
| --json, -j | boolean | False | Render command output (and logs) as structured JSON |
| --log-level | str (repeatable) | [] | Per-component log level (repeatable). Example: --log-level snapshot:debug --log-level plugin:warning. Known components: plugin, lock, registry, snapshot |

`--help` displays command help.

## `dbwarden init`

Initialize the migrations directory.

| Argument or option | Type | Default | Description |
|---|---|---|---|
| --database, -d | str | None | Database name to create migration directory for |

`--help` displays command help.

## `dbwarden generate-models`

Reverse-engineer SQLAlchemy model code from a live database.

| Argument or option | Type | Default | Description |
|---|---|---|---|
| --output, -o | str | 'models' | Output directory for generated model files |
| --tables | str | None | Comma-separated list of tables to include |
| --exclude-tables | str | None | Comma-separated list of tables to exclude |
| --clickhouse-engines | boolean | False | Include ClickHouse engine metadata |
| --relationships | boolean | False | Generate relationship attributes |
| --dialect | str | None | SQL dialect for type mapping (auto-detected by default) |
| --single-file | boolean | False | Generate a single models.py file |
| --base | str | None | Custom Base class import path (e.g. 'app.core.database:Base' or 'app.database:DeclarativeBase') |
| --database, -d | str | None | Target database name |

`--help` displays command help.

## `dbwarden export-models`

Export current model definitions to a JSON state file for offline diffs.

| Argument or option | Type | Default | Description |
|---|---|---|---|
| --output, -o | str | None | Output path for the model state JSON file |
| --database, -d | str | None | Target database name |

`--help` displays command help.

## `dbwarden recover-model-state`

Recover a deleted model state file by replaying migrations in a sandbox.

| Argument or option | Type | Default | Description |
|---|---|---|---|
| --database, -d | str | None | Target database name |

`--help` displays command help.

## `dbwarden make-migrations`

Auto-generate SQL migration from SQLAlchemy models.

| Argument or option | Type | Default | Description |
|---|---|---|---|
| description | str | None | Description for the migration |
| --verbose, -v | boolean | False | Enable verbose logging |
| --plan | boolean | False | Output migration plan JSON without writing files |
| --sql | boolean | False | Output raw migration SQL to stdout without writing files |
| --database, -d | str | None | Target database name |
| --rename | str (repeatable) | [] | Explicitly rename column (format: table.old_name:new_name). Can be repeated. |
| --safe-type-change | boolean | False | Use multi-step safe type change (add temp column, backfill, swap, drop old) |
| --rename-table | str (repeatable) | [] | Declare a table rename as old_table:new_table. Repeatable. |
| --concurrent, --no-concurrent | boolean | True | Use CREATE INDEX CONCURRENTLY on PostgreSQL (disable inside transactions) |
| --offline | boolean | False | Use model state file instead of live database (requires export-models first) |
| --clickhouse-engine-recreate | boolean | False | Allow automatic ClickHouse table rebuild when engine changes require recreation |
| --drop-preserved-clickhouse-table, --keep-preserved-clickhouse-table | boolean | None | Drop the preserved old ClickHouse table after swap; if omitted, prompt in TTY and preserve by default |
| --postgres-auto-using | boolean | False | Emit active USING clause on PostgreSQL ALTER COLUMN TYPE (default: commented-out) |
| --type, -t | str | 'versioned' | Output prefix: versioned (default), runs_always/ra, runs_on_change/roc |
| --perf | boolean | False | Log SQL-generation phase timing |
| --split-at-severity | str | None | Defer operations at or above SAFE, INFO, WARN, or CRITICAL. |
| --strict-pending, --no-strict-pending | boolean | None | Refuse generation when pending files lack composable plans. |
| --dry-run | boolean | False | Preview generated migrations without writing files. |
| --param | str (repeatable) | [] | Freeze a data expression parameter as name=JSON or name=text. |
| --show-managed-values | boolean | False | Include data literals in --plan output. |

`--help` displays command help.

## `dbwarden make-data-migration`

Generate versioned managed-row and transformation migrations.

| Argument or option | Type | Default | Description |
|---|---|---|---|
| description | str | None |  |
| --database, -d | str | None |  |
| --offline | boolean | False |  |
| --dry-run | boolean | False |  |
| --plan | boolean | False |  |
| --sql | boolean | False |  |
| --param | str (repeatable) | [] |  |
| --show-managed-values | boolean | False |  |
| --split-at-severity | str | None |  |

`--help` displays command help.

## `dbwarden new`

Create a new manual migration file.

| Argument or option | Type | Default | Description |
|---|---|---|---|
| description | str | required | Description of the migration |
| --version | str | None | Version of the migration |
| --database, -d | str | None | Target database name |
| --type, -t | str | 'versioned' | Migration type: versioned (default), runs_always/ra, runs_on_change/roc |

`--help` displays command help.

## `dbwarden migrate`

Apply pending migrations to the database.

| Argument or option | Type | Default | Description |
|---|---|---|---|
| --count, -c | int | None | Number of migrations to apply |
| --to-version, -t | str | None | Migrate to a specific version |
| --verbose, -v | boolean | False | Enable verbose logging |
| --database, -d | str | None | Target database name |
| --all, -a | boolean | False | Run migrations on all databases sequentially |
| --baseline | boolean | False | Mark migrations as applied without executing |
| --reapply-data | boolean | False | Explicitly reapply data migrations that were successfully rolled back |
| --with-backup, -b | boolean | False | Create a backup before migrating |
| --backup-dir | str | None | Directory for backup files |
| --dry-run | boolean | False | Show what would be applied without executing |
| --data | boolean | False | With --dry-run, verify and render frozen data bundle details without writes |
| --sandbox | boolean | False | Apply migrations in a temporary sandbox database |
| --apply-seeds | boolean | False | Apply pending seeds after migrations (overrides config) |
| --perf | boolean | False | Log per-SQL-statement timing breakdowns |
| --defer-snapshots | boolean | False | Write one final schema snapshot instead of one after every migration |
| --max-severity | str | None | Severity ceiling: SAFE, INFO, WARN, CRITICAL. Stops before higher/UNKNOWN files; exit 3. |
| --force | boolean | False | Acknowledge operation risks; does not raise the severity ceiling. |

`--help` displays command help.

## `dbwarden rollback`

Rollback the last applied migration.

| Argument or option | Type | Default | Description |
|---|---|---|---|
| --count, -c | int | None | Number of migrations to rollback |
| --to-version, -t | str | None | Rollback to a specific version |
| --verbose, -v | boolean | False | Enable verbose logging |
| --database, -d | str | None | Target database name |
| --perf | boolean | False | Log per-SQL-statement timing breakdowns |

`--help` displays command help.

## `dbwarden downgrade`

Downgrade to a specific migration version by reverting applied migrations.

| Argument or option | Type | Default | Description |
|---|---|---|---|
| --to, -t | str | required | Target version to downgrade to |
| --verbose, -v | boolean | False | Enable verbose logging |
| --database, -d | str | None | Target database name |
| --perf | boolean | False | Log per-SQL-statement timing breakdowns |

`--help` displays command help.

## `dbwarden make-rollback`

Generate a rollback SQL file for a given migration file.

| Argument or option | Type | Default | Description |
|---|---|---|---|
| migration_file | str | required | Path to migration SQL file |

`--help` displays command help.

## `dbwarden snapshot`

Snapshot the DDL schema of a specific table.

| Argument or option | Type | Default | Description |
|---|---|---|---|
| table_name | str | required | Table name to snapshot |
| --database, -d | str | None | Target database name |

`--help` displays command help.

## `dbwarden history`

Show the full migration history.

| Argument or option | Type | Default | Description |
|---|---|---|---|
| --database, -d | str | None | Target database name |

`--help` displays command help.

## `dbwarden status`

Show migration status (applied and pending).

| Argument or option | Type | Default | Description |
|---|---|---|---|
| --database, -d | str | None | Target database name |
| --all, -a | boolean | False | Show status for all databases |
| --all-environments | boolean | False | Show status for all registered environments |

`--help` displays command help.

## `dbwarden check`

Run the schema safety analyzer.

| Argument or option | Type | Default | Description |
|---|---|---|---|
| version | str | None | Version to classify with --write-plan. |
| --out, -o | str | 'txt' | Output format (json, txt) |
| --force | boolean | False | Allow warning-level changes to pass |
| --database, -d | str | None | Target database name |
| --write-plan | boolean | False | Statically classify SQL into hash-bound plans; no live database. |
| --all | boolean | False | Classify all migration files; unresolved files produce exit 4. |
| --data | boolean | False | Also verify live declarative data convergence. |

`--help` displays command help.

## `dbwarden check-db`

Inspect the live database schema.

| Argument or option | Type | Default | Description |
|---|---|---|---|
| --out, -o | str | 'txt' | Output format (json, yaml, sql, txt) |
| --database, -d | str | None | Target database name |

`--help` displays command help.

## `dbwarden check-impact`

Analyze impact of a migration on your codebase.

| Argument or option | Type | Default | Description |
|---|---|---|---|
| migration | str | required | Migration version or plan file path |
| --out, -o | str | 'text' | Output format: text (default) or json |
| --scan-path | str | '.' | Directory to scan for affected code |
| --deep | boolean | False | Enable deep introspection (imports models live) |
| --verbose, -v | boolean | False | Show INFO-level operations in scan |
| --database, -d | str | None | Target database name |

`--help` displays command help.

## `dbwarden diff`

Show structural differences between models and database (read-only, no files written).

| Argument or option | Type | Default | Description |
|---|---|---|---|
| --out, -o | str | 'table' | Output format: table (default), json, sql |
| --verbose, -v | boolean | False | Enable verbose logging |
| --database, -d | str | None | Target database name |
| --offline | boolean | False | Use model state file instead of live database snapshot |

`--help` displays command help.

## `dbwarden config`

Display current Python configuration.

| Argument or option | Type | Default | Description |
|---|---|---|---|

`--help` displays command help.

## `dbwarden version`

Display dbwarden version and compatibility information.

| Argument or option | Type | Default | Description |
|---|---|---|---|

`--help` displays command help.

## `dbwarden lock-status`

Check if migration is currently locked.

| Argument or option | Type | Default | Description |
|---|---|---|---|
| --database, -d | str | None | Target database name |

`--help` displays command help.

## `dbwarden unlock`

Release the migration lock.

| Argument or option | Type | Default | Description |
|---|---|---|---|
| --database, -d | str | None | Target database name |
| --force, -f | boolean | False | Skip confirmation and force-release the lock |

`--help` displays command help.

## `dbwarden merge`

Merge divergent migration histories after a branch merge.

| Argument or option | Type | Default | Description |
|---|---|---|---|
| --dry-run | boolean | False | Preview reconciliation and superseded files without writing. |
| --split-at-severity | str | None | Split reconciliation at SAFE, INFO, WARN, or CRITICAL. |
| --database, -d | str | None | Target database name |
| --rename-column | str (repeatable) | None | Column rename to confirm (format: table.old=new) |
| --rename-table | str (repeatable) | None | Table rename to confirm (format: old=new) |
| --force, -f | boolean | False | Force marking hand-edited migrations |
| --commit | boolean | False | Create a git commit with the changes |
| --json | boolean | False | Output results as JSON |
| --verbose, -v | boolean | False | Enable verbose logging |

`--help` displays command help.

## `dbwarden rebase`

Recover a disposable environment after a merge.

| Argument or option | Type | Default | Description |
|---|---|---|---|
| --database, -d | str | None | Target database name |
| --yes, -y | boolean | False | Skip confirmation prompts |
| --force, -f | boolean | False | Force operation even against persistent environments |
| --check, --dry-run | boolean | False | Only check what would happen, don't make changes |
| --verbose, -v | boolean | False | Enable verbose logging |

`--help` displays command help.

## `dbwarden reconcile`

Recover a persistent environment after a dirty merge.

| Argument or option | Type | Default | Description |
|---|---|---|---|
| --force | boolean | False | Acknowledge operation risks in reconciliation. |
| --split-at-severity | str | None | Split reconciliation by severity. |
| environment | str | required | Environment name to reconcile |
| --database, -d | str | None | Target database name |
| --rename-column | str (repeatable) | None | Column rename to confirm |
| --dry-run | boolean | False | Only show what would happen |
| --verbose, -v | boolean | False | Enable verbose logging |

`--help` displays command help.

## `dbwarden database`

List configured databases

| Argument or option | Type | Default | Description |
|---|---|---|---|

`--help` displays command help.

## `dbwarden database list`

List all configured databases.

| Argument or option | Type | Default | Description |
|---|---|---|---|

`--help` displays command help.

## `dbwarden settings`

View dbwarden settings

| Argument or option | Type | Default | Description |
|---|---|---|---|

`--help` displays command help.

## `dbwarden settings show`

Show current settings configuration.

| Argument or option | Type | Default | Description |
|---|---|---|---|
| database | str | None | Database name |
| --all | boolean | False | Show all databases |

`--help` displays command help.

## `dbwarden seed`

Manage seed data

| Argument or option | Type | Default | Description |
|---|---|---|---|

`--help` displays command help.

## `dbwarden seed create`

Create a new seed file.

| Argument or option | Type | Default | Description |
|---|---|---|---|
| description | str | required | Description for the seed |
| --type, -t | str | 'sql' | Seed type: sql or python |
| --verbose, -v | boolean | False | Enable verbose logging |
| --database, -d | str | None | Target database name |

`--help` displays command help.

## `dbwarden seed apply`

Apply pending seeds.

| Argument or option | Type | Default | Description |
|---|---|---|---|
| --version, -v | str | None | Apply a specific seed version |
| --dry-run | boolean | False | Show what would be applied without executing |
| --database, -d | str | None | Target database name |
| --all, -a | boolean | False | Apply seeds on all databases |
| --verbose, -v | boolean | False | Enable verbose logging |

`--help` displays command help.

## `dbwarden seed list`

List seed files and their applied status.

| Argument or option | Type | Default | Description |
|---|---|---|---|
| --database, -d | str | None | Target database name |
| --all, -a | boolean | False | Show seeds for all databases |
| --verbose, -v | boolean | False | Enable verbose logging |
| --prune | boolean | False | Remove tracking records for seed files that no longer exist |

`--help` displays command help.

## `dbwarden seed export`

Export code seeds to ROC SQL files for stateless application.

| Argument or option | Type | Default | Description |
|---|---|---|---|
| --database, -d | str | None | Target database name |
| --all, -a | boolean | False | Export seeds for all configured databases |
| --output-dir, -o | str | 'seeds' | Output directory for ROC seed files (default: seeds/) |

`--help` displays command help.

## `dbwarden seed rollback`

Rollback seed tracking records (allows re-application).

| Argument or option | Type | Default | Description |
|---|---|---|---|
| --count, -c | int | None | Number of seeds to rollback |
| --to-version, -t | str | None | Rollback to a specific version |
| --database, -d | str | None | Target database name |
| --all, -a | boolean | False | Rollback seeds in all databases |
| --verbose, -v | boolean | False | Enable verbose logging |

`--help` displays command help.

## `dbwarden plugin`

Manage dbwarden plugins

| Argument or option | Type | Default | Description |
|---|---|---|---|

`--help` displays command help.

## `dbwarden plugin categories`

List built-in and loaded plugin migration groups in execution order.

| Argument or option | Type | Default | Description |
|---|---|---|---|
| --format, -f | str | 'table' | Output format: table or json |

`--help` displays command help.

## `dbwarden plugin list`

List installed dbwarden plugins.

| Argument or option | Type | Default | Description |
|---|---|---|---|
| --format, -f | str | 'table' | Output format: table (default) or json |

`--help` displays command help.

## `dbwarden plugin info`

Show plugin metadata.

| Argument or option | Type | Default | Description |
|---|---|---|---|
| dist_name | str | required | Plugin distribution name |
| --format, -f | str | 'table' | Output format: table (default) or json |

`--help` displays command help.

## `dbwarden plugin trust`

Allow a community plugin to load.

| Argument or option | Type | Default | Description |
|---|---|---|---|
| dist_name | str | required | Community plugin distribution name |

`--help` displays command help.

## `dbwarden plugin untrust`

Remove trust for a community plugin.

| Argument or option | Type | Default | Description |
|---|---|---|---|
| dist_name | str | required | Community plugin distribution name |

`--help` displays command help.

## `dbwarden plugin add`

Install a dbwarden plugin.

| Argument or option | Type | Default | Description |
|---|---|---|---|
| dist_name | str | required | Plugin distribution name |
| --uv | boolean | False | Install with `uv add` instead of pip |
| --version | str | None | Pin an exact version to install (e.g. 1.2.0) |
| --dry-run | boolean | False | Show the install plan without installing |

`--help` displays command help.

## `dbwarden plugin remove`

Uninstall a dbwarden plugin.

| Argument or option | Type | Default | Description |
|---|---|---|---|
| dist_name | str | required | Plugin distribution name |
| --uv | boolean | False | Uninstall with `uv remove` instead of pip |
| --dry-run | boolean | False | Show the uninstall plan without uninstalling |

`--help` displays command help.

## `dbwarden data`

Render and document declarative data migrations

| Argument or option | Type | Default | Description |
|---|---|---|---|

`--help` displays command help.

## `dbwarden data render`

Command group.

| Argument or option | Type | Default | Description |
|---|---|---|---|
| --database, -d | str | None |  |
| --model | str | None |  |
| --format | str | 'text' |  |
| --include | str | 'schema,constraints,relationships,data,transitions' |  |
| --only-with-data | boolean | False |  |
| --relationship-depth | int range | 0 |  |
| --show-managed-values | boolean | False |  |
| --param | str (repeatable) | None |  |

`--help` displays command help.

## `dbwarden data describe`

Command group.

| Argument or option | Type | Default | Description |
|---|---|---|---|
| --database, -d | str | None |  |
| --model | str | None |  |
| --format | str | 'text' |  |
| --output | path | None |  |
| --include | str | 'schema,constraints,relationships,data,transitions' |  |
| --only-with-data | boolean | False |  |
| --relationship-depth | int range | 0 |  |
| --show-managed-values | boolean | False |  |
| --param | str (repeatable) | None |  |

`--help` displays command help.

## `dbwarden data docs`

Command group.

| Argument or option | Type | Default | Description |
|---|---|---|---|
| --output | path | required |  |
| --database, -d | str | None |  |
| --diagrams | str | None |  |
| --show-managed-values | boolean | False |  |
| --param | str (repeatable) | None |  |

`--help` displays command help.

## `dbwarden data transition`

Validate, plan, and audit data transitions

| Argument or option | Type | Default | Description |
|---|---|---|---|

`--help` displays command help.

## `dbwarden data transition validate`

Command group.

| Argument or option | Type | Default | Description |
|---|---|---|---|
| name | str | None |  |
| --database, -d | str | None |  |
| --param | str (repeatable) | None |  |

`--help` displays command help.

## `dbwarden data transition plan`

Command group.

| Argument or option | Type | Default | Description |
|---|---|---|---|
| name | str | required |  |
| --database, -d | str | None |  |
| --format | str | 'text' |  |
| --param | str (repeatable) | None |  |

`--help` displays command help.

## `dbwarden data transition dry-run`

Command group.

| Argument or option | Type | Default | Description |
|---|---|---|---|
| name | str | required |  |
| --database, -d | str | None |  |
| --probes | boolean | False |  |
| --param | str (repeatable) | None |  |

`--help` displays command help.

## `dbwarden data transition render`

Command group.

| Argument or option | Type | Default | Description |
|---|---|---|---|
| name | str | required |  |
| --database, -d | str | None |  |
| --format | str | 'text' |  |
| --show-managed-values | boolean | False |  |
| --param | str (repeatable) | None |  |

`--help` displays command help.

## `dbwarden data transition describe`

Command group.

| Argument or option | Type | Default | Description |
|---|---|---|---|
| name | str | required |  |
| --database, -d | str | None |  |
| --format | str | 'text' |  |
| --output | path | None |  |
| --param | str (repeatable) | None |  |

`--help` displays command help.

## `dbwarden data transition audit`

Command group.

| Argument or option | Type | Default | Description |
|---|---|---|---|
| name | str | required |  |
| --database, -d | str | None |  |
| --format | str | 'json' |  |

`--help` displays command help.

## `dbwarden data transition reconcile`

Command group.

| Argument or option | Type | Default | Description |
|---|---|---|---|
| migration_id | str | required |  |
| --database, -d | str | None |  |
| --apply | boolean | False |  |
| --decision | str | None |  |

`--help` displays command help.

## `dbwarden data transition new`

Command group.

| Argument or option | Type | Default | Description |
|---|---|---|---|
| name | str | required |  |
| --database, -d | str | None |  |
| --manual | boolean | False |  |
| --from-table | str | None |  |
| --from-model | str | None |  |
| --source-snapshot | str | None |  |
| --to-model | str (repeatable) | None |  |
| --map | str (repeatable) | None |  |
| --source-key | str | None |  |
| --key | str (repeatable) | None |  |
| --where | str (repeatable) | None |  |
| --preserve-source, --drop-source | boolean | True |  |
| --acknowledge-drop | boolean | False |  |
| --coverage | str | 'all' |  |
| --overlap | str | 'error' |  |
| --priority | str (repeatable) | None |  |

`--help` displays command help.

## `dbwarden data-transition`

Validate, plan, and audit data transitions

| Argument or option | Type | Default | Description |
|---|---|---|---|

`--help` displays command help.

## `dbwarden data-transition validate`

Command group.

| Argument or option | Type | Default | Description |
|---|---|---|---|
| name | str | None |  |
| --database, -d | str | None |  |
| --param | str (repeatable) | None |  |

`--help` displays command help.

## `dbwarden data-transition plan`

Command group.

| Argument or option | Type | Default | Description |
|---|---|---|---|
| name | str | required |  |
| --database, -d | str | None |  |
| --format | str | 'text' |  |
| --param | str (repeatable) | None |  |

`--help` displays command help.

## `dbwarden data-transition dry-run`

Command group.

| Argument or option | Type | Default | Description |
|---|---|---|---|
| name | str | required |  |
| --database, -d | str | None |  |
| --probes | boolean | False |  |
| --param | str (repeatable) | None |  |

`--help` displays command help.

## `dbwarden data-transition render`

Command group.

| Argument or option | Type | Default | Description |
|---|---|---|---|
| name | str | required |  |
| --database, -d | str | None |  |
| --format | str | 'text' |  |
| --show-managed-values | boolean | False |  |
| --param | str (repeatable) | None |  |

`--help` displays command help.

## `dbwarden data-transition describe`

Command group.

| Argument or option | Type | Default | Description |
|---|---|---|---|
| name | str | required |  |
| --database, -d | str | None |  |
| --format | str | 'text' |  |
| --output | path | None |  |
| --param | str (repeatable) | None |  |

`--help` displays command help.

## `dbwarden data-transition audit`

Command group.

| Argument or option | Type | Default | Description |
|---|---|---|---|
| name | str | required |  |
| --database, -d | str | None |  |
| --format | str | 'json' |  |

`--help` displays command help.

## `dbwarden data-transition reconcile`

Command group.

| Argument or option | Type | Default | Description |
|---|---|---|---|
| migration_id | str | required |  |
| --database, -d | str | None |  |
| --apply | boolean | False |  |
| --decision | str | None |  |

`--help` displays command help.

## `dbwarden data-transition new`

Command group.

| Argument or option | Type | Default | Description |
|---|---|---|---|
| name | str | required |  |
| --database, -d | str | None |  |
| --manual | boolean | False |  |
| --from-table | str | None |  |
| --from-model | str | None |  |
| --source-snapshot | str | None |  |
| --to-model | str (repeatable) | None |  |
| --map | str (repeatable) | None |  |
| --source-key | str | None |  |
| --key | str (repeatable) | None |  |
| --where | str (repeatable) | None |  |
| --preserve-source, --drop-source | boolean | True |  |
| --acknowledge-drop | boolean | False |  |
| --coverage | str | 'all' |  |
| --overlap | str | 'error' |  |
| --priority | str (repeatable) | None |  |

`--help` displays command help.
