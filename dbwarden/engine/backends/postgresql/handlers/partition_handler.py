from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from dbwarden.engine.core.protocol import ObjectHandler, Op, RunPhase
from dbwarden.engine.snapshot import MigrationStatement, StatementOrder


def _quote_relation(name: str) -> str:
    return ".".join(
        part if part.isidentifier() and part == part.lower() else '"' + part.replace('"', '""') + '"'
        for part in name.split(".")
    )


class PartitionHandler(ObjectHandler):
    object_type: str = "partition"
    op_types: tuple[str, ...] = (
        "alter_pg_partition",
        "attach_partition",
        "detach_partition",
    )
    run_phase: RunPhase = RunPhase.DIFF
    statement_order: StatementOrder = StatementOrder.ALTER_TABLE_OPTIONS

    def extract(self, snapshot: dict[str, Any]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for tname, tdata in snapshot.get("tables", {}).items():
            pg_table = tdata.get("pg_table") or tdata.get("backend_table_spec") or {}
            if pg_table.get("backend") is not None and pg_table.get("backend") != "postgresql":
                continue
            part_decl = pg_table.get("pg_partition")
            children = pg_table.get("pg_partitions", [])
            # Only include true PG partitioned tables (those with pg_partition declared)
            if part_decl:
                entry: dict[str, Any] = {}
                entry["pg_partition"] = dict(part_decl)
                if children:
                    entry["pg_partitions"] = list(children)
                result[tname] = entry
        return result

    def model_spec_from_tables(self, model_tables: list[Any]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for table in model_tables:
            pg_table = table.pg_table or {}
            part_decl = pg_table.get("pg_partition")
            children = pg_table.get("pg_partitions", [])
            if part_decl or children:
                entry: dict[str, Any] = {}
                if part_decl:
                    entry["pg_partition"] = dict(part_decl)
                if children:
                    entry["pg_partitions"] = list(children)
                result[table.name] = entry
        for table in model_tables:
            pg_table = table.pg_table or {}
            parent = pg_table.get("pg_partition_of")
            bound = pg_table.get("pg_partition_bound")
            if parent and bound:
                parent_entry = result.setdefault(parent, {})
                parent_entry.setdefault("pg_partitions", []).append({"name": table.name, "bound": bound})
        return result

    def model_spec_from_config(self, config: Any) -> dict[str, Any]:
        return {}

    def canonicalize(self, spec: dict[str, Any]) -> dict[str, Any]:
        if not spec:
            return {}
        result: dict[str, Any] = {}
        for tname, tentry in spec.items():
            entry: dict[str, Any] = {}
            part = tentry.get("pg_partition")
            if part:
                entry["pg_partition"] = {
                    "strategy": part.get("strategy", "RANGE").upper(),
                    "columns": list(part.get("columns", [])),
                }
            children = tentry.get("pg_partitions", [])
            if children:
                entry["pg_partitions"] = sorted(
                    [{"name": c["name"], "bound": c["bound"]} for c in children],
                    key=lambda c: c["name"],
                )
            result[tname] = entry
        return result

    def diff(
        self,
        snap_spec: dict[str, Any],
        model_spec: dict[str, Any],
    ) -> Tuple[List[Op], List[Op]]:
        upgrade_ops: list[Op] = []
        rollback_ops: list[Op] = []

        all_tables = set(snap_spec.keys()) | set(model_spec.keys())
        for tname in sorted(all_tables):
            snap_entry = snap_spec.get(tname, {})
            model_entry = model_spec.get(tname, {})
            if not snap_entry and not model_entry:
                continue

            snap_part = snap_entry.get("pg_partition")
            model_part = model_entry.get("pg_partition")

            if snap_part != model_part:
                upgrade_attrs: dict[str, Any] = {
                    "table": tname,
                    "from_value": snap_part,
                    "to_value": model_part,
                }
                rollback_attrs: dict[str, Any] = {
                    "table": tname,
                    "from_value": model_part,
                    "to_value": snap_part,
                }
                upgrade_ops.append(Op(
                    object_type="alter_pg_partition",
                    upgrade_attrs=upgrade_attrs,
                    rollback_attrs=rollback_attrs,
                ))
                rollback_ops.append(Op(
                    object_type="alter_pg_partition",
                    upgrade_attrs=rollback_attrs,
                    rollback_attrs=upgrade_attrs,
                ))

            snap_children = snap_entry.get("pg_partitions", [])
            model_children = model_entry.get("pg_partitions", [])
            snap_by_name = {c["name"]: c for c in snap_children}
            model_by_name = {c["name"]: c for c in model_children}

            for cname, c in snap_by_name.items():
                if cname not in model_by_name:
                    upgrade_ops.append(Op(
                        object_type="detach_partition",
                        upgrade_attrs={"table": tname, "partition_name": cname, "bound": c["bound"]},
                        rollback_attrs={"table": tname, "partition_name": cname, "bound": c["bound"]},
                    ))
                    rollback_ops.append(Op(
                        object_type="attach_partition",
                        upgrade_attrs={"table": tname, "partition_name": cname, "bound": c["bound"]},
                        rollback_attrs={"table": tname, "partition_name": cname, "bound": c["bound"]},
                    ))
            for cname, c in model_by_name.items():
                if cname not in snap_by_name:
                    upgrade_ops.append(Op(
                        object_type="attach_partition",
                        upgrade_attrs={"table": tname, "partition_name": cname, "bound": c["bound"]},
                        rollback_attrs={"table": tname, "partition_name": cname, "bound": c["bound"]},
                    ))
                    rollback_ops.append(Op(
                        object_type="detach_partition",
                        upgrade_attrs={"table": tname, "partition_name": cname, "bound": c["bound"]},
                        rollback_attrs={"table": tname, "partition_name": cname, "bound": c["bound"]},
                    ))
                elif snap_by_name[cname]["bound"] != c["bound"]:
                    upgrade_ops.append(Op(
                        object_type="detach_partition",
                        upgrade_attrs={"table": tname, "partition_name": cname, "bound": snap_by_name[cname]["bound"]},
                        rollback_attrs={"table": tname, "partition_name": cname, "bound": c["bound"]},
                    ))
                    rollback_ops.append(Op(
                        object_type="attach_partition",
                        upgrade_attrs={"table": tname, "partition_name": cname, "bound": snap_by_name[cname]["bound"]},
                        rollback_attrs={"table": tname, "partition_name": cname, "bound": c["bound"]},
                    ))
                    upgrade_ops.append(Op(
                        object_type="attach_partition",
                        upgrade_attrs={"table": tname, "partition_name": cname, "bound": c["bound"]},
                        rollback_attrs={"table": tname, "partition_name": cname, "bound": snap_by_name[cname]["bound"]},
                    ))
                    rollback_ops.append(Op(
                        object_type="detach_partition",
                        upgrade_attrs={"table": tname, "partition_name": cname, "bound": c["bound"]},
                        rollback_attrs={"table": tname, "partition_name": cname, "bound": snap_by_name[cname]["bound"]},
                    ))

        return upgrade_ops, rollback_ops

    def emit(
        self, op: Op, db_name: Optional[str] = None
    , **kwargs: Any) -> List[MigrationStatement]:
        from dbwarden.engine.snapshot import _get_backend

        backend = _get_backend(db_name)
        if backend != "postgresql":
            return []

        stmts: list[MigrationStatement] = []
        ot = op.object_type
        table = op.upgrade_attrs["table"]

        if ot == "alter_pg_partition":
            stmts.append(MigrationStatement(
                order=StatementOrder.ALTER_TABLE_OPTIONS,
                upgrade_sql=f"-- Partition strategy changed for {table}; requires table rebuild",
                rollback_sql=f"-- Cannot revert partition change for {table}",
            ))
        elif ot == "attach_partition":
            pname = _quote_relation(op.upgrade_attrs["partition_name"])
            bound = op.upgrade_attrs["bound"]
            stmts.append(MigrationStatement(
                order=StatementOrder.ALTER_TABLE_OPTIONS,
                upgrade_sql=f"ALTER TABLE {table} ATTACH PARTITION {pname} {bound};",
                rollback_sql=f"ALTER TABLE {table} DETACH PARTITION {pname};",
            ))
        elif ot == "detach_partition":
            pname = _quote_relation(op.upgrade_attrs["partition_name"])
            stmts.append(MigrationStatement(
                order=StatementOrder.ALTER_TABLE_OPTIONS,
                upgrade_sql=f"ALTER TABLE {table} DETACH PARTITION {pname};",
                rollback_sql=f"ALTER TABLE {table} ATTACH PARTITION {pname} {op.upgrade_attrs['bound']};",
            ))

        return stmts
