# 9. Merges

A split sends one source to many targets. A merge is the opposite: several
historical sources flow into one current target, and every non-key output is
computed by an explicit, deterministic rule. `merge_sources()` is the contract
that makes a many-to-one migration reviewable instead of a last-write-wins guess.

## What you'll learn

- Why a merge needs explicit grouping keys, projection, and value rules
- How `from_source(source, identity=, map=, priority=)` projects a common shape
- How `merge_sources(*inputs, key=, values=, allow_empty=False)` groups and reduces
- How `aggregate(...)` and `winner(...)` compute each non-key output
- How to batch the merge with `execution=batch(...)`
- Which merge shapes are rejected, and why ClickHouse cannot run one

## Prerequisites

- Completed [8. Splits and priority](./08-splits-and-priority.md).
- The runnable [merges example](../../../examples/data-migrations/merges/).

## Step 1: The problem: two ledgers, one account table

Two legacy systems recorded the same accounts with different amounts and names:

```text
legacy_a(id, account, amount, name) ─┐
                                     ├─▶ account(id, total, name)
legacy_b(id, account, amount, name) ─┘
```

Rows are grouped by `account`. `total` is the sum of all contributions, and `name`
comes from the highest-priority source. Without an explicit rule, "which name
wins?" and "do duplicate amounts add or overwrite?" have no defensible answer. A
merge declares the answer up front.

## Step 2: Pin both sources

Every input is an independently pinned `historical_table`. In the example both
happen to live in the same snapshot, but each pin is still exact:

```python
left = historical_table("legacy_a", snapshot="primary__0001_legacy_merge_sources")
right = historical_table("legacy_b", snapshot="primary__0001_legacy_merge_sources")
```

A merge cannot read the same historical table twice, and it cannot use a plain
mapped model as an input.

## Step 3: Project a common shape with `from_source`

`from_source(source, identity=, map=, priority=)` gives each input a stable
identity, a named projection, and a priority:

```python
from_source(
    left,
    identity=["id"],
    map={"id": col("account"), "amount": col("amount"), "name": col("name")},
    priority=0,
)
```

- `identity` names non-null, unique source keys. They must be integer or string
  columns so byte ordering is stable; cast anything else explicitly.
- `map` names the fields this input contributes. Every input must project the
  **same set of names** with compatible types — here `id`, `amount`, `name`.
- `priority` is an integer, and priorities must be distinct across inputs. Lower
  numbers win for `winner`.

Translation to runtime is total: a merge input that uses a Python callable or a
wider expression is rejected, and projection names cannot use the reserved
`_dbw_` prefix.

## Step 4: Group and reduce with `merge_sources`

```python
source = merge_sources(
    from_source(left, identity=["id"],
                map={"id": col("account"), "amount": col("amount"), "name": col("name")},
                priority=0),
    from_source(right, identity=["id"],
                map={"id": col("account"), "amount": col("amount"), "name": col("name")},
                priority=1),
    key=["id"],
    values={"total": aggregate("sum", col("amount")), "name": winner(col("name"))},
    allow_empty=False,
)
```

- `key=["id"]` are the grouping columns. They must be present in every input's
  projection and must resolve to integers or strings.
- `values` gives a rule for every non-key output. A bare column is not allowed;
  each field needs `aggregate(...)` or `winner(...)`. Key columns must not appear
  in `values`.
- `allow_empty=False` (the default) refuses to retire the sources when all inputs
  are empty. Set `allow_empty=True` only when an empty merged target is a reviewed
  outcome; the flag is part of the merge's identity.

The result is a `MergedSource`, not a table. Assign it to `DataTransition.source`
and the merge grouping key becomes the default `source_identity`.

Here the two inputs are `legacy_a` (priority 0) and `legacy_b` (priority 1). Group
`account` 10 exists in both, so its amounts are summed and its name comes from the
lower-priority `legacy_a` row.

| Rule | Contract |
|---|---|
| `aggregate("sum", expr)` | Sum non-null numeric contributions. |
| `aggregate("avg", expr)` | Average non-null numeric contributions. |
| `aggregate("min", expr)` / `aggregate("max", expr)` | Ordered minimum or maximum under pinned backend semantics. |
| `aggregate("count", expr)` | Count non-null expression results. |
| `aggregate("require_equal", expr)` | Require every contribution (including null placement) to be equal. Strings compare as UTF-8 bytes, not the database collation. |
| `winner(expr)` | Select the lowest numeric source priority, then order by the source identity's UTF-8 bytes. |

