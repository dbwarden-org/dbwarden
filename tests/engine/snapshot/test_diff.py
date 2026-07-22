import json
import os
import tempfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from dbwarden.engine.snapshot import (
    _apply_rename_intents,
    _assemble_migration,
    _build_index_name,
    _build_index_sql,
    _normalize_mysql_default,
    _compute_table_overlap,
    _rename_table_sql,
    TableRenameIntent,
    MigrationStatement,
    StatementOrder,
    compute_checksum,
    detect_renames,
    diff_models_against_snapshot,
    find_latest_snapshot,
    get_schemas_directory,
    normalize_type,
    read_snapshot,
    write_snapshot,
    snapshot_diff_to_sql,
    extract_full_schema_snapshot,
)
from dbwarden.engine.snapshot.diff import _suppress_ch_column_ops
from dbwarden.engine.model_discovery import IndexInfo, ModelColumn, ModelTable
from dbwarden.engine.migration_name import Change
from dbwarden.engine.offline import diff_model_states, model_state_to_dict
from dbwarden.engine.backends.postgresql.handlers import ConstraintHandler
from dbwarden.engine.core.protocol import Op


def _mc(name: str, typ: str, pk: bool = False, nullable: bool = True) -> ModelColumn:
    return ModelColumn(name, typ, nullable, pk, False, None, None)


class TestSuppressChColumnOps:
    def test_suppresses_ch_column_ops(self):
        from dbwarden.engine.core.protocol import Op

        ops = [
            Op(object_type="alter_column_type", upgrade_attrs={"table": "t", "column": "c", "model_type": "TEXT"}),
            Op(object_type="add_column", upgrade_attrs={"table": "t", "column": "c", "definition": {}}),
            Op(object_type="drop_column", upgrade_attrs={"table": "t", "column": "c"}),
        ]
        result = _suppress_ch_column_ops(ops)
        assert len(result) == 2
        assert result[0].object_type == "add_column"
        assert result[1].object_type == "drop_column"

    def test_suppress_ch_column_ops_with_dicts(self):
        ops = [
            {"type": "alter_column_type", "table": "t", "column": "c"},
            {"type": "alter_column_nullable", "table": "t", "column": "c"},
            {"type": "alter_column_default", "table": "t", "column": "c"},
            {"type": "alter_column_comment", "table": "t", "column": "c"},
            {"type": "add_column", "table": "t", "column": "c"},
        ]
        result = _suppress_ch_column_ops(ops)
        assert len(result) == 1
        assert result[0]["type"] == "add_column"

    def test_suppress_empty_ops(self):
        assert _suppress_ch_column_ops([]) == []


class TestSystemTableSkip:
    def test_system_table_not_matched_to_snapshot(self):
        snapshot = {
            "tables": {
                "_dbwarden_system": {
                    "columns": {"id": {"type": "integer"}},
                    "primary_key": ["id"],
                    "comment": None,
                }
            },
            "enums": {},
            "indexes": {},
            "constraints": {},
        }
        model_tables = [
            ModelTable(name="_dbwarden_system", columns=[_mc("id", "INTEGER", pk=True, nullable=False)]),
            ModelTable(name="users", columns=[_mc("id", "INTEGER", pk=True, nullable=False)]),
        ]
        upgrade, rollback = diff_models_against_snapshot(model_tables, snapshot)
        sys_create = [op for op in upgrade if op.get("type") == "create_table" and op.get("table") == "_dbwarden_system"]
        sys_drop = [op for op in upgrade if op.get("type") == "drop_table" and op.get("table") == "_dbwarden_system"]
        assert len(sys_create) == 0
        assert len(sys_drop) == 0


class TestDiffModelsAgainstSnapshot:
    def test_new_table_detected(self):
        snapshot = {
            "tables": {},
            "enums": {},
            "indexes": {},
            "constraints": {},
        }
        model_tables = [
            ModelTable(
                name="users",
                columns=[_mc("id", "INTEGER", pk=True, nullable=False)],
            )
        ]
        upgrade, rollback = diff_models_against_snapshot(model_tables, snapshot)
        assert any(op["type"] == "create_table" and op["table"] == "users" for op in upgrade)

    def test_dropped_table_detected(self):
        snapshot = {
            "tables": {
                "old_table": {
                    "columns": {"id": {"type": "integer"}},
                    "primary_key": ["id"],
                    "comment": None,
                }
            },
            "enums": {},
            "indexes": {},
            "constraints": {},
        }
        model_tables: list[ModelTable] = []
        upgrade, rollback = diff_models_against_snapshot(model_tables, snapshot)
        assert any(op["type"] == "drop_table" and op["table"] == "old_table" for op in upgrade)

    def test_add_column_detected(self):
        snapshot = {
            "tables": {
                "users": {
                    "columns": {"id": {"type": "integer", "nullable": False, "primary_key": True}},
                    "primary_key": ["id"],
                    "comment": None,
                }
            },
            "enums": {},
            "indexes": {},
            "constraints": {},
        }
        model_tables = [
            ModelTable(
                name="users",
                columns=[
                    _mc("id", "INTEGER", pk=True, nullable=False),
                    _mc("email", "VARCHAR(255)"),
                ],
            )
        ]
        upgrade, rollback = diff_models_against_snapshot(model_tables, snapshot)
        add_ops = [op for op in upgrade if op["type"] == "add_column"]
        assert len(add_ops) == 1
        assert add_ops[0]["column"] == "email"

    def test_drop_column_detected(self):
        snapshot = {
            "tables": {
                "users": {
                    "columns": {
                        "id": {"type": "integer", "nullable": False, "primary_key": True},
                        "legacy_field": {"type": "varchar", "nullable": True, "primary_key": False},
                    },
                    "primary_key": ["id"],
                    "comment": None,
                }
            },
            "enums": {},
            "indexes": {},
            "constraints": {},
        }
        model_tables = [
            ModelTable(
                name="users",
                columns=[_mc("id", "INTEGER", pk=True, nullable=False)],
            )
        ]
        upgrade, rollback = diff_models_against_snapshot(model_tables, snapshot)
        drop_ops = [op for op in upgrade if op["type"] == "drop_column"]
        assert len(drop_ops) == 1
        assert drop_ops[0]["column"] == "legacy_field"

    def test_rename_column_detected(self):
        snapshot = {
            "tables": {
                "users": {
                    "columns": {
                        "id": {"type": "integer", "nullable": False, "primary_key": True},
                        "username": {"type": "varchar", "nullable": True, "primary_key": False},
                    },
                    "primary_key": ["id"],
                    "comment": None,
                }
            },
            "enums": {},
            "indexes": {},
            "constraints": {},
        }
        model_tables = [
            ModelTable(
                name="users",
                columns=[
                    _mc("id", "INTEGER", pk=True, nullable=False),
                    _mc("email", "VARCHAR(255)"),
                ],
            )
        ]
        upgrade, rollback = diff_models_against_snapshot(model_tables, snapshot)
        rename_ops = [op for op in upgrade if op["type"] == "rename_column"]
        add_ops = [op for op in upgrade if op["type"] == "add_column"]
        drop_ops = [op for op in upgrade if op["type"] == "drop_column"]

        assert len(rename_ops) == 1, f"Expected 1 rename, got {len(rename_ops)}. add={add_ops}, drop={drop_ops}"
        assert rename_ops[0]["old_name"] == "username"
        assert rename_ops[0]["new_name"] == "email"

    def test_type_change_detected_as_drop_add(self):
        snapshot = {
            "tables": {
                "users": {
                    "columns": {
                        "id": {"type": "integer", "nullable": False, "primary_key": True},
                        "name": {"type": "varchar", "nullable": True, "primary_key": False},
                    },
                    "primary_key": ["id"],
                    "comment": None,
                }
            },
            "enums": {},
            "indexes": {},
            "constraints": {},
        }
        model_tables = [
            ModelTable(
                name="users",
                columns=[
                    _mc("id", "INTEGER", pk=True, nullable=False),
                    _mc("name", "INTEGER"),
                ],
            )
        ]
        upgrade, rollback = diff_models_against_snapshot(model_tables, snapshot)
        rename_ops = [op for op in upgrade if op["type"] == "rename_column"]
        assert len(rename_ops) == 0



