from __future__ import annotations

from typing import Any

from dbwarden.engine.core.statement_order import MigrationStatement, StatementOrder
from dbwarden.logging import get_logger

logger = get_logger()


def _build_pg_meta_sql(
    table: str,
    column: str,
    col_type: str,
    snap_type: str,
    to_pg_column: dict[str, Any],
    from_pg_column: dict[str, Any],
    backend: str,
) -> list[MigrationStatement]:
    if backend != "postgresql":
        return []

    mapping = [
        ("collation", "pg_collation"),
        ("storage", "pg_storage"),
        ("compression", "pg_compression"),
        ("generated", "pg_generated"),
        ("identity", "pg_identity"),
        ("identity_start", "pg_identity_start"),
        ("identity_increment", "pg_identity_increment"),
        ("identity_min", "pg_identity_min"),
        ("identity_max", "pg_identity_max"),
    ]
    stmts: list[MigrationStatement] = []
    for key, model_key in mapping:
        from_val = from_pg_column.get(key)
        to_val = to_pg_column.get(model_key)
        if from_val == to_val:
            continue
        if key == "collation":
            up = f"ALTER TABLE {table} ALTER COLUMN {column} TYPE {col_type} COLLATE \"{to_val}\";" if to_val else f"ALTER TABLE {table} ALTER COLUMN {column} TYPE {col_type};"
            rb = f"ALTER TABLE {table} ALTER COLUMN {column} TYPE {snap_type} COLLATE \"{from_val}\";" if from_val else f"ALTER TABLE {table} ALTER COLUMN {column} TYPE {snap_type};"
        elif key == "storage":
            if to_val is None and from_val is None:
                continue
            if to_val is None:
                continue
            up = f"ALTER TABLE {table} ALTER COLUMN {column} SET STORAGE {to_val};"
            rb = f"ALTER TABLE {table} ALTER COLUMN {column} SET STORAGE {from_val};" if from_val else f"ALTER TABLE {table} ALTER COLUMN {column} SET STORAGE EXTENDED;"
        elif key == "compression":
            if to_val is None and from_val is None:
                continue
            if to_val is None:
                continue
            up = f"ALTER TABLE {table} ALTER COLUMN {column} SET COMPRESSION {to_val};"
            rb = f"ALTER TABLE {table} ALTER COLUMN {column} SET COMPRESSION {from_val};" if from_val else f"ALTER TABLE {table} ALTER COLUMN {column} SET COMPRESSION DEFAULT;"
        elif key == "generated":
            if to_val:
                up = f"ALTER TABLE {table} ALTER COLUMN {column} SET EXPRESSION AS ({to_val});"
            else:
                up = f"ALTER TABLE {table} ALTER COLUMN {column} DROP EXPRESSION;"
            if from_val:
                rb = f"ALTER TABLE {table} ALTER COLUMN {column} SET EXPRESSION AS ({from_val});"
            else:
                rb = f"ALTER TABLE {table} ALTER COLUMN {column} DROP EXPRESSION;"
        elif key == "identity":
            if to_val:
                up = f"ALTER TABLE {table} ALTER COLUMN {column} ADD GENERATED {to_val} AS IDENTITY;"
            else:
                up = f"ALTER TABLE {table} ALTER COLUMN {column} DROP IDENTITY IF EXISTS;"
            if from_val:
                rb = f"ALTER TABLE {table} ALTER COLUMN {column} ADD GENERATED {from_val} AS IDENTITY;"
            else:
                rb = f"ALTER TABLE {table} ALTER COLUMN {column} DROP IDENTITY IF EXISTS;"
        elif key in ("identity_start", "identity_increment", "identity_min", "identity_max"):
            pg_key = {
                "identity_start": "START WITH",
                "identity_increment": "INCREMENT BY",
                "identity_min": "MINVALUE",
                "identity_max": "MAXVALUE",
            }[key]
            if to_val is None:
                continue
            up = f"ALTER TABLE {table} ALTER COLUMN {column} SET {pg_key} {to_val};"
            rb = f"ALTER TABLE {table} ALTER COLUMN {column} SET {pg_key} {from_val};" if from_val is not None else f"-- No ALTER needed: remove {pg_key} for {table}.{column}"
        else:
            continue
        stmts.append(MigrationStatement(
            order=StatementOrder.ALTER_COLUMN_TYPE,
            upgrade_sql=up, rollback_sql=rb,
        ))
    return stmts


def _prepend_pg_preamble(
    upgrade_sql: str,
    rollback_sql: str,
    changes: list[Any],
    database: str | None,
) -> tuple[str, str, list[Any]]:
    try:
        from dbwarden.config import get_database, get_multi_db_config
        mc = get_multi_db_config()
        db_name = database or mc.default
        config = get_database(db_name)
        if config.database_type != "postgresql":
            return upgrade_sql, rollback_sql, changes

        if any(getattr(config, attr, None) for attr in (
            "pg_sequences", "pg_domains", "pg_functions", "pg_triggers",
            "pg_roles", "pg_default_privileges", "pg_composite_types",
            "pg_extended_statistics", "pg_event_triggers",
        )):
            from dbwarden.engine.core.registry import RegistryDriver

            _reg = RegistryDriver()
            _up_ops, _rb_ops = _reg.run({"domains": {}, "sequences": {}, "functions": {}, "tables": {}, "roles": {}, "default_privileges": {}, "composite_types": {}, "extended_stats": {}, "event_triggers": {}}, [], config)
            if _up_ops:
                _stmts = _reg.emit_all(_up_ops, db_name=db_name)
                _pg_up = "\n".join(s.upgrade_sql for s in _stmts)
                _pg_rb = "\n".join(s.rollback_sql for s in reversed(_stmts))
                if _pg_up.strip():
                    if upgrade_sql.strip():
                        upgrade_sql = _pg_up + "\n\n" + upgrade_sql
                    else:
                        upgrade_sql = _pg_up
                if _pg_rb.strip():
                    if rollback_sql.strip():
                        rollback_sql = _pg_rb + "\n\n" + rollback_sql
                    else:
                        rollback_sql = _pg_rb
                from dbwarden.engine.migration_name import Change
                for op in reversed(_up_ops):
                    _name = (
                        op.upgrade_attrs.get("domain_name")
                        or op.upgrade_attrs.get("seq_name")
                        or op.upgrade_attrs.get("function_name")
                        or op.upgrade_attrs.get("role_name")
                        or op.upgrade_attrs.get("type_name")
                        or ""
                    )
                    if _name:
                        changes.insert(0, Change(type="add", object_type="preamble", name=_name))
    except Exception:
        logger.exception("Failed to prepend PostgreSQL preamble; preamble objects omitted")
    return upgrade_sql, rollback_sql, changes
