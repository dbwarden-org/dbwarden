# `downgrade`

Revert applied migrations to reach a specific target version.

## Usage

```bash
$ dbwarden downgrade --to 0005 --database primary
```

## Options

- `--to`, `-t` (required) - Target version to downgrade to
- `--database`, `-d`
- `--verbose`, `-v`

## Notes

- reads `-- rollback` sections from migration files and applies them in reverse order
- only reverts versions after the target version; versions at or before the target are preserved
- same lock discipline as `migrate` and `rollback`
- fails if the target version has not been applied

See also: [rollback](rollback.md)
