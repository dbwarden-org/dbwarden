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
- `--verbose`, `-v`

## Notes

- creates metadata/lock tables if needed
- executes versioned + repeatable migrations
- uses lock protection to prevent concurrent migration mutation

See also: [Your First Migration](../getting-started/first-migration.md)
