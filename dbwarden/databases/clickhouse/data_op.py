from __future__ import annotations

from dataclasses import dataclass


@dataclass
class DataOp:
    """An authored SQL descriptor; construction does not execute or register it.

    Attributes:
        name: Author-assigned operation label.
        forward: The SQL to execute forward.
        rollback: SQL to undo, or ``None`` for irreversible ops.
        requires_confirmation: If True, the emitter comments out SQL for review.
    """
    name: str
    forward: str
    rollback: str | None = None
    requires_confirmation: bool = False


def data_op(
    *,
    name: str,
    forward: str,
    rollback: str | None = None,
    requires_confirmation: bool = False,
) -> DataOp:
    """Build a ClickHouse SQL descriptor for an explicit migration or plugin.

    The schema differ does not discover these descriptors. Execution and
    repeat behavior depend on the migration file an authoring tool writes.

    Args:
        name: Author-assigned operation name; history tracks migration files.
        forward: The SQL to execute forward.
        rollback: SQL to undo, or ``None`` for irreversible (a warning is
            shown at apply time).
        requires_confirmation: Comment out SQL for review when emitting the op.

    Example::

        from dbwarden.databases.clickhouse import data_op

        drop_old = data_op(
            name="drop_2023_01",
            forward="ALTER TABLE events DROP PARTITION '202301'",
            rollback=None,
            requires_confirmation=True,
        )
    """
    return DataOp(
        name=name,
        forward=forward,
        rollback=rollback,
        requires_confirmation=requires_confirmation,
    )
