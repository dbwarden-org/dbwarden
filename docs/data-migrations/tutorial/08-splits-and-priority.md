# 8. Splits and priority

The previous page sent every source row to every target. Real splits are more
selective: a source row goes to one of several targets depending on a predicate,
and overlapping predicates are resolved by an explicit priority rather than by
declaration order.

## What you'll learn

- How one source fans out to several targets with `overlap="fan_out"`
- How `overlap="priority"` with `coverage="all_assigned_once"` routes each row to exactly one target
- Why every priority target needs a distinct integer `priority=`, and what the effective predicates look like
- How target foreign keys order the writes
- Which failures — ambiguous identities, uncovered rows, foreign-key cycles — are caught before any write

## Prerequisites

- Completed [7. Historical transitions](./07-historical-transitions.md).
- The runnable [transitions example](../../../examples/data-migrations/transitions/).

## Step 1: `fan_out` — one row, several targets

The transitions example sets `overlap="fan_out"` and leaves `coverage` at its
default `all`:

```python
overlap = "fan_out"
targets = [
    into(Person, map={Person.id: legacy.id, Person.name: legacy.full_name},
         key=[Person.id], on_conflict="ignore_if_equivalent"),
    into(Customer, map={Customer.person_id: legacy.id,
                        Customer.tier: func.lower(func.split_part(legacy.email, "@", 2))},
         key=[Customer.person_id], on_conflict="ignore_if_equivalent"),
]
```

An `into(...)` without a `where=` predicate matches every source row. Under
`fan_out`, matching several targets is the intent, so a row legitimately populates
both `person` and `customer`. `coverage="all"` still requires every source row to
match *at least one* target. `fan_out` permits only the edges you actually list;
it is not "match anything".

## Step 2: `priority` — one row, one of several targets

When targets have predicates that can both be true, and a row must land in exactly
one, use priority. It requires two settings together: `coverage="all_assigned_once"`
and `overlap="priority"`. Each target then carries a distinct integer `priority=`,
and the lowest number wins.

```python
from app.models import Customer, Person
from dbwarden.data import DataTransition, func, historical_table, into, literal

legacy = historical_table("legacy_people", snapshot="primary__0001_legacy_schema")

IS_EXAMPLE = func.lower(func.split_part(legacy.email, "@", 2)) == literal("example.com")


class RouteLegacyPeople(DataTransition):
    source = legacy
    source_identity = [legacy.id]
    coverage = "all_assigned_once"
    overlap = "priority"
    on_complete = "preserve"
    rollback = "restore_preserved_source"
    targets = [
        into(
            Customer,
            map={Customer.person_id: legacy.id,
                 Customer.tier: func.lower(func.split_part(legacy.email, "@", 2))},
            key=[Customer.person_id],
            where=IS_EXAMPLE,
            priority=10,
            on_conflict="ignore_if_equivalent",
        ),
        into(
            Person,
            map={Person.id: legacy.id, Person.name: legacy.full_name},
            key=[Person.id],
            priority=20,
            on_conflict="ignore_if_equivalent",
        ),
    ]
```

The example project ships only the `fan_out` form; this variant is the same split
with routing added. Here the `Customer` target claims `example.com` addresses at
priority 10, and `Person` is the priority-20 default (no `where`, so always true).

Priority is not a tie-breaker applied after the fact. The compiler rewrites each
target's *effective* predicate to exclude rows already claimed by a lower-priority
target, then `all_assigned_once` checks that every source row has exactly one
effective assignment:

- a row matching no effective predicate fails coverage;
- a row matching two effective predicates fails overlap.

`coverage="all_assigned_once"` and `overlap="priority"` are two halves of one
setting. Supplying one without the other is a validation error, and `priority=`
values must be distinct integers.

## Step 3: Validate and run the priority variant

```bash
dbwarden data transition validate RouteLegacyPeople
```

```text
Data declarations are valid.
```

Generate and apply as usual:

```bash
dbwarden make-migrations "route legacy people"
dbwarden migrate --force
```

```text
Generated: primary__0002_route_legacy_people.sql (3 ops, max severity WARN)
Migrations completed successfully: 1 migrations applied.
person:   [(2, 'Alan Turing'), (3, 'Grace Hopper')]
customer: [(1, 'example.com')]
```

Ada (`ada@example.com`) matched both predicates, so the priority-10 `Customer`
target won and she landed only in `customer`. Alan and Grace matched only the
default `Person` target. Compare that with the `fan_out` split, where Ada would
have produced a `person` row *and* a `customer` row.

## Step 4: Target foreign keys order the writes

Targets are not written in the order you list them. The compiler builds a
dependency graph from the foreign keys between target tables, then topologically
sorts it so a referenced row exists before a row that points at it. Given
`customer.person_id → person.id`, `person` is written first even if `Customer`
is declared first.

Two cases fail before execution:

- a cycle between targets, because deferred constraints are unsupported:
  `Transition target foreign-key cycle requires deferred constraints, which are unsupported`;
- a target foreign key pointing at a historical source you are removing:
  `Transition target <name> has a foreign key to removed historical source <source>`.

## Step 5: What fails before any write

Beyond the compile-time policy checks, an applied migration runs read-only guards
before it mutates anything. These are the ones a split typically trips:

| Guard | Failure |
|---|---|
| Source identity is non-null | `Source identity contains NULL` |
| Source identity is unique | `Source identity is not unique` |
| Coverage | `Source rows are not covered by any target` |
| Overlap (`error`, `exactly_once_match`, `all_assigned_once`) | `Source rows match multiple targets` |
| `max_rows` | `Transition row limit exceeded` |

No target row is written until the guard step passes, so an ambiguous identity or
an overlapping assignment is reported instead of half-applied.

## Step 6: Review before you run

```bash
dbwarden data transition describe RouteLegacyPeople --database primary
dbwarden data transition plan RouteLegacyPeople --database primary --format json
dbwarden data transition dry-run RouteLegacyPeople --database primary --probes
```

`describe` prints the resolved coverage and overlap and each target's conflict
policy. `plan` emits the dependency-ordered operations. `dry-run --probes` runs
only the read-only guards and reports observed counts without writing.

## Recap

- `overlap="fan_out"` lets one row deliberately populate several targets.
- `overlap="priority"` with `coverage="all_assigned_once"` routes each row to exactly one target using distinct integer `priority=` values; lower wins.
- Effective predicates exclude already-claimed rows, then coverage and overlap are both enforced.
- Target foreign keys determine write order; cycles and references to a removed source fail planning.
- Identity, coverage, and overlap guards run before any target write.

## What's next

Combine several historical sources into one target with deterministic rules:
[9. Merges](./09-merges.md).