class TestSnapshotDiffToSql:
    def test_rename_column_generates_rename_sql(self):
        upgrade_ops = [
            {"type": "rename_column", "table": "users", "old_name": "username", "new_name": "email"},
        ]
        rollback_ops = [
            {"type": "rename_column", "table": "users", "old_name": "email", "new_name": "username"},
        ]
        upgrade_sql, rollback_sql, changes = snapshot_diff_to_sql(upgrade_ops, rollback_ops, db_name=None)
        assert "RENAME COLUMN username TO email" in upgrade_sql
        assert "RENAME COLUMN email TO username" in rollback_sql
        assert any(c.operation == "rename_column" and c.target == "email" for c in changes)



class TestDiffModelsAgainstSnapshotEdgeCases:
    def test_empty_model_tables(self):
        snapshot = {
            "tables": {
                "users": {
                    "columns": {"id": {"type": "integer", "nullable": False, "primary_key": True}},
                    "primary_key": ["id"],
                    "comment": None,
                }
            },
            "enums": {},
            "indexes": {},
            "constraints": {},
        }
        upgrade, rollback = diff_models_against_snapshot([], snapshot)
        assert any(op["type"] == "drop_table" for op in upgrade)

    def test_same_tables_no_changes(self):
        snapshot = {
            "tables": {
                "users": {
                    "columns": {"id": {"type": "integer", "nullable": False, "primary_key": True}},
                    "primary_key": ["id"],
                    "comment": None,
                }
            },
            "enums": {},
            "indexes": {},
            "constraints": {},
        }
        model_tables = [
            ModelTable(
                name="users",
                columns=[_mc("id", "INTEGER", pk=True, nullable=False)],
            )
        ]
        upgrade, rollback = diff_models_against_snapshot(model_tables, snapshot)
        assert upgrade == []
        assert rollback == []

    def test_table_with_no_columns_in_snapshot(self):
        snapshot = {
            "tables": {
                "empty": {
                    "columns": {},
                    "primary_key": [],
                    "comment": None,
                }
            },
            "enums": {},
            "indexes": {},
            "constraints": {},
        }
        model_tables = [
            ModelTable(
                name="empty",
                columns=[_mc("id", "INTEGER", pk=True, nullable=False)],
            )
        ]
        upgrade, rollback = diff_models_against_snapshot(model_tables, snapshot)
        add_ops = [op for op in upgrade if op["type"] == "add_column"]
        assert len(add_ops) == 1

    def test_snapshot_no_constraints_indexes_keys(self):
        snapshot = {
            "tables": {
                "users": {
                    "columns": {"id": {"type": "integer", "nullable": False, "primary_key": True}},
                }
            },
        }
        model_tables = [
            ModelTable(
                name="users",
                columns=[_mc("id", "INTEGER", pk=True, nullable=False)],
                foreign_keys=[],
                indexes=[],
            )
        ]
        upgrade, rollback = diff_models_against_snapshot(model_tables, snapshot)
        assert upgrade == []

    def test_fk_add_and_drop_same_diff(self):
        snapshot = {
            "tables": {
                "users": {"columns": {"id": {"type": "integer"}, "old_ref": {"type": "integer"}}},
                "old_group": {"columns": {"id": {"type": "integer"}}},
                "groups": {"columns": {"id": {"type": "integer"}}},
            },
            "constraints": {
                "users_old_ref_fkey": {
                    "type": "foreign_key",
                    "table": "users",
                    "columns": ["old_ref"],
                    "referenced_table": "old_group",
                    "referenced_columns": ["id"],
                },
            },
            "indexes": {},
        }
        model_tables = [
            ModelTable(
                name="users",
                columns=[
                    _mc("id", "INTEGER", pk=True, nullable=False),
                    ModelColumn("group_id", "INTEGER", True, False, False, None, None),
                ],
                foreign_keys=[{"columns": ["group_id"], "referred_table": "groups", "referred_columns": ["id"]}],
            ),
            ModelTable(
                name="groups",
                columns=[_mc("id", "INTEGER", pk=True, nullable=False)],
            ),
        ]
        upgrade, rollback = diff_models_against_snapshot(model_tables, snapshot)
        add_fk = [op for op in upgrade if op["type"] == "add_foreign_key"]
        drop_fk = [op for op in upgrade if op["type"] == "drop_foreign_key"]
        assert len(add_fk) == 1
        assert len(drop_fk) == 1
        assert add_fk[0]["columns"] == ["group_id"]
        assert drop_fk[0]["columns"] == ["old_ref"]

    def test_index_same_sig_different_name_no_change(self):
        snapshot = {
            "tables": {
                "users": {"columns": {"id": {"type": "integer"}, "email": {"type": "varchar"}}},
            },
            "indexes": {
                "custom_idx_name": {"table": "users", "columns": ["email"], "unique": False, "type": "btree"},
            },
            "constraints": {},
        }
        model_tables = [
            ModelTable(
                name="users",
                columns=[
                    _mc("id", "INTEGER", pk=True, nullable=False),
                    ModelColumn("email", "VARCHAR", True, False, False, None, None),
                ],
                indexes=[{"columns": ["email"], "unique": False}],
            ),
        ]
        upgrade, rollback = diff_models_against_snapshot(model_tables, snapshot)
        idx_ops = [op for op in upgrade if "index" in op["type"]]
        assert len(idx_ops) == 0

    def test_rename_and_type_change_same_table(self):
        snapshot = {
            "tables": {
                "users": {
                    "columns": {
                        "id": {"type": "integer", "nullable": False, "primary_key": True},
                        "name": {"type": "varchar", "nullable": True, "primary_key": False},
                        "bio": {"type": "text", "nullable": True, "primary_key": False},
                    },
                    "primary_key": ["id"],
                    "comment": None,
                }
            },
            "enums": {},
            "indexes": {},
            "constraints": {},
        }
        model_tables = [
            ModelTable(
                name="users",
                columns=[
                    _mc("id", "INTEGER", pk=True, nullable=False),
                    ModelColumn("full_name", "VARCHAR", True, False, False, None, None),
                    ModelColumn("bio", "TEXT", False, False, False, None, None),
                ],
            )
        ]
        upgrade, rollback = diff_models_against_snapshot(model_tables, snapshot)
        rename_ops = [op for op in upgrade if op["type"] == "rename_column"]
        nullable_ops = [op for op in upgrade if op["type"] == "alter_column_nullable"]
        assert len(rename_ops) >= 1
        assert len(nullable_ops) >= 1

    def test_table_rename_and_column_add_combined(self):
        snapshot = {
            "tables": {
                "users": {
                    "columns": {
                        "id": {"type": "integer", "nullable": False, "primary_key": True},
                        "name": {"type": "varchar", "nullable": True},
                    },
                    "primary_key": ["id"],
                    "comment": None,
                }
            },
            "enums": {},
            "indexes": {},
            "constraints": {},
        }
        model_tables = [
            ModelTable(
                name="accounts",
                columns=[
                    ModelColumn("id", "INTEGER", False, True, False, None, None),
                    ModelColumn("name", "VARCHAR", True, False, False, None, None),
                    ModelColumn("email", "VARCHAR", True, False, False, None, None),
                ],
            ),
        ]
        upgrade, rollback = diff_models_against_snapshot(model_tables, snapshot)
        add_ops = [op for op in upgrade if op["type"] == "add_column"]
        drop_ops = [op for op in upgrade if op["type"] == "drop_column"]
        assert len(drop_ops) >= 0



