# `new`

Create a manual migration file.

## Usage

```bash
$ dbwarden new "manual hotfix" --database primary
$ dbwarden new "backfill users" --database primary --version 0042
$ dbwarden new "seed data" --database primary --type ra
$ dbwarden new "update view" --database primary --type roc
```

## Options

- positional `description`
- `--database`, `-d`
- `--version`
- `--type`, `-t`: Migration type: `versioned` (default), `ra` / `runs_always`, or `roc` / `runs_on_change`

## Notes

- use when change is not model-driven
- file is scaffolded with `-- upgrade` and `-- rollback` sections

See also: [Your First Migration](../getting-started/first-migration.md)