`sum` and `avg` require a numeric expression. An unknown policy, a raw reducer, or
an implicit winner is rejected.

## Step 5: Declare the transition and batch it

The merged source drives a normal single-target transition. Two settings are
special to merges: completion must be `preserve` or an acknowledged `drop`, and
completion coverage must be complete (`coverage="all"`, `on_unmatched="error"`).

```python
class ConsolidateAccounts(DataTransition):
    source = source
    targets = [
        into(
            Account,
            map={Account.id: col("id"), Account.total: col("total"),
                 Account.name: col("name")},
            key=[Account.id],
            on_conflict="ignore_if_equivalent",
        )
    ]
    execution = batch(size=2, key=["id"])
    on_complete = "preserve"
    rollback = "restore_preserved_source"
```

`execution=batch(size=2, key=["id"])` splits the write into ascending keyset
chunks of two, keyed by the grouping key. The chunks stay in one statement
transaction; only after all of them succeed does the source retirement run. See
[10. Capture, archive, and batching](./10-capture-archive-batching.md) for the full
batch contract. Because `on_complete="preserve"`, rollback renames the preserved
sources back and removes only the rows the merge owns.

## Step 6: Run the example

```bash
cd merges
bash scripts/01-legacy-schema.sh
bash scripts/02-consolidate.sh
```

Script `01` creates and seeds both ledgers and reports the pinned snapshot; script
`02` materializes the merge, generates, and applies it:

```text
=== 02: Consolidate the ledgers ===
Pinning snapshot: primary__0001_legacy_merge_sources
Generated: primary__0002_consolidate_accounts.sql (2 ops, max severity WARN)
Migrations completed successfully: 1 migrations applied.
account:             [(10, 12, 'first'), (20, 5, 'twenty'), (30, 11, 'thirty')]
legacy tables left:  []
preserved sources:   2
```

Read the result against the seed data:

| account | contributions | total | name | why |
|---|---|---|---|---|
| 10 | 2 + 3 (legacy_a), 7 (legacy_b) | 12 | `first` | sum of amounts; winner is priority-0 legacy_a's lowest id |
| 20 | 5 (legacy_a) | 5 | `twenty` | single contribution |
| 30 | 11 (legacy_b) | 11 | `thirty` | single contribution |

`legacy tables left: []` means both sources were retired (renamed, not deleted),
and `preserved sources: 2` shows the two deterministic preservation tables. The
internal staging name `_dbwarden_merge_<id>` you see in the safety log is a
transition-owned table, not a desired model.

## Step 7: What a merge refuses

The compiler rejects a merge that cannot be reproduced deterministically:

- a revision that reuses the old declaration — changed grouping, sources, or
  field rules require a **new** declaration with pinned retained sources;
- different projection fields between inputs, or ambiguous field rules;
- nullable or duplicate source identities, or missing grouping keys;
- a merge output that reuses the reserved `_dbw_` prefix;
- reading the same historical table twice;
- all inputs empty with `allow_empty=False`.

Batches aside, a merge with `on_complete="archive"` is not allowed: archive
completion belongs to a normal single-source transition, so merges use `preserve`
or an acknowledged `drop`.

On MySQL and MariaDB, every source is frozen under one multi-table rename before
any source is read, so writes between renames cannot create an inconsistent cut.
ClickHouse rejects merges entirely because it cannot establish the required
source-write boundary, and the failure happens at planning rather than as
placeholder SQL.

## Recap

- A merge is many sources into exactly one target, with explicit grouping and one rule per non-key field.
- `from_source` gives each input a stable identity, a shared projection, and a distinct priority.
- `merge_sources` groups by `key` and reduces each `values` field with `aggregate(...)` or `winner(...)`; `allow_empty=False` is the default.
- `winner` orders by priority then source-identity UTF-8 bytes; `aggregate` is limited to the listed policies.
- Changed merge semantics need a new declaration; ClickHouse rejects merges.

## What's next

Record preimages, move retired rows to an archive, and bound long writes with
batches: [10. Capture, archive, and batching](./10-capture-archive-batching.md).
