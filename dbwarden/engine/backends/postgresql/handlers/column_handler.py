from __future__ import annotations

import json
from typing import Any, Dict, List, Optional, Tuple

from dbwarden.engine.model_discovery import generate_add_column_sql
from dbwarden.engine.core.protocol import ObjectHandler, Op, RunPhase
from dbwarden.engine.snapshot import (
    MigrationStatement,
    StatementOrder,
    normalize_type,
)


class ColumnHandler(ObjectHandler):
    object_type: str = "column"
    op_types: tuple[str, ...] = (
        "add_column",
        "drop_column",
        "alter_column_type",
        "alter_column_nullable",
        "alter_column_autoincrement",
        "alter_column_default",
        "alter_column_comment",
        "alter_pg_column_meta",
        "alter_my_column_meta",
        "rename_column",
    )
    run_phase: RunPhase = RunPhase.DIFF
    statement_order: StatementOrder = StatementOrder.ADD_COLUMN

    _db_name: str | None = None

    def extract(self, snapshot: dict[str, Any]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for tname, tdata in snapshot.get("tables", {}).items():
            result[tname] = dict(tdata.get("columns", {}))
        return result

    def model_spec_from_tables(self, model_tables: list[Any]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for table in model_tables:
            cols = {c.name: c for c in table.columns}
            if cols:
                result[table.name] = cols
        return result

    def model_spec_from_config(self, config: Any) -> dict[str, Any]:
        return {}

    def canonicalize(self, spec: dict[str, Any]) -> dict[str, Any]:
        return spec

    def diff(
        self,
        snap_spec: dict[str, Any],
        model_spec: dict[str, Any],
    ) -> Tuple[List[Op], List[Op]]:
        from dbwarden.engine.snapshot import (
            _get_backend,
            _model_type_str,
            _strip_ch_type_wrappers,
            snap_to_model_key,
            detect_renames,
        )

        upgrade_ops: list[Op] = []
        rollback_ops: list[Op] = []

        all_tables = set(snap_spec.keys()) | set(model_spec.keys())
        for tname in sorted(all_tables):
            snap_columns = snap_spec.get(tname, {})
            model_columns_dict = model_spec.get(tname, {})
            if not snap_columns and not model_columns_dict:
                continue

            snap_pk_count = sum(
                1 for sc in snap_columns.values()
                if sc.get("primary_key")
            )
            model_pk_count = sum(1 for mc in model_columns_dict.values() if mc.primary_key)
            pk_count = max(snap_pk_count, model_pk_count)

            dropped_cols = []
            for col_name in snap_columns:
                if col_name not in model_columns_dict:
                    dropped_cols.append((col_name, snap_columns[col_name]))
            added_cols = []
            for col_name, model_col in model_columns_dict.items():
                if col_name not in snap_columns:
                    added_cols.append((col_name, model_col))

            renames = detect_renames(tname, dropped_cols, added_cols)
            renamed_old = {old for old, _ in renames}
            renamed_new = {new for _, new in renames}

            for old_name, new_name in renames:
                upgrade_ops.append(Op(
                    object_type="rename_column",
                    upgrade_attrs={
                        "table": tname,
                        "old_name": old_name,
                        "new_name": new_name,
                    },
                    rollback_attrs={
                        "table": tname,
                        "old_name": new_name,
                        "new_name": old_name,
                    },
                ))
                rollback_ops.append(Op(
                    object_type="rename_column",
                    upgrade_attrs={
                        "table": tname,
                        "old_name": new_name,
                        "new_name": old_name,
                    },
                    rollback_attrs={
                        "table": tname,
                        "old_name": old_name,
                        "new_name": new_name,
                    },
                ))

            backend = _get_backend(self._db_name)
            for col_name in snap_columns:
                if col_name not in model_columns_dict:
                    continue
                if col_name in renamed_old or col_name in renamed_new:
                    continue
                snap_col = snap_columns[col_name]
                model_col = model_columns_dict[col_name]

                snap_raw = snap_col.get("type", "")
                if backend == "clickhouse":
                    snap_raw = _strip_ch_type_wrappers(snap_raw)
                    model_raw = model_col.ch_meta.get("ch_type", str(model_col.type))
                    model_raw = _strip_ch_type_wrappers(model_raw)
                else:
                    model_raw = _model_type_str(model_col.type)
                snap_type = normalize_type(snap_raw)["type"]
                model_type = normalize_type(model_raw)["type"]
                if snap_type != model_type:
                    op_model_type = model_col.ch_meta.get("ch_type", model_col.type) if backend == "clickhouse" else model_col.type
                    upgrade_ops.append(Op(
                        object_type="alter_column_type",
                        upgrade_attrs={
                            "table": tname, "column": col_name,
                            "snap_type": snap_col.get("type", ""),
                            "model_type": op_model_type,
                        },
                        rollback_attrs={
                            "table": tname, "column": col_name,
                            "snap_type": snap_col.get("type", ""),
                            "model_type": op_model_type,
                        },
                    ))
                    rollback_ops.append(Op(
                        object_type="alter_column_type",
                        upgrade_attrs={
                            "table": tname, "column": col_name,
                            "snap_type": snap_col.get("type", ""),
                            "model_type": op_model_type,
                        },
                        rollback_attrs={
                            "table": tname, "column": col_name,
                            "snap_type": snap_col.get("type", ""),
                            "model_type": op_model_type,
                        },
                    ))

                snap_nullable = snap_col.get("nullable", True)
                if snap_nullable != model_col.nullable:
                    upgrade_ops.append(Op(
                        object_type="alter_column_nullable",
                        upgrade_attrs={
                            "table": tname, "column": col_name,
                            "nullable": model_col.nullable,
                            "col_type": model_col.type,
                        },
                        rollback_attrs={
                            "table": tname, "column": col_name,
                            "nullable": snap_nullable,
                            "col_type": snap_col.get("type", ""),
                        },
                    ))
                    rollback_ops.append(Op(
                        object_type="alter_column_nullable",
                        upgrade_attrs={
                            "table": tname, "column": col_name,
                            "nullable": snap_nullable,
                            "col_type": snap_col.get("type", ""),
                        },
                        rollback_attrs={
                            "table": tname, "column": col_name,
                            "nullable": model_col.nullable,
                            "col_type": model_col.type,
                        },
                    ))

                self._diff_autoincrement(
                    tname, col_name, snap_col, model_col, pk_count,
                    backend, upgrade_ops, rollback_ops,
                )

                self._diff_default(
                    tname, col_name, snap_col, model_col,
                    backend, upgrade_ops, rollback_ops,
                )

                snap_col_comment = snap_col.get("comment")
                if snap_col_comment != model_col.comment:
                    snap_my_col = snap_col.get("my_column") or {}
                    model_my_col = model_col.my_meta or {}
                    upgrade_ops.append(Op(
                        object_type="alter_column_comment",
                        upgrade_attrs={
                            "table": tname, "column": col_name,
                            "comment": model_col.comment,
                            "previous_comment": snap_col_comment,
                            "col_type": model_col.type,
                            "nullable": model_col.nullable,
                            "autoincrement": model_col.autoincrement,
                            "my_meta": model_my_col,
                        },
                        rollback_attrs={
                            "table": tname, "column": col_name,
                            "comment": snap_col_comment,
                            "col_type": snap_col.get("type", ""),
                            "nullable": snap_nullable,
                            "autoincrement": snap_col.get("autoincrement", False),
                            "my_meta": snap_my_col,
                        },
                    ))
                    rollback_ops.append(Op(
                        object_type="alter_column_comment",
                        upgrade_attrs={
                            "table": tname, "column": col_name,
                            "comment": snap_col_comment,
                            "col_type": snap_col.get("type", ""),
                            "nullable": snap_nullable,
                            "autoincrement": snap_col.get("autoincrement", False),
                            "my_meta": snap_my_col,
                        },
                        rollback_attrs={
                            "table": tname, "column": col_name,
                            "comment": model_col.comment,
                            "col_type": model_col.type,
                            "nullable": model_col.nullable,
                            "autoincrement": model_col.autoincrement,
                            "my_meta": model_my_col,
                        },
                    ))

                snap_pg_col = snap_col.get("pg_column") or {}
                model_pg_meta = model_col.pg_meta or {}
                norm_snap_pg_col = {
                    snap_to_model_key(k): v for k, v in snap_pg_col.items()
                    if snap_to_model_key(k) not in ("pg_type",)
                }
                model_pg_meta_filtered = {
                    k: v for k, v in model_pg_meta.items()
                    if k not in ("pg_type", "pg_enum_name", "pg_enum_values")
                }
                # Physical tuning the model does not mention is not a
                # difference: a column a DBA set to MAIN storage would
                # otherwise be reported as changed on every run, and reset.
                for _tuning_key in ("pg_storage", "pg_compression"):
                    if _tuning_key not in model_pg_meta_filtered:
                        norm_snap_pg_col.pop(_tuning_key, None)
                if norm_snap_pg_col != model_pg_meta_filtered:
                    upgrade_ops.append(Op(
                        object_type="alter_pg_column_meta",
                        upgrade_attrs={
                            "table": tname, "column": col_name,
                            "col_type": model_col.type,
                            "snap_type": snap_col.get("type", ""),
                            "from_pg_column": snap_pg_col,
                            "to_pg_column": model_pg_meta,
                        },
                        rollback_attrs={
                            "table": tname, "column": col_name,
                            "col_type": snap_col.get("type", ""),
                            "snap_type": model_col.type,
                            "from_pg_column": model_pg_meta,
                            "to_pg_column": snap_pg_col,
                        },
                    ))
                    rollback_ops.append(Op(
                        object_type="alter_pg_column_meta",
                        upgrade_attrs={
                            "table": tname, "column": col_name,
                            "col_type": snap_col.get("type", ""),
                            "snap_type": model_col.type,
                            "from_pg_column": model_pg_meta,
                            "to_pg_column": snap_pg_col,
                        },
                        rollback_attrs={
                            "table": tname, "column": col_name,
                            "col_type": model_col.type,
                            "snap_type": snap_col.get("type", ""),
                            "from_pg_column": snap_pg_col,
                            "to_pg_column": model_pg_meta,
                        },
                    ))

                snap_my_col = snap_col.get("my_column") or {}
                model_my_col = model_col.my_meta or {}
                if snap_my_col != model_my_col:
                    upgrade_ops.append(Op(
                        object_type="alter_my_column_meta",
                        upgrade_attrs={
                            "table": tname, "column": col_name,
                            "col_type": model_col.type,
                            "snap_type": snap_col.get("type", ""),
                            "from_my_column": snap_my_col,
                            "to_my_column": model_my_col,
                            "nullable": model_col.nullable,
                            "default": model_col.default,
                            "comment": model_col.comment,
                            "autoincrement": model_col.autoincrement,
                            "snap_nullable": snap_col.get("nullable", True),
                            "snap_default": snap_col.get("default"),
                            "snap_comment": snap_col.get("comment"),
                        },
                        rollback_attrs={
                            "table": tname, "column": col_name,
                            "col_type": snap_col.get("type", ""),
                            "snap_type": model_col.type,
                            "from_my_column": model_my_col,
                            "to_my_column": snap_my_col,
                            "nullable": snap_col.get("nullable", True),
                            "default": snap_col.get("default"),
                            "comment": snap_col.get("comment"),
                            "autoincrement": snap_col.get("autoincrement", False),
                            "snap_nullable": model_col.nullable,
                            "snap_default": model_col.default,
                            "snap_comment": model_col.comment,
                        },
                    ))
                    rollback_ops.append(Op(
                        object_type="alter_my_column_meta",
                        upgrade_attrs={
                            "table": tname, "column": col_name,
                            "col_type": snap_col.get("type", ""),
                            "snap_type": model_col.type,
                            "from_my_column": model_my_col,
                            "to_my_column": snap_my_col,
                            "nullable": snap_col.get("nullable", True),
                            "default": snap_col.get("default"),
                            "comment": snap_col.get("comment"),
                            "autoincrement": snap_col.get("autoincrement", False),
                            "snap_nullable": model_col.nullable,
                            "snap_default": model_col.default,
                            "snap_comment": model_col.comment,
                        },
                        rollback_attrs={
                            "table": tname, "column": col_name,
                            "col_type": model_col.type,
                            "snap_type": snap_col.get("type", ""),
                            "from_my_column": snap_my_col,
                            "to_my_column": model_my_col,
                            "nullable": model_col.nullable,
                            "default": model_col.default,
                            "comment": model_col.comment,
                            "autoincrement": model_col.autoincrement,
                            "snap_nullable": snap_col.get("nullable", True),
                            "snap_default": snap_col.get("default"),
                            "snap_comment": snap_col.get("comment"),
                        },
                    ))

            for col_name, col_def in dropped_cols:
                if col_name in renamed_old:
                    continue
                upgrade_ops.append(Op(
                    object_type="drop_column",
                    upgrade_attrs={
                        "table": tname, "column": col_name,
                        "definition": col_def,
                    },
                    rollback_attrs={
                        "table": tname, "column": col_name,
                    },
                ))
                rollback_ops.append(Op(
                    object_type="add_column",
                    upgrade_attrs={
                        "table": tname, "column": col_name,
                        "definition": col_def,
                    },
                    rollback_attrs={
                        "table": tname, "column": col_name,
                    },
                ))

            for col_name, model_col in added_cols:
                if col_name in renamed_new:
                    continue
                upgrade_ops.append(Op(
                    object_type="add_column",
                    upgrade_attrs={
                        "table": tname, "column": col_name,
                        "model_column": model_col,
                    },
                    rollback_attrs={
                        "table": tname, "column": col_name,
                    },
                ))
                rollback_ops.append(Op(
                    object_type="drop_column",
                    upgrade_attrs={
                        "table": tname, "column": col_name,
                    },
                    rollback_attrs={
                        "table": tname, "column": col_name,
                    },
                ))

        return upgrade_ops, rollback_ops

    def _diff_autoincrement(
        self, tname, col_name, snap_col, model_col, pk_count, backend,
        up_ops, rb_ops,
    ):
        snap_autoinc = snap_col.get("autoincrement", False)
        model_autoinc = model_col.autoincrement
        model_type_lower = str(model_col.type).lower()
        snap_type_lower = str(snap_col.get("type", "")).lower()
        model_has_identity = bool((model_col.pg_meta or {}).get("pg_identity"))
        snap_has_identity = bool((snap_col.get("pg_column") or {}).get("identity"))

        if model_autoinc == "auto":
            # SERIAL and friends are integer columns: by the time the diff runs,
            # an autoincrementing INTEGER primary key has already been rendered
            # as SERIAL, and looking for "int" alone would resolve "auto" to
            # False and emit an autoincrement change against a column that never
            # changed.
            model_autoinc = (
                model_col.primary_key
                and pk_count <= 1
                and ("int" in model_type_lower or "serial" in model_type_lower or model_has_identity)
            )
        elif model_has_identity and not model_autoinc:
            model_autoinc = True

        if snap_has_identity and not snap_autoinc:
            snap_autoinc = True

        # Treat snapshot SERIAL/BIGSERIAL columns as autoincrement as well.
        if not snap_autoinc and "serial" in snap_type_lower:
            snap_autoinc = True

        if backend == "sqlite" and model_col.primary_key:
            # An INTEGER PRIMARY KEY is SQLite's rowid alias and is
            # autoincrementing whether or not the model says so; on any other
            # primary key AUTOINCREMENT is not legal at all. Either way there is
            # nothing to alter.
            if pk_count > 1:
                return
            if "int" in model_type_lower:
                if model_col.autoincrement in (None, "auto", True):
                    return
            else:
                return
        if model_autoinc is not None and bool(snap_autoinc) != bool(model_autoinc):
            if pk_count > 1 and backend in ("mysql", "mariadb"):
                return
            up_ops.append(Op(
                object_type="alter_column_autoincrement",
                upgrade_attrs={
                    "table": tname, "column": col_name,
                    "autoincrement": model_autoinc,
                    "col_type": model_col.type,
                    "nullable": model_col.nullable,
                },
                rollback_attrs={
                    "table": tname, "column": col_name,
                    "autoincrement": bool(snap_autoinc),
                    "col_type": snap_col.get("type", ""),
                    "nullable": snap_col.get("nullable", True),
                },
            ))
            rb_ops.append(Op(
                object_type="alter_column_autoincrement",
                upgrade_attrs={
                    "table": tname, "column": col_name,
                    "autoincrement": bool(snap_autoinc),
                    "col_type": snap_col.get("type", ""),
                    "nullable": snap_col.get("nullable", True),
                },
                rollback_attrs={
                    "table": tname, "column": col_name,
                    "autoincrement": model_autoinc,
                    "col_type": model_col.type,
                    "nullable": model_col.nullable,
                },
            ))

    def _diff_default(
        self, tname, col_name, snap_col, model_col, backend,
        up_ops, rb_ops,
    ):
        from dbwarden.engine.snapshot import _normalize_default, _normalize_mysql_default

        snap_default = _normalize_default(snap_col.get("default"))
        model_default = _normalize_default(model_col.default)
        if backend in ("mysql", "mariadb"):
            snap_default = _normalize_mysql_default(snap_col.get("default"))
            model_default = _normalize_mysql_default(model_col.default)
            snap_my_col = snap_col.get("my_column") or {}
            model_my_col = model_col.my_meta or {}
            if snap_my_col.get("my_on_update") and model_my_col.get("my_on_update") and model_default is None and snap_default == "CURRENT_TIMESTAMP":
                snap_default = None
        if backend == "postgresql":
            if model_col.autoincrement and snap_default is not None and snap_default.lower().startswith("nextval("):
                snap_default = None
        if snap_default != model_default:
            up_ops.append(Op(
                object_type="alter_column_default",
                upgrade_attrs={
                    "table": tname, "column": col_name,
                    "default": model_default,
                    "col_type": model_col.type,
                    "nullable": model_col.nullable,
                    "my_meta": model_col.my_meta or {},
                },
                rollback_attrs={
                    "table": tname, "column": col_name,
                    "default": snap_default,
                    "col_type": snap_col.get("type", ""),
                    "nullable": snap_col.get("nullable", True),
                    "my_meta": snap_col.get("my_column", {}),
                },
            ))
            rb_ops.append(Op(
                object_type="alter_column_default",
                upgrade_attrs={
                    "table": tname, "column": col_name,
                    "default": snap_default,
                    "col_type": snap_col.get("type", ""),
                    "nullable": snap_col.get("nullable", True),
                    "my_meta": snap_col.get("my_column", {}),
                },
                rollback_attrs={
                    "table": tname, "column": col_name,
                    "default": model_default,
                    "col_type": model_col.type,
                    "nullable": model_col.nullable,
                    "my_meta": model_col.my_meta or {},
                },
            ))

    def emit(
        self, op: Op, db_name: Optional[str] = None
    , **kwargs: Any) -> List[MigrationStatement]:
        from dbwarden.engine.snapshot import (
            _build_alter_default_sql,
            _build_alter_nullable_sql,
            _build_alter_type_sql,
            _build_pg_meta_sql,
            _get_backend,
            _missing_def_placeholder,
            _model_type_str,
            _mysql_column_definition_for_meta,
            _strip_ch_type_wrappers,
        )

        backend = _get_backend(db_name)
        stmts: list[MigrationStatement] = []
        table = op.upgrade_attrs["table"]

        if op.object_type == "rename_column":
            old_name = op.upgrade_attrs["old_name"]
            new_name = op.upgrade_attrs["new_name"]
            stmts.append(MigrationStatement(
                order=StatementOrder.RENAME_COLUMN,
                upgrade_sql=f"ALTER TABLE {table} RENAME COLUMN {old_name} TO {new_name}",
                rollback_sql=f"ALTER TABLE {table} RENAME COLUMN {new_name} TO {old_name}",
            ))

        elif op.object_type == "add_column":
            column = op.upgrade_attrs["column"]
            model_col = op.upgrade_attrs.get("model_column")
            if model_col:
                sql = generate_add_column_sql(table, model_col, db_name)
                stmts.append(MigrationStatement(
                    order=StatementOrder.ADD_COLUMN,
                    upgrade_sql=sql,
                    rollback_sql=f"ALTER TABLE {table} DROP COLUMN {column}",
                ))
            else:
                col_def = op.upgrade_attrs.get("definition", {})
                col_type = col_def.get("type")
                if not col_type:
                    col_type = _missing_def_placeholder(backend)
                stmts.append(MigrationStatement(
                    order=StatementOrder.ADD_COLUMN,
                    upgrade_sql=f"ALTER TABLE {table} ADD COLUMN {column} {col_type}",
                    rollback_sql=f"ALTER TABLE {table} DROP COLUMN {column}",
                ))

        elif op.object_type == "drop_column":
            column = op.upgrade_attrs["column"]
            warning = f"-- WARNING: DROPPING COLUMN {table}.{column}\n"
            col_type = op.upgrade_attrs.get("definition", {}).get("type", "")
            if not col_type:
                col_type = _missing_def_placeholder(backend)
            stmts.append(MigrationStatement(
                order=StatementOrder.DROP_COLUMN,
                upgrade_sql=f"{warning}ALTER TABLE {table} DROP COLUMN {column}",
                rollback_sql=f"ALTER TABLE {table} ADD COLUMN {column} {col_type}",
            ))

        elif op.object_type == "alter_column_type":
            column = op.upgrade_attrs["column"]
            model_type = op.upgrade_attrs.get("model_type", "")
            if not isinstance(model_type, str):
                model_type = _model_type_str(model_type)
            alter_up, alter_rb = _build_alter_type_sql(
                table, column, model_type, backend,
                old_type=op.upgrade_attrs.get("snap_type", ""),
            )
            stmts.append(MigrationStatement(
                order=StatementOrder.ALTER_COLUMN_TYPE,
                upgrade_sql=alter_up, rollback_sql=alter_rb,
            ))

        elif op.object_type == "alter_column_nullable":
            column = op.upgrade_attrs["column"]
            nullable = op.upgrade_attrs.get("nullable", True)
            col_type = op.upgrade_attrs.get("col_type", "")
            null_up, null_rb = _build_alter_nullable_sql(table, column, nullable, col_type, backend)
            stmts.append(MigrationStatement(
                order=StatementOrder.ALTER_COLUMN_NULLABLE,
                upgrade_sql=null_up, rollback_sql=null_rb,
            ))

        elif op.object_type == "alter_column_autoincrement":
            column = op.upgrade_attrs["column"]
            autoinc = op.upgrade_attrs.get("autoincrement", False)
            col_type = op.upgrade_attrs.get("col_type", "integer")
            seq_name = f"{table}_{column}_seq"
            if backend == "postgresql":
                if autoinc:
                    upgrade_sql = (
                        f"CREATE SEQUENCE IF NOT EXISTS {seq_name};\n"
                        f"ALTER TABLE {table} ALTER COLUMN {column} SET DEFAULT nextval('{seq_name}');\n"
                        f"ALTER SEQUENCE {seq_name} OWNED BY {table}.{column};"
                    )
                    rollback_sql = (
                        f"ALTER TABLE {table} ALTER COLUMN {column} DROP DEFAULT;\n"
                        f"DROP SEQUENCE IF EXISTS {seq_name};"
                    )
                else:
                    upgrade_sql = (
                        f"ALTER TABLE {table} ALTER COLUMN {column} DROP DEFAULT;\n"
                        f"DROP SEQUENCE IF EXISTS {seq_name};"
                    )
                    rollback_sql = (
                        f"CREATE SEQUENCE IF NOT EXISTS {seq_name};\n"
                        f"ALTER TABLE {table} ALTER COLUMN {column} SET DEFAULT nextval('{seq_name}');\n"
                        f"ALTER SEQUENCE {seq_name} OWNED BY {table}.{column};"
                    )
            elif backend in ("mysql", "mariadb"):
                nullable = op.upgrade_attrs.get("nullable")
                null_clause = ""
                if nullable is not None:
                    null_clause = " NOT NULL" if not nullable else " NULL"
                if autoinc:
                    upgrade_sql = f"ALTER TABLE {table} MODIFY COLUMN {column} {col_type}{null_clause} AUTO_INCREMENT;"
                    rollback_sql = f"ALTER TABLE {table} MODIFY COLUMN {column} {col_type}{null_clause};"
                else:
                    upgrade_sql = f"ALTER TABLE {table} MODIFY COLUMN {column} {col_type}{null_clause};"
                    rollback_sql = f"ALTER TABLE {table} MODIFY COLUMN {column} {col_type}{null_clause} AUTO_INCREMENT;"
            else:
                upgrade_sql = f"-- Autoincrement toggle for {table}.{column} only supported on PostgreSQL"
                rollback_sql = f"-- Autoincrement toggle for {table}.{column} only supported on PostgreSQL"
            stmts.append(MigrationStatement(
                order=StatementOrder.ALTER_COLUMN_AUTOINCREMENT,
                upgrade_sql=upgrade_sql, rollback_sql=rollback_sql,
            ))

        elif op.object_type == "alter_column_default":
            column = op.upgrade_attrs["column"]
            default = op.upgrade_attrs.get("default")
            col_type = op.upgrade_attrs.get("col_type")
            nullable = op.upgrade_attrs.get("nullable")
            my_meta = op.upgrade_attrs.get("my_meta", {})
            def_up, _ = _build_alter_default_sql(
                table, column, default, backend,
                col_type=col_type, nullable=nullable, my_meta=my_meta,
            )
            rollback_attrs = op.rollback_attrs or {}
            rb_table = rollback_attrs.get("table", table)
            rb_column = rollback_attrs.get("column", column)
            rb_default = rollback_attrs.get("default")
            rb_col_type = rollback_attrs.get("col_type", col_type)
            rb_nullable = rollback_attrs.get("nullable", nullable)
            rb_my_meta = rollback_attrs.get("my_meta", my_meta)
            def_rb, _ = _build_alter_default_sql(
                rb_table, rb_column, rb_default, backend,
                col_type=rb_col_type, nullable=rb_nullable, my_meta=rb_my_meta,
            )
            stmts.append(MigrationStatement(
                order=StatementOrder.ALTER_COLUMN_DEFAULT,
                upgrade_sql=def_up, rollback_sql=def_rb,
            ))

        elif op.object_type == "alter_column_comment":
            column = op.upgrade_attrs["column"]
            comment = op.upgrade_attrs.get("comment") or ""
            prev = op.upgrade_attrs.get("previous_comment") or ""
            raw_comment = op.upgrade_attrs.get("comment")
            raw_prev = op.upgrade_attrs.get("previous_comment")
            col_type = op.upgrade_attrs.get("col_type", "")
            nullable = op.upgrade_attrs.get("nullable")
            if backend == "sqlite":
                c = comment.replace(chr(39), chr(39)+chr(39))
                col = f"{table}.{column}"
                up = f"-- COMMENT ON COLUMN {col} IS '{c}';" if comment else f"-- COMMENT ON COLUMN {col} IS NULL;"
                rb = f"-- COMMENT ON COLUMN {col} IS '{prev}';" if prev else f"-- COMMENT ON COLUMN {col} IS NULL;"
            elif backend in ("mysql", "mariadb"):
                my_meta = op.upgrade_attrs.get("my_meta", {}) or {}
                autoinc = op.upgrade_attrs.get("autoincrement")
                up = (
                    f"ALTER TABLE {table} MODIFY COLUMN {column} "
                    f"{_mysql_column_definition_for_meta(col_type, my_meta, nullable=nullable, comment=raw_comment, autoincrement=autoinc)};"
                )
                rb = (
                    f"ALTER TABLE {table} MODIFY COLUMN {column} "
                    f"{_mysql_column_definition_for_meta(col_type, my_meta, nullable=nullable, comment=raw_prev, autoincrement=autoinc)};"
                )
            else:
                up = f"COMMENT ON COLUMN {table}.{column} IS '{comment.replace(chr(39), chr(39)+chr(39))}';" if comment else f"COMMENT ON COLUMN {table}.{column} IS NULL;"
                rb = f"COMMENT ON COLUMN {table}.{column} IS '{prev.replace(chr(39), chr(39)+chr(39))}';" if prev else f"COMMENT ON COLUMN {table}.{column} IS NULL;"
            stmts.append(MigrationStatement(
                order=StatementOrder.ALTER_COLUMN_COMMENT,
                upgrade_sql=up, rollback_sql=rb,
            ))

        elif op.object_type == "alter_pg_column_meta":
            column = op.upgrade_attrs["column"]
            stmts_list = _build_pg_meta_sql(
                table, column,
                op.upgrade_attrs.get("col_type", ""),
                op.upgrade_attrs.get("snap_type", ""),
                op.upgrade_attrs.get("to_pg_column", {}),
                op.upgrade_attrs.get("from_pg_column", {}),
                backend,
            )
            stmts.extend(stmts_list)

        elif op.object_type == "alter_my_column_meta":
            column = op.upgrade_attrs["column"]
            from_my = op.upgrade_attrs.get("from_my_column", {}) or {}
            to_my = op.upgrade_attrs.get("to_my_column", {}) or {}
            if backend in ("mysql", "mariadb"):
                col_type = op.upgrade_attrs.get("col_type", "")
                snap_type = op.upgrade_attrs.get("snap_type", "")
                nullable = op.upgrade_attrs.get("nullable")
                default = op.upgrade_attrs.get("default")
                comment = op.upgrade_attrs.get("comment")
                autoinc = op.upgrade_attrs.get("autoincrement")
                snap_nullable = op.upgrade_attrs.get("snap_nullable")
                snap_default = op.upgrade_attrs.get("snap_default")
                snap_comment = op.upgrade_attrs.get("snap_comment")
                up_def = _mysql_column_definition_for_meta(
                    col_type, to_my, nullable=nullable, default=default,
                    comment=comment, autoincrement=autoinc,
                )
                rb_def = _mysql_column_definition_for_meta(
                    snap_type, from_my, nullable=snap_nullable, default=snap_default,
                    comment=snap_comment, autoincrement=autoinc,
                )
                up = f"ALTER TABLE {table} MODIFY COLUMN {column} {up_def};"
                rb = f"ALTER TABLE {table} MODIFY COLUMN {column} {rb_def};"
                stmts.append(MigrationStatement(
                    order=StatementOrder.ALTER_COLUMN_TYPE,
                    upgrade_sql=up, rollback_sql=rb,
                ))

        return stmts
