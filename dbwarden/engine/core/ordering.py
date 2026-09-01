from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from heapq import heappop, heappush
from typing import Any

from dbwarden.engine.core.statement_order import StatementOrder
from dbwarden.exceptions.engine import OrderingError


class Anchor(Enum):
    PREAMBLE = "preamble"
    BEFORE_TABLES = "before_tables"
    AFTER_TABLES = "after_tables"
    AFTER_CONSTRAINTS = "after_constraints"
    AFTER_INDEXES = "after_indexes"
    POSTAMBLE = "postamble"


@dataclass(frozen=True)
class OrderingConstraint:
    after: tuple[Anchor, ...] = ()
    before: tuple[Anchor, ...] = ()
    after_object: tuple[str, ...] = ()
    before_object: tuple[str, ...] = ()


ANCHOR_ORDER: tuple[Anchor, ...] = (
    Anchor.PREAMBLE,
    Anchor.BEFORE_TABLES,
    Anchor.AFTER_TABLES,
    Anchor.AFTER_CONSTRAINTS,
    Anchor.AFTER_INDEXES,
    Anchor.POSTAMBLE,
)

ANCHOR_STATEMENT_ORDER: dict[Anchor, StatementOrder] = {
    Anchor.PREAMBLE: StatementOrder.CREATE_EXTENSION,
    Anchor.BEFORE_TABLES: StatementOrder.CREATE_TYPE,
    Anchor.AFTER_TABLES: StatementOrder.ADD_COLUMN,
    Anchor.AFTER_CONSTRAINTS: StatementOrder.ALTER_TABLE_CONSTRAINT,
    Anchor.AFTER_INDEXES: StatementOrder.ALTER_INDEX,
    Anchor.POSTAMBLE: StatementOrder.ALTER_VIEW,
}


def validate_ordering(ordering: OrderingConstraint) -> None:
    known = set(ANCHOR_ORDER)
    unknown = [anchor for anchor in ordering.after + ordering.before if anchor not in known]
    if unknown:
        names = ", ".join(str(anchor) for anchor in unknown)
        raise OrderingError(f"Unknown ordering anchor: {names}")

    positions = {anchor: idx for idx, anchor in enumerate(ANCHOR_ORDER)}
    for after in ordering.after:
        for before in ordering.before:
            if positions[after] >= positions[before]:
                raise OrderingError(
                    f"Impossible ordering: after {after.value} and before {before.value}"
                )


def statement_order_for(ordering: OrderingConstraint | None) -> StatementOrder | None:
    if ordering is None:
        return None
    validate_ordering(ordering)
    if ordering.after:
        return ANCHOR_STATEMENT_ORDER[max(ordering.after, key=ANCHOR_ORDER.index)]
    if ordering.before:
        return ANCHOR_STATEMENT_ORDER[min(ordering.before, key=ANCHOR_ORDER.index)]
    return None


def apply_public_ordering(handler: Any) -> None:
    ordering = getattr(handler, "ordering", None)
    if ordering is not None and not isinstance(ordering, OrderingConstraint):
        raise TypeError("Object handler ordering must be an OrderingConstraint")
    if ordering is None:
        return
    order = statement_order_for(ordering)
    if order is not None:
        setattr(handler, "statement_order", order)


def order_handlers(handlers: dict[str, Any]) -> list[Any]:
    for handler in handlers.values():
        apply_public_ordering(handler)

    known_objects = set(handlers)
    edges: dict[str, set[str]] = {name: set() for name in handlers}
    indegree: dict[str, int] = {name: 0 for name in handlers}

    for object_type, handler in handlers.items():
        ordering = getattr(handler, "ordering", None)
        if ordering is None:
            continue
        for dependency in ordering.after_object:
            if dependency not in known_objects:
                raise OrderingError(
                    f"Unknown object ordering reference '{dependency}' for '{object_type}'"
                )
            if object_type not in edges[dependency]:
                edges[dependency].add(object_type)
                indegree[object_type] += 1
        for dependent in ordering.before_object:
            if dependent not in known_objects:
                raise OrderingError(
                    f"Unknown object ordering reference '{dependent}' for '{object_type}'"
                )
            if dependent not in edges[object_type]:
                edges[object_type].add(dependent)
                indegree[dependent] += 1

    positions = {name: index for index, name in enumerate(handlers)}
    ready: list[tuple[int, int, str]] = []
    for object_type, count in indegree.items():
        if count == 0:
            heappush(ready, (_handler_order_value(handlers[object_type]), positions[object_type], object_type))

    ordered: list[Any] = []
    while ready:
        _, _, object_type = heappop(ready)
        ordered.append(handlers[object_type])
        for dependent in sorted(edges[object_type], key=lambda name: positions[name]):
            indegree[dependent] -= 1
            if indegree[dependent] == 0:
                heappush(ready, (_handler_order_value(handlers[dependent]), positions[dependent], dependent))

    if len(ordered) != len(handlers):
        cycle = ", ".join(name for name, count in indegree.items() if count > 0)
        raise OrderingError(f"Object handler ordering cycle detected: {cycle}")

    return ordered


def _handler_order_value(handler: Any) -> int:
    order = getattr(handler, "statement_order", None)
    if order is None:
        return 0
    return int(order)
