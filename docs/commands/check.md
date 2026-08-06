# `check`

Analyze schema differences between your SQLAlchemy models and the live database.

## Usage

```bash
$ dbwarden check --database primary
$ dbwarden check --database primary --force
$ dbwarden check --database primary --out json
```

## Options

- `--database`, `-d` - Target database
- `--out`, `-o` - Output format: `txt` or `json`
- `--force` - Allow warning-level changes to pass

## Severity model

- `INFO` - safe changes like adding a projection or adding a new object
- `WARNING` - risky changes that require `--force`
- `ERROR` - blocked changes such as partition/order key changes

## Current behavior

DBWarden runs generic safety checks for all backends, covering column type changes, nullability changes, default changes, and table operations. For ClickHouse specifically, additional checks classify changes for:

- added or removed columns
- type changes
- engine changes
- TTL changes
- `ORDER BY` changes
- `PARTITION BY` changes
- materialized view query changes
- projection additions/removals

## Notes

- warning-level changes exit non-zero unless `--force` is provided
- error-level changes remain blocking even with `--force`
- output is based on live database inspection plus current model metadata
