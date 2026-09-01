from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum
from typing import Any, List, Optional, Protocol, Tuple

from dbwarden.engine.core.ordering import OrderingConstraint
from dbwarden.engine.core.statement_order import MigrationStatement, StatementOrder


class RunPhase(IntEnum):
    """When a handler's diff runs relative to table extraction.

    Attributes:
        PREAMBLE: Handler runs before table extraction (e.g. extensions, roles).
        DIFF: Handler runs after table extraction (e.g. columns, indexes).
    """
    PREAMBLE = 0
    DIFF = 1


@dataclass
class Op:
    """An atomic schema operation produced by a handler's diff.

    Attributes:
        object_type: Registration key (e.g. ``"column"``, ``"index"``).
        upgrade_attrs: Attributes for the upgrade (forward) migration.
        rollback_attrs: Attributes for the rollback migration.
        irreversible: If True, the rollback is a no-op comment.
    """
    object_type: str
    upgrade_attrs: dict[str, Any] = field(default_factory=dict)
    rollback_attrs: dict[str, Any] = field(default_factory=dict)
    irreversible: bool = False


def op_to_dict(op: Op, *, skip_none: bool = False) -> dict[str, Any]:
    attrs = {
        k: v
        for k, v in op.upgrade_attrs.items()
        if not skip_none or v is not None
    }
    data = {"type": op.object_type, **attrs}
    if op.rollback_attrs:
        data["__rollback_attrs"] = op.rollback_attrs
    if op.irreversible:
        data["__irreversible"] = True
    return data


class ObjectHandler(Protocol):
    """Protocol that every schema object handler must implement.

    A handler participates in the extract -> diff -> emit pipeline for
    one object type (e.g. ``"column"``, ``"index"``, ``"role"``). Core
    and plugins both register handlers; the ``RegistryDriver`` orchestrates
    them in ordering-anchored order.

    Attributes:
        object_type: Registration key. Must be unique across all handlers.
        run_phase: Whether this handler runs before (PREAMBLE) or after
            (DIFF) table extraction.
        statement_order: Where emitted statements appear in the migration.
        ordering: Anchors that control when this handler runs relative
            to others.
    """

    object_type: str
    run_phase: RunPhase
    statement_order: StatementOrder
    ordering: OrderingConstraint

    def extract(self, snapshot: dict[str, Any]) -> dict[str, Any]:
        """Extract the handler's spec from a live database snapshot.

        Args:
            snapshot: The full schema snapshot from
                ``extract_full_schema_snapshot``.

        Returns:
            A backend-specific spec dict for comparison.
        """
        ...

    def model_spec_from_config(self, config: Any) -> dict[str, Any]:
        """Build a model spec from configuration (for PREAMBLE handlers).

        Args:
            config: The dbwarden configuration object.

        Returns:
            A spec dict representing the desired state from config.
        """
        ...

    def model_spec_from_tables(
        self, model_tables: list[Any]
    ) -> dict[str, Any]:
        """Build a model spec from SQLAlchemy model tables (for DIFF handlers).

        Args:
            model_tables: List of ``ModelTable`` instances.

        Returns:
            A spec dict representing the desired state from models.
        """
        ...

    def canonicalize(self, spec: dict[str, Any]) -> dict[str, Any]:
        """Normalize a spec for comparison.

        Args:
            spec: A spec from ``extract`` or ``model_spec_from_*``.

        Returns:
            A canonical form suitable for diff comparison.
        """
        ...

    def diff(
        self,
        snap_spec: dict[str, Any],
        model_spec: dict[str, Any],
    ) -> Tuple[List[Op], List[Op]]:
        """Compare snapshot and model specs to produce upgrade and rollback ops.

        Args:
            snap_spec: Canonicalized spec from the live database.
            model_spec: Canonicalized spec from the desired state.

        Returns:
            A tuple of (upgrade_ops, rollback_ops).
        """
        ...

    def emit(
        self, op: Op, db_name: Optional[str] = None,
        **kwargs: Any,
    ) -> List[MigrationStatement]:
        """Convert an Op into executable migration statements.

        Args:
            op: The operation to emit.
            db_name: Optional database name for multi-db setups.
            **kwargs: Backend-specific options (e.g. ``schema``).

        Returns:
            A list of ``MigrationStatement`` objects.
        """
        ...
