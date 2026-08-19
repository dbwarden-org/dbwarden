from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from dbwarden.engine.core.protocol import ObjectHandler, Op, RunPhase
from dbwarden.engine.snapshot import MigrationStatement, StatementOrder


def _enum_types_from_tables(tables: list[Any]) -> dict[str, list[str]]:
    """Collect PostgreSQL enum types referenced by model/snapshot tables."""
    enums: dict[str, list[str]] = {}
    for table in tables:
        if isinstance(table, dict):
            columns = table.get("columns", {})
        else:
            columns = getattr(table, "columns", None) or {}
        if isinstance(columns, dict):
            columns = list(columns.values())
        for col in columns:
            pg_type: dict[str, Any] | None = None
            if hasattr(col, "pg_meta"):
                pg_meta = getattr(col, "pg_meta", None) or {}
                if isinstance(pg_meta, dict):
                    pg_type = pg_meta.get("pg_type")
            elif isinstance(col, dict):
                pg_type = col.get("pg_type")
            if pg_type and pg_type.get("kind") == "enum":
                type_name = pg_type.get("type_name", "")
                values = pg_type.get("values", [])
                if type_name and type_name not in enums:
                    enums[type_name] = list(values)
    return enums


class TypeHandler(ObjectHandler):
    object_type: str = "type"
    op_types: tuple[str, ...] = (
        "create_type",
        "drop_type",
    )
    run_phase: RunPhase = RunPhase.DIFF
    statement_order: StatementOrder = StatementOrder.CREATE_TYPE

    def extract(self, snapshot: dict[str, Any]) -> dict[str, Any]:
        tables = list(snapshot.get("tables", {}).values())
        return _enum_types_from_tables(tables)

    def model_spec_from_tables(self, model_tables: list[Any]) -> dict[str, Any]:
        return _enum_types_from_tables(model_tables)

    def model_spec_from_config(self, config: Any) -> dict[str, Any]:
        return {}

    def canonicalize(self, spec: dict[str, Any]) -> dict[str, Any]:
        return dict(spec)

    def diff(
        self,
        snap_spec: dict[str, Any],
        model_spec: dict[str, Any],
    ) -> Tuple[List[Op], List[Op]]:
        upgrade_ops: list[Op] = []
        rollback_ops: list[Op] = []

        snap_enums = set(snap_spec.keys())
        model_enums = set(model_spec.keys())

        for enum_name in sorted(model_enums - snap_enums):
            values = model_spec[enum_name]
            upgrade_ops.append(Op(
                object_type="create_type",
                upgrade_attrs={"enum_name": enum_name, "values": values},
                rollback_attrs={"enum_name": enum_name},
            ))
            rollback_ops.append(Op(
                object_type="drop_type",
                upgrade_attrs={"enum_name": enum_name},
                rollback_attrs={"enum_name": enum_name, "values": values},
            ))

        for enum_name in sorted(snap_enums - model_enums):
            values = snap_spec[enum_name]
            upgrade_ops.append(Op(
                object_type="drop_type",
                upgrade_attrs={"enum_name": enum_name},
                rollback_attrs={"enum_name": enum_name, "values": values},
            ))
            rollback_ops.append(Op(
                object_type="create_type",
                upgrade_attrs={"enum_name": enum_name, "values": values},
                rollback_attrs={"enum_name": enum_name},
            ))

        return upgrade_ops, rollback_ops

    def emit(
        self, op: Op, db_name: Optional[str] = None
    , **kwargs: Any) -> List[MigrationStatement]:
        enum_name = op.upgrade_attrs.get("enum_name", "")
        values = op.upgrade_attrs.get("values", [])
        quoted_name = str(enum_name).replace('"', '""')

        if op.object_type == "create_type":
            values_sql = ", ".join(repr(v) for v in values)
            return [MigrationStatement(
                order=StatementOrder.CREATE_TYPE,
                upgrade_sql=f"CREATE TYPE \"{quoted_name}\" AS ENUM ({values_sql});",
                rollback_sql=f"DROP TYPE IF EXISTS \"{quoted_name}\";",
            )]
        else:  # drop_type
            return [MigrationStatement(
                order=StatementOrder.CREATE_TYPE,
                upgrade_sql=f"DROP TYPE IF EXISTS \"{quoted_name}\";",
                rollback_sql=f"-- Type {quoted_name} definition was not captured" if not values else f"CREATE TYPE \"{quoted_name}\" AS ENUM ({', '.join(repr(v) for v in values)});",
            )]
