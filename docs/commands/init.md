# `init`

Initialize DBWarden project scaffolding.

## Usage

```bash
$ dbwarden init
$ dbwarden init --database primary
```

## What it does

- creates `migrations/` and `migrations/<database>/` if missing
- creates/updates config scaffold (`dbwarden.py`) if needed
- does not mutate your database schema

## Notes

- safe to run multiple times
- first command to run in a new project

See also: [Configuration](../configuration/index.md)
