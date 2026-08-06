# `check-db`

Inspect live database schema.

## Usage

```bash
$ dbwarden check-db --database primary
$ dbwarden check-db --database primary --out json
$ dbwarden check-db --database primary --out yaml
```

## Options

- `--database`, `-d`
- `--out`, `-o` (`txt`, `json`, `yaml`, `sql`)

## Notes

- useful for schema inspection and diagnostics
- complements `status` and `history`

See also: [Your First Migration](../getting-started/first-migration.md)
