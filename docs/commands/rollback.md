# `rollback`

Rollback applied migrations using `-- rollback` SQL sections.

## Usage

```bash
$ dbwarden rollback --database primary
$ dbwarden rollback --database primary --count 2
$ dbwarden rollback --database primary --to-version 0007
```

## Options

- `--database`, `-d`
- `--count`, `-c`
- `--to-version`, `-t`
- `--verbose`, `-v`

## Notes

- rollback runs in reverse order
- same lock discipline as migrate

See also: [Your First Migration](../getting-started/first-migration.md)