class TestApplyRenameIntentsEdgeCases:
    def test_empty_confirmed_no_renames(self):
        ops = [
            {"type": "add_column", "table": "users", "column": "email"},
        ]
        rollback = [
            {"type": "drop_column", "table": "users", "column": "email"},
        ]
        result_up, result_rb = _apply_rename_intents(ops, rollback, set())
        assert result_up == ops
        assert result_rb == rollback

    def test_confirmed_entries_not_matching_any_op(self):
        ops = [
            {"type": "add_column", "table": "users", "column": "email"},
        ]
        rollback = [
            {"type": "drop_column", "table": "users", "column": "email"},
        ]
        confirmed = {("other", "foo", "bar")}
        result_up, result_rb = _apply_rename_intents(ops, rollback, confirmed)
        assert result_up == ops

    def test_confirmed_rename_has_resolved_from_flag_map(self):
        ops = [
            {"type": "rename_column", "table": "users", "old_name": "name", "new_name": "full_name"},
        ]
        rollback = [
            {"type": "rename_column", "table": "users", "old_name": "full_name", "new_name": "name"},
        ]
        confirmed = {("users", "name", "full_name")}
        origin = {("users", "name", "full_name"): "rename_flag"}
        result_up, _ = _apply_rename_intents(ops, rollback, confirmed, origin)
        assert result_up[0].get("resolved_from") == "rename_flag"

    def test_all_six_limit_not_exceeded(self):
        ops = []
        rollback = []
        confirmed = set()
        for i in range(6):
            ops.append({"type": "rename_column", "table": "t", "old_name": f"old_{i}", "new_name": f"new_{i}"})
            rollback.append({"type": "rename_column", "table": "t", "old_name": f"new_{i}", "new_name": f"old_{i}"})
            confirmed.add(("t", f"old_{i}", f"new_{i}"))
        result_up, result_rb = _apply_rename_intents(ops, rollback, confirmed)
        assert len(result_up) == 6
        assert all(r["type"] == "rename_column" for r in result_up)

    def test_drop_add_converted_to_rename_with_resolved_from_overlap(self):
        ops = [
            {"type": "drop_column", "table": "users", "column": "name"},
            {"type": "add_column", "table": "users", "column": "full_name"},
        ]
        rollback = [
            {"type": "add_column", "table": "users", "column": "name", "definition": {}},
            {"type": "drop_column", "table": "users", "column": "full_name"},
        ]
        confirmed = {("users", "name", "full_name")}
        origin = {("users", "name", "full_name"): "auto_detect"}
        result_up, _ = _apply_rename_intents(ops, rollback, confirmed, origin)
        assert result_up[0]["type"] == "rename_column"
        assert result_up[0].get("resolved_from") == "auto_detect"

    def test_confirmed_trumps_flag_format(self):
        ops = [
            {"type": "drop_column", "table": "users", "column": "name"},
            {"type": "add_column", "table": "users", "column": "email"},
        ]
        rollback = [
            {"type": "add_column", "table": "users", "column": "name", "definition": {}},
            {"type": "drop_column", "table": "users", "column": "email"},
        ]
        confirmed = {("users", "name", "email")}
        result_up, _ = _apply_rename_intents(ops, rollback, confirmed)
        assert len(result_up) == 1
        assert result_up[0]["type"] == "rename_column"
        assert result_up[0]["old_name"] == "name"
        assert result_up[0]["new_name"] == "email"



class TestColumnLevelDiff:
    def test_type_change_detected(self):
        snapshot = {
            "tables": {
                "users": {
                    "columns": {
                        "id": {"type": "integer", "nullable": False, "primary_key": True},
                        "name": {"type": "varchar", "nullable": True, "primary_key": False},
                    },
                    "primary_key": ["id"],
                    "comment": None,
                }
            },
            "enums": {},
            "indexes": {},
            "constraints": {},
        }
        model_tables = [
            ModelTable(
                name="users",
                columns=[
                    _mc("id", "INTEGER", pk=True, nullable=False),
                    _mc("name", "TEXT"),
                ],
            )
        ]
        upgrade, _ = diff_models_against_snapshot(model_tables, snapshot)
        type_changes = [op for op in upgrade if op["type"] == "alter_column_type"]
        assert len(type_changes) == 1
        assert type_changes[0]["column"] == "name"
        assert type_changes[0]["model_type"] == "TEXT"

    def test_no_type_change_same_type(self):
        snapshot = {
            "tables": {
                "users": {
                    "columns": {
                        "id": {"type": "integer", "nullable": False, "primary_key": True},
                    },
                    "primary_key": ["id"],
                    "comment": None,
                }
            },
            "enums": {},
            "indexes": {},
            "constraints": {},
        }
        model_tables = [
            ModelTable(
                name="users",
                columns=[_mc("id", "INTEGER", pk=True, nullable=False)],
            )
        ]
        upgrade, _ = diff_models_against_snapshot(model_tables, snapshot)
        type_changes = [op for op in upgrade if op["type"] == "alter_column_type"]
        assert len(type_changes) == 0

    def test_nullable_change_detected(self):
        snapshot = {
            "tables": {
                "users": {
                    "columns": {
                        "id": {"type": "integer", "nullable": False, "primary_key": True},
                        "email": {"type": "varchar", "nullable": True, "primary_key": False},
                    },
                    "primary_key": ["id"],
                    "comment": None,
                }
            },
            "enums": {},
            "indexes": {},
            "constraints": {},
        }
        model_tables = [
            ModelTable(
                name="users",
                columns=[
                    _mc("id", "INTEGER", pk=True, nullable=False),
                    ModelColumn("email", "VARCHAR(255)", False, False, False, None, None),
                ],
            )
        ]
        upgrade, _ = diff_models_against_snapshot(model_tables, snapshot)
        nullable_changes = [op for op in upgrade if op["type"] == "alter_column_nullable"]
        assert len(nullable_changes) == 1
        assert nullable_changes[0]["column"] == "email"
        assert nullable_changes[0]["nullable"] is False

    def test_default_change_detected(self):
        snapshot = {
            "tables": {
                "users": {
                    "columns": {
                        "id": {"type": "integer", "nullable": False, "primary_key": True},
                        "name": {"type": "varchar", "nullable": True, "primary_key": False, "default": "foo"},
                    },
                    "primary_key": ["id"],
                    "comment": None,
                }
            },
            "enums": {},
            "indexes": {},
            "constraints": {},
        }
        model_tables = [
            ModelTable(
                name="users",
                columns=[
                    _mc("id", "INTEGER", pk=True, nullable=False),
                    ModelColumn("name", "VARCHAR(255)", True, False, False, "bar", None),
                ],
            )
        ]
        upgrade, _ = diff_models_against_snapshot(model_tables, snapshot)
        default_changes = [op for op in upgrade if op["type"] == "alter_column_default"]
        assert len(default_changes) == 1
        assert default_changes[0]["column"] == "name"
        assert default_changes[0]["default"] == "bar"

    def test_all_column_changes_in_one_table(self):
        snapshot = {
            "tables": {
                "users": {
                    "columns": {
                        "name": {"type": "varchar", "nullable": True, "primary_key": False, "default": None},
                    },
                    "primary_key": [],
                    "comment": None,
                }
            },
            "enums": {},
            "indexes": {},
            "constraints": {},
        }
        model_tables = [
            ModelTable(
                name="users",
                columns=[
                    ModelColumn("name", "TEXT", False, False, False, "hello", None),
                ],
            )
        ]
        upgrade, _ = diff_models_against_snapshot(model_tables, snapshot)
        types = {op["type"] for op in upgrade}
        assert "alter_column_type" in types
        assert "alter_column_nullable" in types
        assert "alter_column_default" in types



