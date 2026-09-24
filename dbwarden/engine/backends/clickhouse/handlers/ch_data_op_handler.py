from __future__ import annotations

from typing import Any

from dbwarden.engine.core.protocol import ObjectHandler, Op, RunPhase
from dbwarden.engine.snapshot import MigrationStatement, StatementOrder


class ChDataOpHandler(ObjectHandler):
    """Emit authored data operations; schema diffing produces none."""

    object_type: str = "ch_data_op"
    op_types: tuple[str, ...] = ("apply_data_op",)
    run_phase: RunPhase = RunPhase.DIFF
    statement_order: StatementOrder = StatementOrder.ALTER_TABLE_OPTIONS

    def extract(self, snapshot: dict[str, Any]) -> dict[str, Any]:
        return {}

    def model_spec_from_tables(self, model_tables: list[Any]) -> dict[str, Any]:
        return {}

    def model_spec_from_config(self, config: Any) -> dict[str, Any]:
        return {}

    def canonicalize(self, spec: dict[str, Any]) -> dict[str, Any]:
        return spec

    def diff(
        self,
        snap_spec: dict[str, Any],
        model_spec: dict[str, Any],
    ) -> tuple[list[Op], list[Op]]:
        return [], []

    def emit(
        self, op: Op, db_name: str | None = None,
        **kwargs: Any,
    ) -> list[MigrationStatement]:
        from dbwarden.databases.clickhouse.data_op import DataOp

        data_op: DataOp | None = op.upgrade_attrs.get("data_op")
        if data_op is None:
            return []

        forward = data_op.forward
        rollback = data_op.rollback or "-- irreversible; no rollback generated"

        if data_op.requires_confirmation:
            forward = (
                f"-- DATA-OP: {data_op.name} (requires confirmation)\n"
                f"-- Remove the '-- ' prefix below to apply:\n"
                + "\n".join(f"-- {line}" for line in forward.splitlines())
            )
            rollback = (
                f"-- Rollback for {data_op.name}:\n"
                f"{rollback}"
            )

        return [
            MigrationStatement(
                order=StatementOrder.ALTER_TABLE_OPTIONS,
                upgrade_sql=forward,
                rollback_sql=rollback,
            )
        ]
