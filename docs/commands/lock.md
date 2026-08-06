# `lock-status` and `unlock`

Inspect and recover migration lock state.

## Usage

```bash
$ dbwarden lock-status --database primary
$ dbwarden unlock --database primary
```

## Options

- `--database`, `-d`

## Notes

- use `lock-status` to inspect lock state
- use `unlock` only when lock is stale and no migration is running

See also: [Migration Locking](../advanced/migration-locking.md)
