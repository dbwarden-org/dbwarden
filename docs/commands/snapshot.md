# `snapshot`

Output the DDL schema of a specific database table.

## Usage

```bash
$ dbwarden snapshot users --database primary
```

## Options

- `TABLE` (required) - Name of the table to snapshot
- `--database`, `-d`

## Output

For standard SQL databases (SQLite, PostgreSQL, MySQL, MariaDB):

- `CREATE TABLE` statement with column types, nullability, and defaults
- `CREATE INDEX` statements
- Foreign key constraints

For ClickHouse:

- The raw `CREATE TABLE` query from `system.tables`

## Notes

- output is printed to stdout
- useful for debugging schema differences or documenting table structure
- internally uses `sqlalchemy.inspect()` for generic databases
