# Conventions

## Canonicalization rules

The canonicalizer normalizes DDL before diffing. These normalizations apply:

| Rule | Example |
|------|---------|
| Whitespace normalization | `ORDER BY (a,b)` → `ORDER BY (a, b)` |
| Trailing comma removal | `(a, b,)` → `(a, b)` |
| Engine parameter normalization | ReplicatedMergeTree arguments |
| Settings key normalization | Underscore-vs-hyphen normalization |
| Type alias expansion | `INT` → `Int32`, `VARCHAR` → `String` |
| Nullable/LowCardinality wrapper normalization | Wrapping order |
| `ENGINE = Distributed(cluster, db, table)` | Shard/key delimiters |

## Defaults-as-absence

If a property matches the ClickHouse default, it is omitted from the emitted DDL. For example:

- `index_granularity = 8192` is the default and is not emitted unless explicitly set to a non-default value
- `SETTINGS` block is omitted entirely when all settings are at their defaults

This means the diff is clean: only non-default values appear in the DDL and the field is absent from declarations until overridden.

## Secrets-declare-only

Credentials are never diffed. See [Named collections](named-collections.md).

- Values of `password`, `sasl_password`, `secret_access_key` etc. are not compared between model and server
- Named collection declarations list the **keys** that should exist, not the secret values
- RBAC `identified_by` values are not stored in the model at all

## Additional model examples

### Builder vs raw dict comparison

```python
# Builder path (preferred): typed, validated, autocompletable
ch = ch_table(
    engine=replicated_merge_tree("/zk/path", "{replica}"),
    order_by=["ts", "id"],
    partition_by="toYYYYMM(ts)",
    settings=MergeTreeSettings({
        "index_granularity": 4096,
        "min_bytes_for_wide_part": "10485760",
    }),
)

# Raw dict path (escape hatch): same output, no validation
ch = {
    "engine": {
        "type": "ReplicatedMergeTree",
        "params": ["/zk/path", "{replica}"],
    },
    "order_by": ["ts", "id"],
    "partition_by": "toYYYYMM(ts)",
    "settings": {
        "index_granularity": "4096",
        "min_bytes_for_wide_part": "10485760",
    },
}
```

### Defaults-as-absence example

```python
# These two models emit identical DDL:
class Explicit(Base):
    __tablename__ = "t"
    x: Mapped[int] = mapped_column()
    class Meta(CHTableMeta):
        ch = ch_table(engine=merge_tree(), order_by="x",
                       settings=MergeTreeSettings(index_granularity=8192))

class Implicit(Base):
    __tablename__ = "t"
    x: Mapped[int] = mapped_column()
    class Meta(CHTableMeta):
        ch = ch_table(engine=merge_tree(), order_by="x")
        # index_granularity=8192 is the default, omitted from DDL
```

Both produce:

```sql
CREATE TABLE t (x Int64) ENGINE = MergeTree() ORDER BY x
```

## Two-door API

Every operation has two paths:

| Path | Purpose |
|------|---------|
| Builder (e.g., `ch_table()`, `ChRoleSpec()`) | Primary. Provides type-checking, autocomplete, and validation. Covers 95% of use cases. |
| Raw dict (e.g., `{"type": "MergeTree"}`) | Escape hatch. For infrequent or edge-case settings the builder doesn't expose. |

Raw dicts are passed through without validation:

```python
# Builder path
ch = ch_table(
    engine=merge_tree(),
    settings={"allow_experimental_inverted_index": 1},
)

# Raw path
ch = {"engine": {"type": "MergeTree"}, "settings": {"allow_experimental_inverted_index": "1"}}
```

Raw dicts are validated for structure (must match a known schema) but not for content correctness. The builder path is always preferred.

## Version support evidence

Every assertion about version support is backed by a test case in the audit harness:

```
tests/databases/clickhouse/audit/
├── 24.3/       # 31 cases, zero drift
└── 26.6/       # same 31 cases, zero drift
```

New ClickHouse versions are added by running the existing test suite against the new version. Zero branching in the canonicalizer means there is no per-version code path to update.
