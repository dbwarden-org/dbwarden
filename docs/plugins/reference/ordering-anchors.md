---
description: Public ordering anchors for dbwarden object plugins.
---

# Ordering Anchors

Object plugins position their SQL using public **anchors**, not private statement-order integers. At registration, dbwarden validates your `OrderingConstraint` and derives the internal `statement_order` from the anchors for you.

## The `Anchor` Enum

| Anchor | Meaning |
|--------|---------|
| `PREAMBLE` | Earliest setup statements (extensions, roles, schemas). |
| `BEFORE_TABLES` | Objects that must exist before tables (types, domains). |
| `AFTER_TABLES` | Objects emitted after table creation (added columns). |
| `AFTER_CONSTRAINTS` | Objects emitted after table constraints. |
| `AFTER_INDEXES` | Objects emitted after indexes. |
| `POSTAMBLE` | Final statements (views, grants that depend on everything). |

Anchors run in the order listed above.

## `OrderingConstraint`

```python
@dataclass(frozen=True)
class OrderingConstraint:
    after: tuple[Anchor, ...] = ()
    before: tuple[Anchor, ...] = ()
    after_object: tuple[str, ...] = ()   # by another handler's object_type
    before_object: tuple[str, ...] = ()
```

- `after` / `before` place the handler relative to core milestones.
- `after_object` / `before_object` place it relative to other object handlers by their `object_type`, forming a DAG that dbwarden topologically sorts.

## Examples

Extensions, first thing (PostgreSQL):

```python
ordering = OrderingConstraint(after=(Anchor.PREAMBLE,), before=(Anchor.BEFORE_TABLES,))
```

Roles, before the objects that reference them:

```python
ordering = OrderingConstraint(after=(Anchor.PREAMBLE,), before=(Anchor.BEFORE_TABLES,))
```

Grants, after everything they depend on:

```python
ordering = OrderingConstraint(after=(Anchor.AFTER_INDEXES,), before=(Anchor.POSTAMBLE,))
```

Triggers, after tables and their constraints exist:

```python
ordering = OrderingConstraint(after=(Anchor.AFTER_CONSTRAINTS,))
```

A grant handler that must run after a role handler, by object type:

```python
ordering = OrderingConstraint(after_object=("role",))
```

## Failure Modes

dbwarden rejects invalid ordering at registration or run time by raising `OrderingError`:

- **Impossible anchor pair**, `after` an anchor that comes at or after `before`:

  ```python
  OrderingConstraint(after=(Anchor.POSTAMBLE,), before=(Anchor.PREAMBLE,))
  # OrderingError: Impossible ordering: after postamble and before preamble
  ```

- **Unknown object reference**, naming an `object_type` no registered handler provides:

  ```python
  OrderingConstraint(after_object=("missing",))
  # OrderingError: Unknown object ordering reference 'missing' for '<your type>'
  ```

- **Cycle**, two handlers that each require running after the other:

  ```python
  # first: after_object=("second",);  second: after_object=("first",)
  # OrderingError: Object handler ordering cycle detected: first, second
  ```

## Why `StatementOrder` Is Private

The internal `StatementOrder` integers (and their exact values) are an implementation detail and are renumbered as core evolves. Anchors are the **public, stable contract**. Never read or set `statement_order` yourself; declare `ordering` with anchors and let dbwarden compute the rest.
