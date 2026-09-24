# `status`

Show migration status (applied vs pending).

Pending rows include recorded severity and their state under configured `max_severity`: pending, deferred, or blocked by an earlier version. JSON includes trust reasons, blocking versions, and deferred age. Status reads plans without reparsing SQL; applied historical files need no severity plan.

## Usage

```bash
$ dbwarden status --database primary
$ dbwarden status --all
```

## Options

- `--database`, `-d`
- `--all`, `-a`
- `--all-environments`: show status for all registered environments

## Notes

- run before and after migration execution
- supports multi-database status with `--all`

See also: [Your First Migration](../getting-started/first-migration.md)