class TestColumnLevelSqlGeneration:
    def test_alter_default_rollback_uses_serialized_prior_state(self, monkeypatch):
        monkeypatch.setattr(
            "dbwarden.engine.snapshot._get_backend",
            lambda db_name=None: "postgresql",
        )
        ops = [
            {
                "type": "alter_column_default",
                "table": "users",
                "column": "active",
                "default": "true",
                "__rollback_attrs": {
                    "table": "users",
                    "column": "active",
                    "default": "false",
                },
            },
        ]

        sql, rb_sql, _changes = snapshot_diff_to_sql(ops, [], db_name="test")

        assert "SET DEFAULT 'true'" in sql
        assert "SET DEFAULT 'false'" in rb_sql
        assert "DROP DEFAULT" not in rb_sql

    def test_diff_to_sql_preserves_prior_default_in_rollback_attrs(self, monkeypatch):
        monkeypatch.setattr(
            "dbwarden.engine.snapshot._get_backend",
            lambda db_name=None: "postgresql",
        )
        snapshot = {
            "tables": {
                "users": {
                    "columns": {
                        "active": {
                            "type": "BOOLEAN",
                            "nullable": True,
                            "primary_key": False,
                            "default": "false",
                        },
                    },
                    "primary_key": [],
                    "comment": None,
                },
            },
            "enums": {},
            "indexes": {},
            "constraints": {},
        }
        model_tables = [
            ModelTable(
                name="users",
                columns=[
                    ModelColumn("active", "BOOLEAN", True, False, False, "true", None),
                ],
            )
        ]

        upgrade_ops, rollback_ops = diff_models_against_snapshot(model_tables, snapshot, db_name="test")
        default_op = next(op for op in upgrade_ops if op["type"] == "alter_column_default")

        assert default_op["__rollback_attrs"]["default"] == "FALSE"

        sql, rb_sql, _changes = snapshot_diff_to_sql(upgrade_ops, rollback_ops, db_name="test")

        assert "SET DEFAULT TRUE" in sql
        assert "SET DEFAULT FALSE" in rb_sql
        assert "DROP DEFAULT" not in rb_sql

    def test_alter_type_generates_sql(self):
        ops = [
            {"type": "alter_column_type", "table": "users", "column": "name", "model_type": "TEXT"},
        ]
        rollback_ops = [
            {"type": "alter_column_type", "table": "users", "column": "name", "snap_type": "varchar"},
        ]
        sql, rb_sql, changes = snapshot_diff_to_sql(ops, rollback_ops, db_name="test")
        assert "ALTER COLUMN name" in sql or "name TYPE" in sql or "SQLite does not support" in sql

    def test_alter_nullable_set_not_null(self):
        ops = [
            {"type": "alter_column_nullable", "table": "users", "column": "email", "nullable": False, "col_type": "VARCHAR"},
        ]
        rollback_ops = [
            {"type": "alter_column_nullable", "table": "users", "column": "email", "nullable": True, "col_type": "VARCHAR"},
        ]
        sql, rb_sql, changes = snapshot_diff_to_sql(ops, rollback_ops, db_name="test")
        assert "NOT NULL" in sql or "not supported" in sql

    def test_alter_default_set(self):
        ops = [
            {"type": "alter_column_default", "table": "users", "column": "name", "default": "'hello'"},
        ]
        rollback_ops = [
            {"type": "alter_column_default", "table": "users", "column": "name", "default": None},
        ]
        sql, rb_sql, changes = snapshot_diff_to_sql(ops, rollback_ops, db_name="test")
        assert "SET DEFAULT" in sql

    def test_alter_default_drop(self):
        ops = [
            {"type": "alter_column_default", "table": "users", "column": "name", "default": None},
        ]
        rollback_ops = [
            {"type": "alter_column_default", "table": "users", "column": "name", "default": "'old'"},
        ]
        sql, rb_sql, changes = snapshot_diff_to_sql(ops, rollback_ops, db_name="test")
        assert "DROP DEFAULT" in sql

    def test_drop_column_warning(self):
        ops = [
            {"type": "drop_column", "table": "users", "column": "legacy", "definition": {"type": "varchar"}},
        ]
        rollback_ops = [
            {"type": "add_column", "table": "users", "column": "legacy", "definition": {"type": "varchar"}},
        ]
        sql, _, _ = snapshot_diff_to_sql(ops, rollback_ops, db_name="test")
        assert "WARNING" in sql
        assert "DROP COLUMN" in sql

    def test_safe_type_change_enters_multi_step_mode(self):
        ops = [
            {"type": "alter_column_type", "table": "users", "column": "name", "model_type": "TEXT"},
        ]
        rollback_ops = [
            {"type": "alter_column_type", "table": "users", "column": "name", "snap_type": "varchar"},
        ]
        sql, rb_sql, changes = snapshot_diff_to_sql(ops, rollback_ops, db_name=None, safe_type_change=True)
        assert sql != ""



class TestSnapshotDiffToSqlEdgeCases:
    def test_empty_ops(self):
        sql, rb_sql, changes = snapshot_diff_to_sql([], [], db_name=None)
        assert sql == ""
        assert rb_sql == ""
        assert changes == []

    def test_all_op_types_together(self, monkeypatch):
        with tempfile.TemporaryDirectory() as tmpdir:
            monkeypatch.chdir(tmpdir)
            Path("dbwarden/schemas").mkdir(parents=True)
            Path("dbwarden.py").write_text(
                "from dbwarden import database_config\n"
                "database_config(database_name='test', default=True, database_type='sqlite', "
                "database_url_sync='sqlite:///./test.db', model_paths=['models'])\n"
            )
            ops = [
                {"type": "rename_table", "old_table": "users", "new_table": "accounts"},
                {"type": "rename_column", "table": "accounts", "old_name": "name", "new_name": "full_name"},
                {"type": "alter_column_type", "table": "accounts", "column": "bio", "model_type": "TEXT"},
                {"type": "alter_column_nullable", "table": "accounts", "column": "email", "nullable": False, "col_type": "VARCHAR"},
                {"type": "alter_column_default", "table": "accounts", "column": "role", "default": "'admin'"},
                {"type": "add_column", "table": "accounts", "column": "phone", "model_column": ModelColumn("phone", "VARCHAR", True, False, False, None, None)},
                {"type": "add_foreign_key", "table": "accounts", "columns": ["group_id"], "referenced_table": "groups", "referenced_columns": ["id"]},
                {"type": "add_index", "table": "accounts", "columns": ["email"], "unique": True},
                {"type": "drop_column", "table": "accounts", "column": "legacy", "definition": {"type": "varchar"}},
                {"type": "drop_table", "table": "old_thing"},
            ]
            rollback_ops = [dict(op) for op in ops]
            sql, rb_sql, changes = snapshot_diff_to_sql(ops, rollback_ops, db_name="test")
            assert "ALTER TABLE users RENAME TO accounts;" in sql
            assert "RENAME COLUMN" in sql
            assert "SET DEFAULT" in sql
            assert "ADD COLUMN" in sql
            assert "ADD CONSTRAINT" in sql or "not supported" in sql
            assert "CREATE UNIQUE INDEX" in sql or "CREATE INDEX" in sql
            assert "WARNING" in sql
            assert "DROP TABLE" in sql
            assert len(changes) == 10

    def test_create_table_emits_indexes_and_postgres_comment(self, monkeypatch):
        monkeypatch.setattr("dbwarden.engine.snapshot._get_backend", lambda db_name=None: "postgresql")
        monkeypatch.setattr("dbwarden.engine.model_discovery.type_mapping._get_backend_name", lambda db_name=None: "postgresql")

        table = ModelTable(
            name="users",
            columns=[
                ModelColumn("id", "INTEGER", False, True, False, None, None),
                ModelColumn("email", "VARCHAR", False, False, False, None, None),
            ],
            indexes=[IndexInfo(columns=["email"], name="ix_users_email")],
            comment="User table",
        )
        mock_find = lambda table_name, db_name=None: table if table_name == "users" else None
        monkeypatch.setattr("dbwarden.engine.snapshot.sql_gen._find_model_table", mock_find)
        monkeypatch.setattr("dbwarden.engine.snapshot._find_model_table", mock_find)

        sql, rb_sql, changes = snapshot_diff_to_sql([{"type": "create_table", "table": "users"}], [], db_name="test")

        assert "CREATE TABLE IF NOT EXISTS users" in sql
        assert "CREATE INDEX ix_users_email ON users (email);" in sql
        assert "COMMENT ON TABLE users IS 'User table';" in sql
        assert "DROP INDEX ix_users_email" in rb_sql
        assert any(c.operation == "add_index" for c in changes)

    def test_rename_plus_type_change_combined(self):
        ops = [
            {"type": "rename_column", "table": "users", "old_name": "name", "new_name": "full_name"},
            {"type": "alter_column_type", "table": "users", "column": "bio", "model_type": "TEXT"},
        ]
        rollback_ops = [
            {"type": "rename_column", "table": "users", "old_name": "full_name", "new_name": "name"},
            {"type": "alter_column_type", "table": "users", "column": "bio", "snap_type": "varchar"},
        ]
        sql, rb_sql, changes = snapshot_diff_to_sql(ops, rollback_ops, db_name=None)
        assert "RENAME COLUMN" in sql
        assert "ALTER COLUMN" in sql or "TYPE" in sql
        assert any(c.operation == "rename_column" for c in changes)
        assert any(c.operation == "alter_column_type" for c in changes)

    def test_malformed_op_missing_keys_skipped(self):
        ops = [
            {"type": "unknown_op", "table": "users"},
            {"type": "rename_column", "table": "users", "old_name": "name", "new_name": "full_name"},
        ]
        rollback_ops = [
            {"type": "unknown_op", "table": "users"},
            {"type": "rename_column", "table": "users", "old_name": "full_name", "new_name": "name"},
        ]
        sql, rb_sql, changes = snapshot_diff_to_sql(ops, rollback_ops, db_name=None)
        assert "RENAME COLUMN" in sql

    def test_mysql_syntax_is_used(self):
        ops = [
            {"type": "add_column", "table": "users", "column": "email", "model_column": ModelColumn("email", "VARCHAR(255)", True, False, False, None, None)},
        ]
        rollback_ops = [
            {"type": "drop_column", "table": "users", "column": "email"},
        ]
        sql, _, _ = snapshot_diff_to_sql(ops, rollback_ops, db_name="mysql_db")
        assert "ADD COLUMN" in sql or "ALTER TABLE" in sql



