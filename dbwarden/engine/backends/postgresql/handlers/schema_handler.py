from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from dbwarden.engine.migration_name import Change
from dbwarden.engine.core.protocol import ObjectHandler, Op, RunPhase
from dbwarden.engine.snapshot import MigrationStatement, StatementOrder


class SchemaHandler(ObjectHandler):
    object_type: str = "schema"
    op_types: tuple[str, ...] = (
        "create_schema",
        "drop_schema",
    )
    run_phase: RunPhase = RunPhase.PREAMBLE
    statement_order: StatementOrder = StatementOrder.CREATE_SCHEMA

    def extract(self, snapshot: dict[str, Any]) -> dict[str, Any]:
        return {}

    def model_spec_from_config(self, config: Any) -> dict[str, Any]:
        return {}

    def model_spec_from_tables(self, model_tables: list[Any]) -> dict[str, Any]:
        return {}

    def canonicalize(self, spec: dict[str, Any]) -> dict[str, Any]:
        return spec

    def diff(
        self,
        snap_spec: dict[str, Any],
        model_spec: dict[str, Any],
    ) -> Tuple[List[Op], List[Op]]:
        return [], []

    def emit(
        self, op: Op, db_name: Optional[str] = None
    , **kwargs: Any) -> List[MigrationStatement]:
        s = op.upgrade_attrs.get("schema", "")
        quoted_schema = str(s).replace('"', '""')
        if op.object_type == "create_schema":
            return [MigrationStatement(
                order=StatementOrder.CREATE_SCHEMA,
                upgrade_sql=f'CREATE SCHEMA IF NOT EXISTS "{quoted_schema}";',
                rollback_sql=f'DROP SCHEMA IF EXISTS "{quoted_schema}";',
            )]
        else:  # drop_schema
            return [MigrationStatement(
                order=StatementOrder.CREATE_SCHEMA,
                upgrade_sql=f'DROP SCHEMA IF EXISTS "{quoted_schema}";',
                rollback_sql=f'CREATE SCHEMA IF NOT EXISTS "{quoted_schema}";',
            )]
