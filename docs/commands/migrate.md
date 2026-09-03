# `migrate`

Apply pending migrations.

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
- `--with-backup`, `-b`
- `--backup-dir`
- `--dry-run`: preview changes without applying
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

## Dirty environment detection

If the target environment has unreconciled merge changes (dirty merge), `dbwarden migrate` refuses to run and directs you to run `dbwarden reconcile` first. This prevents applying the wrong migration chain to a persistent environment.

```bash
$ dbwarden migrate --database primary
Error: Environment 'primary' has unreconciled merge changes. Run 'dbwarden reconcile' first, or use --dry-run to preview.
```

See also: [Your First Migration](../getting-started/first-migration.md)