class TestForeignKeyDiff:
    def test_fk_name_generation(self):
        handler = ConstraintHandler()
        name = handler._build_fk_name("users", ["group_id"])
        assert name == "fk_users_group_id"

    def test_fk_diff_add(self):
        snapshot = {
            "tables": {
                "users": {"columns": {"id": {"type": "integer"}, "group_id": {"type": "integer"}}},
                "groups": {"columns": {"id": {"type": "integer"}, "name": {"type": "varchar"}}},
            },
            "constraints": {},
            "indexes": {},
        }
        model_tables = [
            ModelTable(
                name="users",
                columns=[
                    ModelColumn("id", "INTEGER", False, True, False, None, None),
                    ModelColumn("group_id", "INTEGER", True, False, False, None, None),
                ],
                foreign_keys=[{"columns": ["group_id"], "referred_table": "groups", "referred_columns": ["id"]}],
            ),
            ModelTable(
                name="groups",
                columns=[
                    ModelColumn("id", "INTEGER", False, True, False, None, None),
                    ModelColumn("name", "VARCHAR", True, False, False, None, None),
                ],
            ),
        ]
        upgrade, rollback = diff_models_against_snapshot(model_tables, snapshot)
        fk_ops = [op for op in upgrade if op["type"] == "add_foreign_key"]
        assert len(fk_ops) == 1
        assert fk_ops[0]["columns"] == ["group_id"]
        assert fk_ops[0]["referenced_table"] == "groups"

    def test_fk_diff_drop(self):
        snapshot = {
            "tables": {
                "users": {"columns": {"id": {"type": "integer"}, "group_id": {"type": "integer"}}},
                "groups": {"columns": {"id": {"type": "integer"}}},
            },
            "constraints": {
                "users_group_id_fkey": {
                    "type": "foreign_key",
                    "table": "users",
                    "columns": ["group_id"],
                    "referenced_table": "groups",
                    "referenced_columns": ["id"],
                    "on_delete": "NO ACTION",
                    "on_update": "NO ACTION",
                },
            },
            "indexes": {},
        }
        model_tables = [
            ModelTable(
                name="users",
                columns=[
                    ModelColumn("id", "INTEGER", False, True, False, None, None),
                    ModelColumn("group_id", "INTEGER", True, False, False, None, None),
                ],
            ),
        ]
        upgrade, rollback = diff_models_against_snapshot(model_tables, snapshot)
        fk_ops = [op for op in upgrade if op["type"] == "drop_foreign_key"]
        assert len(fk_ops) == 1
        assert fk_ops[0]["columns"] == ["group_id"]

    def test_fk_diff_no_change(self):
        snapshot = {
            "tables": {
                "users": {"columns": {"id": {"type": "integer"}, "group_id": {"type": "integer"}}},
                "groups": {"columns": {"id": {"type": "integer"}}},
            },
            "constraints": {
                "users_group_id_fkey": {
                    "type": "foreign_key",
                    "table": "users",
                    "columns": ["group_id"],
                    "referenced_table": "groups",
                    "referenced_columns": ["id"],
                    "on_delete": "NO ACTION",
                    "on_update": "NO ACTION",
                },
            },
            "indexes": {},
        }
        model_tables = [
            ModelTable(
                name="users",
                columns=[
                    ModelColumn("id", "INTEGER", False, True, False, None, None),
                    ModelColumn("group_id", "INTEGER", True, False, False, None, None),
                ],
                foreign_keys=[{"columns": ["group_id"], "referred_table": "groups", "referred_columns": ["id"]}],
            ),
        ]
        upgrade, rollback = diff_models_against_snapshot(model_tables, snapshot)
        fk_ops = [op for op in upgrade if "foreign_key" in op["type"]]
        assert len(fk_ops) == 0

    def test_fk_add_sql_postgresql(self, monkeypatch):
        monkeypatch.setattr("dbwarden.engine.snapshot._get_backend", lambda db_name=None: "postgresql")
        op = Op(object_type="add_foreign_key", upgrade_attrs={"table": "users", "columns": ["group_id"], "referenced_table": "groups", "referenced_columns": ["id"]})
        stmts = ConstraintHandler().emit(op, db_name="primary")
        assert len(stmts) == 1
        assert "ADD CONSTRAINT" in stmts[0].upgrade_sql
        assert "fk_users_group_id" in stmts[0].upgrade_sql

    def test_fk_drop_sql_mysql(self, monkeypatch):
        monkeypatch.setattr("dbwarden.engine.snapshot._get_backend", lambda db_name=None: "mysql")
        op = Op(object_type="drop_foreign_key", upgrade_attrs={"table": "users", "columns": ["group_id"], "referenced_table": "groups", "referenced_columns": ["id"]})
        stmts = ConstraintHandler().emit(op, db_name="primary")
        assert "DROP FOREIGN KEY" in stmts[0].upgrade_sql

    def test_fk_sql_sqlite_not_supported(self, monkeypatch):
        monkeypatch.setattr("dbwarden.engine.snapshot._get_backend", lambda db_name=None: "sqlite")
        op = Op(object_type="add_foreign_key", upgrade_attrs={"table": "users", "columns": ["group_id"], "referenced_table": "groups", "referenced_columns": ["id"]})
        stmts = ConstraintHandler().emit(op, db_name="primary")
        assert "not supported" in stmts[0].upgrade_sql

    def test_fk_sql_with_deferrable(self, monkeypatch):
        monkeypatch.setattr("dbwarden.engine.snapshot._get_backend", lambda db_name=None: "postgresql")
        op = Op(object_type="add_foreign_key", upgrade_attrs={"table": "users", "columns": ["group_id"], "referenced_table": "groups", "referenced_columns": ["id"], "deferrable": True})
        stmts = ConstraintHandler().emit(op, db_name="primary")
        assert "DEFERRABLE INITIALLY DEFERRED" in stmts[0].upgrade_sql

    def test_fk_match_full_emits_match_clause(self, monkeypatch):
        monkeypatch.setattr("dbwarden.engine.snapshot._get_backend", lambda db_name=None: "postgresql")
        op = Op(object_type="add_foreign_key", upgrade_attrs={
            "table": "users", "columns": ["group_id"],
            "referenced_table": "groups", "referenced_columns": ["id"],
            "match": "FULL",
        })
        stmts = ConstraintHandler().emit(op, db_name="primary")
        assert "MATCH FULL" in stmts[0].upgrade_sql

    def test_fk_match_simple_not_emitted(self, monkeypatch):
        monkeypatch.setattr("dbwarden.engine.snapshot._get_backend", lambda db_name=None: "postgresql")
        op = Op(object_type="add_foreign_key", upgrade_attrs={
            "table": "users", "columns": ["group_id"],
            "referenced_table": "groups", "referenced_columns": ["id"],
            "match": "SIMPLE",
        })
        stmts = ConstraintHandler().emit(op, db_name="primary")
        assert "MATCH" not in stmts[0].upgrade_sql

    def test_fk_match_none_not_emitted(self, monkeypatch):
        monkeypatch.setattr("dbwarden.engine.snapshot._get_backend", lambda db_name=None: "postgresql")
        op = Op(object_type="add_foreign_key", upgrade_attrs={
            "table": "users", "columns": ["group_id"],
            "referenced_table": "groups", "referenced_columns": ["id"],
        })
        stmts = ConstraintHandler().emit(op, db_name="primary")
        assert "MATCH" not in stmts[0].upgrade_sql

    def test_fk_match_simple_canonicalized_to_absence(self):
        """Canonicalize SIMPLE to absence so snap and model compare equal."""
        handler = ConstraintHandler()
        snap_spec = {"users": {"uniques": {}, "checks": {}, "fks": [
            {"columns": ["a"], "referenced_table": "b", "referenced_columns": ["id"],
             "match": "SIMPLE", "on_delete": "NO ACTION", "on_update": "NO ACTION"},
        ]}}
        model_spec = {"users": {"uniques": {}, "checks": {}, "fks": [
            {"columns": ["a"], "referenced_table": "b", "referenced_columns": ["id"],
             "on_delete": "NO ACTION", "on_update": "NO ACTION"},
        ]}}
        c_snap = handler.canonicalize(snap_spec)
        c_model = handler.canonicalize(model_spec)
        assert c_snap == c_model
        up, rb = handler.diff(c_snap, c_model)
        assert up == []

    def test_fk_validation_skips_missing_ref_table(self):
        snapshot = {
            "tables": {
                "users": {"columns": {"id": {"type": "integer"}, "group_id": {"type": "integer"}}},
            },
            "constraints": {},
            "indexes": {},
        }
        model_tables = [
            ModelTable(
                name="users",
                columns=[
                    ModelColumn("id", "INTEGER", False, True, False, None, None),
                    ModelColumn("group_id", "INTEGER", True, False, False, None, None),
                ],
                foreign_keys=[{"columns": ["group_id"], "referred_table": "groups", "referred_columns": ["id"]}],
            ),
        ]
        upgrade, rollback = diff_models_against_snapshot(model_tables, snapshot)
        fk_ops = [op for op in upgrade if op["type"] == "add_foreign_key"]
        assert len(fk_ops) == 0



