# `settings`

View dbwarden configuration. All database settings are defined in Python code via
declarative database classes or `database_config()`, so `settings show` is a read-only command for inspecting
the current configuration.

## `settings show`

### Usage

```bash
$ dbwarden settings show
$ dbwarden settings show primary
$ dbwarden settings show --all
```

### Options

- `--all`, `-a`: show all configured databases

### Example output

```
Database: PRIMARY (default)
  • Default: True
  • Type: SQLite
  • URL: sqlite:///./app.db
  • Migrations Directory: migrations/primary
  • Migration Table: _dbwarden_migrations
  • Seed Table: _dbwarden_seeds
  • Model Paths: ['app']
  • Dev Database Type: None
  • Dev Database URL: None
  • Overlap Models: False
```

## See also

- [`database list`](./database.md)
- [Configuration docs](../configuration/index.md)
