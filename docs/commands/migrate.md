# `migrate`

Apply pending migrations.

`--max-severity INFO` applies a strict prefix and exits **3** before the first higher-severity or UNKNOWN file. Later files and repeatables do not execute. UNKNOWN files stop under every ceiling, including the default CRITICAL; only a file with no plan at all passes the default ceiling, governed by the missing_plan preflight policy. `--force` acknowledges risks independently of this ceiling. Dry runs report the stop and return 0; baseline records metadata without SQL. See [Safety-scoped migrations](../correctness/safety-scoped-migrations.md).

## Usage

```bash
$ dbwarden migrate --database primary
$ dbwarden migrate --all
$ dbwarden migrate --database primary --to-version 0010
$ dbwarden migrate --database primary --count 2
$ dbwarden migrate --database primary --with-backup --backup-dir ./backups
$ dbwarden migrate --database primary --baseline --to-version 0005
```

## Options

- `--database`, `-d`
- `--all`, `-a`
- `--count`, `-c`
- `--to-version`, `-t`
- `--baseline`
- `--reapply-data`: explicitly start a new data execution epoch after a completed rollback
- `--with-backup`, `-b`
- `--backup-dir`
- `--dry-run`: preview changes without applying
- `--data`: include frozen data plans and available read-only probes with `--dry-run`
- `--sandbox`: apply in a temporary sandbox database
- `--apply-seeds`: apply pending seeds after migrations
- `--defer-snapshots`: write one final schema snapshot instead of one after every migration
- `--perf`: log per-SQL-statement timing breakdowns
- `--verbose`, `-v`

## Notes

- creates metadata/lock tables if needed
- executes versioned + repeatable migrations
- uses lock protection to prevent concurrent migration mutation
- refuses to run against dirty unreconciled environments (directs to `dbwarden reconcile`)
- verifies paired SQL, plan, and frozen artifacts before applying declarative data
- records explicit data baseline acknowledgement and skipped checks; baseline is not a convergence proof

Rolled-back declarative data requires `--reapply-data` before execution can start a new epoch. `--force` does not authorize data reapply. Successful migrations remain skipped even with `--reapply-data`. See [declarative data migrations](../declarative-data-migrations.md) for journals, ownership, backend limits, and recovery after uncertain effects.

## Dirty environment detection

If the target environment has unreconciled merge changes (dirty merge), `dbwarden migrate` refuses to run and directs you to run `dbwarden reconcile` first. This prevents applying the wrong migration chain to a persistent environment.

```bash
$ dbwarden migrate --database primary
Error: Environment 'primary' has unreconciled merge changes. Run 'dbwarden reconcile' first, or use --dry-run to preview.
```

See also: [Your First Migration](../getting-started/first-migration.md)