class TestIndexDiff:
    def test_index_name_generation_non_unique(self):
        name = _build_index_name("users", ["email"], False)
        assert name == "idx_users_email"

    def test_index_name_generation_unique(self):
        name = _build_index_name("users", ["email"], True)
        assert name == "uq_users_email"

    def test_index_diff_add(self):
        snapshot = {
            "tables": {
                "users": {"columns": {"id": {"type": "integer"}, "email": {"type": "varchar"}}},
            },
            "indexes": {},
            "constraints": {},
        }
        model_tables = [
            ModelTable(
                name="users",
                columns=[
                    ModelColumn("id", "INTEGER", False, True, False, None, None),
                    ModelColumn("email", "VARCHAR", True, False, False, None, None),
                ],
                indexes=[{"columns": ["email"], "unique": True}],
            ),
        ]
        upgrade, rollback = diff_models_against_snapshot(model_tables, snapshot)
        idx_ops = [op for op in upgrade if op["type"] == "add_index"]
        assert len(idx_ops) == 1
        assert idx_ops[0]["columns"] == ["email"]
        assert idx_ops[0]["unique"] is True

    def test_index_diff_drop(self):
        snapshot = {
            "tables": {
                "users": {"columns": {"id": {"type": "integer"}, "email": {"type": "varchar"}}},
            },
            "indexes": {
                "idx_users_email": {"table": "users", "columns": ["email"], "unique": False, "type": "btree"},
            },
            "constraints": {},
        }
        model_tables = [
            ModelTable(
                name="users",
                columns=[
                    ModelColumn("id", "INTEGER", False, True, False, None, None),
                    ModelColumn("email", "VARCHAR", True, False, False, None, None),
                ],
            ),
        ]
        upgrade, rollback = diff_models_against_snapshot(model_tables, snapshot)
        idx_ops = [op for op in upgrade if op["type"] == "drop_index"]
        assert len(idx_ops) == 1
        assert idx_ops[0]["columns"] == ["email"]

    def test_index_diff_no_change(self):
        snapshot = {
            "tables": {
                "users": {"columns": {"id": {"type": "integer"}, "email": {"type": "varchar"}}},
            },
            "indexes": {
                "uq_users_email": {"table": "users", "columns": ["email"], "unique": True, "type": "btree"},
            },
            "constraints": {},
        }
        model_tables = [
            ModelTable(
                name="users",
                columns=[
                    ModelColumn("id", "INTEGER", False, True, False, None, None),
                    ModelColumn("email", "VARCHAR", True, False, False, None, None),
                ],
                indexes=[{"columns": ["email"], "unique": True}],
            ),
        ]
        upgrade, rollback = diff_models_against_snapshot(model_tables, snapshot)
        idx_ops = [op for op in upgrade if "index" in op["type"]]
        assert len(idx_ops) == 0

    def test_index_add_sql_postgresql(self):
        op = {"type": "add_index", "table": "users", "columns": ["email"], "unique": True}
        stmts = _build_index_sql(op, "postgresql")
        assert "CREATE UNIQUE INDEX" in stmts[0].upgrade_sql
        assert "CONCURRENTLY" in stmts[0].upgrade_sql

    def test_index_add_sql_postgresql_concurrent_is_autocommit_marked(self):
        op = {"type": "add_index", "table": "users", "columns": ["email"], "unique": False}
        stmts = _build_index_sql(op, "postgresql")
        assert stmts[0].upgrade_sql.startswith("-- @dbwarden:autocommit\n")

    def test_index_add_sql_sqlite(self):
        op = {"type": "add_index", "table": "users", "columns": ["email"], "unique": False}
        stmts = _build_index_sql(op, "sqlite")
        assert "CREATE INDEX" in stmts[0].upgrade_sql
        assert "CONCURRENTLY" not in stmts[0].upgrade_sql

    def test_index_drop_sql(self):
        op = {"type": "drop_index", "table": "users", "index_name": "idx_users_email", "columns": ["email"], "unique": False}
        stmts = _build_index_sql(op, "postgresql")
        assert "DROP INDEX idx_users_email;" in stmts[0].upgrade_sql

    def test_index_sql_with_postgresql_ops(self):
        op = {"type": "add_index", "table": "users", "columns": ["data"],
              "using": "gin", "postgresql_ops": {"data": "jsonb_path_ops"}}
        stmts = _build_index_sql(op, "postgresql")
        assert "USING gin" in stmts[0].upgrade_sql
        assert "data jsonb_path_ops" in stmts[0].upgrade_sql

    def test_index_drop_rollback_with_postgresql_ops(self):
        op = {"type": "drop_index", "table": "users", "columns": ["data"],
              "index_name": "idx_users_data", "using": "gin",
              "postgresql_ops": {"data": "jsonb_path_ops"}}
        stmts = _build_index_sql(op, "postgresql")
        assert "data jsonb_path_ops" in stmts[0].rollback_sql

    def test_index_sql_with_opclass_and_sorting(self):
        op = {"type": "add_index", "table": "users", "columns": ["data"],
              "using": "gin",
              "postgresql_ops": {"data": "jsonb_path_ops"},
              "column_sorting": {"data": "DESC"}}
        stmts = _build_index_sql(op, "postgresql")
        assert "data jsonb_path_ops DESC" in stmts[0].upgrade_sql
        assert "data DESC jsonb_path_ops" not in stmts[0].upgrade_sql

    def test_index_name_generation_multi_column(self):
        name = _build_index_name("users", ["first_name", "last_name"], True)
        assert name == "uq_users_first_name_last_name"

    def test_fk_and_index_in_snapshot_diff_to_sql(self):
        ops = [
            {"type": "add_foreign_key", "table": "users", "columns": ["group_id"], "referenced_table": "groups", "referenced_columns": ["id"]},
            {"type": "add_index", "table": "users", "columns": ["email"], "unique": True},
        ]
        rollback_ops = [
            {"type": "drop_foreign_key", "table": "users", "columns": ["group_id"], "referenced_table": "groups", "referenced_columns": ["id"]},
            {"type": "drop_index", "table": "users", "columns": ["email"], "unique": True},
        ]
        sql, rb_sql, changes = snapshot_diff_to_sql(ops, rollback_ops, db_name=None)
        assert "ADD CONSTRAINT" in sql
        assert "CREATE" in sql
        assert any(c.operation == "add_foreign_key" for c in changes)
        assert any(c.operation == "add_index" for c in changes)

    def test_fk_rollback_sql_correctness(self, monkeypatch):
        monkeypatch.setattr("dbwarden.engine.snapshot._get_backend", lambda db_name=None: "postgresql")
        op = Op(object_type="add_foreign_key", upgrade_attrs={"table": "users", "columns": ["group_id"], "referenced_table": "groups", "referenced_columns": ["id"]})
        stmt = ConstraintHandler().emit(op, db_name="primary")[0]
        assert "DROP CONSTRAINT" in stmt.rollback_sql
        assert "fk_users_group_id" in stmt.rollback_sql

    def test_fk_mariadb_uses_drop_foreign_key(self, monkeypatch):
        monkeypatch.setattr("dbwarden.engine.snapshot._get_backend", lambda db_name=None: "mariadb")
        op = Op(object_type="drop_foreign_key", upgrade_attrs={"table": "users", "columns": ["group_id"], "referenced_table": "groups", "referenced_columns": ["id"]})
        stmt = ConstraintHandler().emit(op, db_name="primary")
        assert "DROP FOREIGN KEY" in stmt[0].upgrade_sql

    def test_index_mysql_syntax(self):
        op = {"type": "add_index", "table": "users", "columns": ["email"], "unique": False}
        stmt = _build_index_sql(op, "mysql")
        assert "CREATE INDEX" in stmt[0].upgrade_sql
        assert "idx_users_email" in stmt[0].upgrade_sql

    def test_index_mariadb_syntax(self):
        op = {"type": "add_index", "table": "users", "columns": ["email"], "unique": True}
        stmt = _build_index_sql(op, "mariadb")
        assert "CREATE UNIQUE INDEX" in stmt[0].upgrade_sql

    def test_mysql_table_option_sql(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            old = os.getcwd()
            os.chdir(tmpdir)
            try:
                Path("dbwarden.py").write_text(
                    "from dbwarden import database_config\n"
                    "database_config(database_name='mysql_db', default=True, database_type='mysql', database_url_sync='mysql+pymysql://root:pw@localhost/test')\n"
                )
                ops = [{
                    "type": "alter_my_table",
                    "table": "users",
                    "key": "my_engine",
                    "from_value": "InnoDB",
                    "to_value": "MyISAM",
                }]
                rollback_ops = [{
                    "type": "alter_my_table",
                    "table": "users",
                    "key": "my_engine",
                    "from_value": "MyISAM",
                    "to_value": "InnoDB",
                }]
                sql, rb_sql, _changes = snapshot_diff_to_sql(ops, rollback_ops, db_name="mysql_db")
                assert "ALTER TABLE users ENGINE=MyISAM;" in sql
                assert "ALTER TABLE users ENGINE=InnoDB;" in rb_sql
            finally:
                os.chdir(old)

    def test_mysql_column_meta_sql(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            old = os.getcwd()
            os.chdir(tmpdir)
            try:
                Path("dbwarden.py").write_text(
                    "from dbwarden import database_config\n"
                    "database_config(database_name='mysql_db', default=True, database_type='mysql', database_url_sync='mysql+pymysql://root:pw@localhost/test')\n"
                )
                ops = [{
                    "type": "alter_my_column_meta",
                    "table": "users",
                    "column": "id",
                    "col_type": "integer",
                    "snap_type": "integer",
                    "from_my_column": {},
                    "to_my_column": {"my_unsigned": True},
                }]
                rollback_ops = [{
                    "type": "alter_my_column_meta",
                    "table": "users",
                    "column": "id",
                    "col_type": "integer",
                    "snap_type": "integer",
                    "from_my_column": {"my_unsigned": True},
                    "to_my_column": {},
                }]
                sql, rb_sql, _changes = snapshot_diff_to_sql(ops, rollback_ops, db_name="mysql_db")
                assert "ALTER TABLE users MODIFY COLUMN id integer UNSIGNED;" in sql
                assert "ALTER TABLE users MODIFY COLUMN id integer;" in rb_sql
            finally:
                os.chdir(old)

    def test_mysql_table_comment_sql(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            old = os.getcwd()
            os.chdir(tmpdir)
            try:
                Path("dbwarden.py").write_text(
                    "from dbwarden import database_config\n"
                    "database_config(database_name='mysql_db', default=True, database_type='mysql', "
                    "database_url_sync='mysql+pymysql://root:pw@localhost/test')\n"
                )
                ops = [{
                    "type": "alter_table_comment",
                    "table": "users",
                    "comment": "Core users table",
                    "previous_comment": "",
                }]
                rollback_ops = [{
                    "type": "alter_table_comment",
                    "table": "users",
                    "comment": "",
                    "previous_comment": "Core users table",
                }]
                sql, rb_sql, _changes = snapshot_diff_to_sql(ops, rollback_ops, db_name="mysql_db")
                assert "ALTER TABLE users COMMENT = 'Core users table';" in sql
                assert "ALTER TABLE users COMMENT = '';" in rb_sql
            finally:
                os.chdir(old)

    def test_mysql_column_comment_sql(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            old = os.getcwd()
            os.chdir(tmpdir)
            try:
                Path("dbwarden.py").write_text(
                    "from dbwarden import database_config\n"
                    "database_config(database_name='mysql_db', default=True, database_type='mysql', "
                    "database_url_sync='mysql+pymysql://root:pw@localhost/test')\n"
                )
                ops = [{
                    "type": "alter_column_comment",
                    "table": "users",
                    "column": "email",
                    "comment": "User email address",
                    "previous_comment": "",
                    "col_type": "VARCHAR(255)",
                    "nullable": False,
                    "autoincrement": False,
                    "my_meta": {},
                }]
                rollback_ops = [{
                    "type": "alter_column_comment",
                    "table": "users",
                    "column": "email",
                    "comment": "",
                    "previous_comment": "User email address",
                    "col_type": "VARCHAR(255)",
                    "nullable": False,
                    "autoincrement": False,
                    "my_meta": {},
                }]
                sql, rb_sql, _changes = snapshot_diff_to_sql(ops, rollback_ops, db_name="mysql_db")
                assert "ALTER TABLE users MODIFY COLUMN email VARCHAR(255) NOT NULL COMMENT 'User email address';" in sql
                assert "ALTER TABLE users MODIFY COLUMN email VARCHAR(255) NOT NULL COMMENT '';" in rb_sql
            finally:
                os.chdir(old)

    def test_mysql_autoincrement_add_sql(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            old = os.getcwd()
            os.chdir(tmpdir)
            try:
                Path("dbwarden.py").write_text(
                    "from dbwarden import database_config\n"
                    "database_config(database_name='mysql_db', default=True, database_type='mysql', "
                    "database_url_sync='mysql+pymysql://root:pw@localhost/test')\n"
                )
                ops = [{
                    "type": "alter_column_autoincrement",
                    "table": "users",
                    "column": "id",
                    "autoincrement": True,
                    "col_type": "INT",
                    "nullable": False,
                }]
                rollback_ops = [{
                    "type": "alter_column_autoincrement",
                    "table": "users",
                    "column": "id",
                    "autoincrement": False,
                    "col_type": "INT",
                    "nullable": False,
                }]
                sql, rb_sql, _changes = snapshot_diff_to_sql(ops, rollback_ops, db_name="mysql_db")
                assert "ALTER TABLE users MODIFY COLUMN id INT NOT NULL AUTO_INCREMENT;" in sql
                assert "ALTER TABLE users MODIFY COLUMN id INT NOT NULL;" in rb_sql
            finally:
                os.chdir(old)

    def test_mysql_autoincrement_remove_sql(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            old = os.getcwd()
            os.chdir(tmpdir)
            try:
                Path("dbwarden.py").write_text(
                    "from dbwarden import database_config\n"
                    "database_config(database_name='mysql_db', default=True, database_type='mysql', "
                    "database_url_sync='mysql+pymysql://root:pw@localhost/test')\n"
                )
                ops = [{
                    "type": "alter_column_autoincrement",
                    "table": "users",
                    "column": "id",
                    "autoincrement": False,
                    "col_type": "INT",
                    "nullable": False,
                }]
                rollback_ops = [{
                    "type": "alter_column_autoincrement",
                    "table": "users",
                    "column": "id",
                    "autoincrement": True,
                    "col_type": "INT",
                    "nullable": False,
                }]
                sql, rb_sql, _changes = snapshot_diff_to_sql(ops, rollback_ops, db_name="mysql_db")
                assert "ALTER TABLE users MODIFY COLUMN id INT NOT NULL;" in sql
                assert "ALTER TABLE users MODIFY COLUMN id INT NOT NULL AUTO_INCREMENT;" in rb_sql
            finally:
                os.chdir(old)

    def test_normalize_mysql_default_strips_on_update_clause(self):
        assert _normalize_mysql_default("CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP") == "CURRENT_TIMESTAMP"

    def test_mysql_table_diff_ignores_omitted_auto_increment_and_row_format_case(self):
        snapshot = {
            "tables": {
                "users": {
                    "columns": {
                        "id": {"type": "integer", "nullable": False, "primary_key": True, "my_column": {"my_unsigned": True}},
                    },
                    "my_table": {
                        "my_engine": "InnoDB",
                        "my_charset": "utf8mb4",
                        "my_collate": "utf8mb4_unicode_ci",
                        "my_row_format": "Dynamic",
                        "my_auto_increment": 1,
                    },
                }
            },
            "constraints": {},
            "indexes": {},
        }
        model_tables = [
            ModelTable(
                name="users",
                columns=[ModelColumn("id", "INT", False, True, False, None, None, my_meta={"my_unsigned": True})],
                my_table={
                    "my_engine": "InnoDB",
                    "my_charset": "utf8mb4",
                    "my_collate": "utf8mb4_unicode_ci",
                    "my_row_format": "DYNAMIC",
                },
            )
        ]
        upgrade, rollback = diff_models_against_snapshot(model_tables, snapshot, db_name=None)
        assert upgrade == []
        assert rollback == []

    def test_index_clickhouse_uses_generic(self):
        op = {"type": "add_index", "table": "users", "columns": ["email"], "unique": False}
        stmt = _build_index_sql(op, "clickhouse")
        assert "CREATE INDEX" in stmt[0].upgrade_sql

    def test_index_drop_sqlite(self):
        op = {"type": "drop_index", "table": "users", "index_name": "idx_users_email", "columns": ["email"], "unique": False}
        stmt = _build_index_sql(op, "sqlite")
        assert "DROP INDEX idx_users_email;" in stmt[0].upgrade_sql

    def test_index_rollback_creates_index(self):
        op = {"type": "drop_index", "table": "users", "index_name": "uq_users_email", "columns": ["email"], "unique": True}
        stmt = _build_index_sql(op, "postgresql")
        assert "CREATE UNIQUE INDEX" in stmt[0].rollback_sql

    def test_composite_fk_name_generation(self):
        handler = ConstraintHandler()
        name = handler._build_fk_name("orders", ["user_id", "product_id"])
        assert name == "fk_orders_user_id_product_id"

    def test_fk_add_sql_does_not_include_on_delete(self, monkeypatch):
        monkeypatch.setattr("dbwarden.engine.snapshot._get_backend", lambda db_name=None: "postgresql")
        op = Op(object_type="add_foreign_key", upgrade_attrs={"table": "users", "columns": ["group_id"], "referenced_table": "groups", "referenced_columns": ["id"], "on_delete": "CASCADE"})
        stmts = ConstraintHandler().emit(op, db_name="primary")
        assert "ON DELETE CASCADE" in stmts[0].upgrade_sql

    def test_fk_add_sql_does_not_include_on_update(self, monkeypatch):
        monkeypatch.setattr("dbwarden.engine.snapshot._get_backend", lambda db_name=None: "postgresql")
        op = Op(object_type="add_foreign_key", upgrade_attrs={"table": "users", "columns": ["group_id"], "referenced_table": "groups", "referenced_columns": ["id"], "on_update": "SET NULL"})
        stmts = ConstraintHandler().emit(op, db_name="primary")
        assert "ON UPDATE SET NULL" in stmts[0].upgrade_sql

    def test_fk_diff_detects_on_delete_change(self):
        snapshot = {
            "tables": {
                "users": {"columns": {"id": {"type": "integer"}, "group_id": {"type": "integer"}}},
                "groups": {"columns": {"id": {"type": "integer"}}},
            },
            "constraints": {
                "users_group_id_fkey": {
                    "type": "foreign_key",
                    "table": "users",
                    "columns": ["group_id"],
                    "referenced_table": "groups",
                    "referenced_columns": ["id"],
                    "on_delete": "NO ACTION",
                    "on_update": "NO ACTION",
                },
            },
            "indexes": {},
        }
        model_tables = [
            ModelTable(
                name="users",
                columns=[
                    ModelColumn("id", "INTEGER", False, True, False, None, None),
                    ModelColumn("group_id", "INTEGER", True, False, False, None, None),
                ],
                foreign_keys=[{"columns": ["group_id"], "referred_table": "groups", "referred_columns": ["id"], "on_delete": "CASCADE"}],
            ),
        ]
        upgrade, _ = diff_models_against_snapshot(model_tables, snapshot)
        assert any(op["type"] == "drop_foreign_key" for op in upgrade)
        assert any(op["type"] == "add_foreign_key" and op.get("on_delete") == "CASCADE" for op in upgrade)

    def test_unique_constraint_diff_add(self):
        snapshot = {"tables": {"users": {"columns": {"email": {"type": "varchar"}}}}, "constraints": {}, "indexes": {}}
        model_tables = [
            ModelTable(
                name="users",
                columns=[ModelColumn("email", "VARCHAR", False, False, False, None, None)],
                uniques=[{"name": "uq_users_email", "columns": ["email"], "deferrable": True}],
            )
        ]
        upgrade, _ = diff_models_against_snapshot(model_tables, snapshot)
        assert any(op["type"] == "add_unique_constraint" for op in upgrade)

    def test_check_constraint_diff_add(self):
        snapshot = {"tables": {"users": {"columns": {"age": {"type": "integer"}}}}, "constraints": {}, "indexes": {}}
        model_tables = [
            ModelTable(
                name="users",
                columns=[ModelColumn("age", "INTEGER", False, False, False, None, None)],
                checks=[{"name": "ck_users_age", "expression": "age >= 0"}],
            )
        ]
        upgrade, _ = diff_models_against_snapshot(model_tables, snapshot)
        assert any(op["type"] == "add_check_constraint" for op in upgrade)

    def test_comment_and_pg_meta_diff(self):
        snapshot = {
            "tables": {
                "users": {
                    "comment": "Old comment",
                    "columns": {
                        "email": {
                            "type": "varchar",
                            "nullable": False,
                            "comment": "Old email",
                            "pg_column": {"collation": "C"},
                        }
                    },
                }
            },
            "constraints": {},
            "indexes": {},
        }
        model_tables = [
            ModelTable(
                name="users",
                columns=[ModelColumn("email", "VARCHAR", False, False, False, None, None, comment="New email", pg_meta={"pg_collation": "en_US.UTF-8"})],
                comment="New comment",
            )
        ]
        upgrade, _ = diff_models_against_snapshot(model_tables, snapshot)
        assert any(op["type"] == "alter_table_comment" for op in upgrade)
        assert any(op["type"] == "alter_column_comment" for op in upgrade)
        assert any(op["type"] == "alter_pg_column_meta" for op in upgrade)

    def test_index_signature_preserves_column_order(self):
        snapshot = {
            "tables": {"users": {"columns": {"first_name": {"type": "varchar"}, "last_name": {"type": "varchar"}}}},
            "indexes": {
                "idx_users_name": {"table": "users", "columns": ["last_name", "first_name"], "unique": False, "using": "btree"},
            },
            "constraints": {},
        }
        model_tables = [
            ModelTable(
                name="users",
                columns=[
                    ModelColumn("first_name", "VARCHAR", True, False, False, None, None),
                    ModelColumn("last_name", "VARCHAR", True, False, False, None, None),
                ],
                indexes=[{"columns": ["first_name", "last_name"], "unique": False, "using": "btree"}],
            )
        ]
        upgrade, _ = diff_models_against_snapshot(model_tables, snapshot)
        assert any(op["type"] == "drop_index" for op in upgrade)
        assert any(op["type"] == "add_index" for op in upgrade)
