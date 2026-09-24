# `check`

Analyze schema differences between your SQLAlchemy models and the live database.

`check --write-plan --all` classifies existing SQL without a database connection. Scope with `--database primary`, or replace `--all` with a version. Trusted plans remain unchanged; generated plans are never overwritten. Any unmapped statement leaves its whole file UNKNOWN and makes the command exit **4**. The report lists severity totals, unresolved reasons, and CRITICAL files for review. Live check output also names matching generated migration files, including deferred siblings. See [Safety-scoped migrations](../correctness/safety-scoped-migrations.md).

The live check also evaluates SQL that is still eligible to run: unapplied versioned migrations, every runs-always migration, and runs-on-change migrations whose checksum differs from the recorded checksum. Superseded files are ignored.

For each pending file, dbwarden uses its trusted, checksum-bound safety plan when one exists. A handwritten file without a trusted plan is classified in memory by the read-only SQL classifier; `check` does not create or update a sidecar. If the classifier cannot map the file completely, the result is `UNKNOWN` and the check fails. `--force` cannot bypass an `UNKNOWN` result.

## Usage

```bash
$ dbwarden check --database primary
$ dbwarden check --database primary --force
$ dbwarden check --database primary --data
$ dbwarden check --database primary --out json
$ dbwarden check --write-plan 0042 --database primary
$ dbwarden check --write-plan --all
```

## Options

- `--database`, `-d` - Target database
- `--out`, `-o` - Output format: `txt` or `json`
- `--force` - Acknowledge warning- and critical-level findings; it cannot bypass `UNKNOWN` SQL or declarative-data drift
- `--data` - Add live declarative-data convergence checks
- `--write-plan` - Statically classify SQL and write checksum-bound plan sidecars without connecting to a database
- `VERSION` - With `--write-plan`, classify one version
- `--all` - With `--write-plan`, classify all migration files

See the generated [CLI option inventory](../reference/cli-options.md) for the authoritative option types and defaults.

## Severity model

- `INFO` - safe changes like adding a projection or adding a new object
- `WARNING` - risky changes, including pending `WARN` operations; requires `--force`
- `ERROR` - critical changes, including pending `CRITICAL` operations; requires `--force`
- `UNKNOWN` - SQL that could not be classified completely; always blocks

## Current behavior

dbwarden runs generic safety checks for all backends, covering column type changes, nullability changes, default changes, and table operations. For ClickHouse specifically, additional checks classify changes for:

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
- critical/error-level findings require `--force`; data drift remains blocking
- unknown pending SQL remains blocking even with `--force`
- `check --data` succeeds only when the schema, pending SQL, and declared data all pass; `--force` does not convert data drift into success
- output combines live database inspection, current model metadata, and pending migration-file safety
