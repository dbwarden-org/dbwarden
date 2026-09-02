# `status`

Show migration status (applied vs pending).

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
