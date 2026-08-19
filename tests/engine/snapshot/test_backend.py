import json
import logging
import os
import tempfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from dbwarden.databases.clickhouse import data_op
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
from dbwarden.engine.snapshot.sql_gen import RollbackContractError
from dbwarden.engine.snapshot.sql_gen import _enforce_rollback_contract
from dbwarden.engine.model_discovery import IndexInfo, ModelColumn, ModelTable
from dbwarden.engine.migration_name import Change
from dbwarden.engine.offline import diff_model_states, model_state_to_dict
from dbwarden.engine.backends.postgresql.handlers import ConstraintHandler, StatisticsHandler
from dbwarden.engine.core.protocol import Op
from dbwarden.logging import get_logger, reset_logger


def _mc(name: str, typ: str, pk: bool = False, nullable: bool = True) -> ModelColumn:
    return ModelColumn(name, typ, nullable, pk, False, None, None)


def test_irreversible_rollback_warns_in_normal_mode(caplog):
    reset_logger()
    get_logger(verbose=False)
    try:
        with caplog.at_level(logging.WARNING):
            snapshot_diff_to_sql(
                [
                    {
                        "type": "refresh_matview",
                        "table": "my_view",
                        "schema": "public",
                        "__irreversible": True,
                    }
                ],
                [],
                db_name=None,
            )

        assert "Irreversible rollback for refresh_matview" in caplog.text
    finally:
        reset_logger()

    caplog.clear()
    get_logger(verbose=True)
    try:
        with caplog.at_level(logging.WARNING):
            snapshot_diff_to_sql(
                [{"type": "refresh_matview", "table": "my_view", "schema": "public"}],
                [],
                db_name=None,
            )
        assert "Irreversible rollback for refresh_matview" in caplog.text
    finally:
        reset_logger()


def test_placeholder_rollback_warns_only_in_verbose_mode(capsys):
    pytest.skip("Placeholder rollback tests moved to plugin (drop_role in dbwarden-pg-extras)")


def test_placeholder_rollback_fails_when_contract_is_enforced():
    pytest.skip("Placeholder rollback tests moved to plugin (drop_role in dbwarden-pg-extras)")


def test_known_irreversible_rollback_passes_enforced_contract(capsys):
    pytest.skip("Irreversible rollback tests moved to plugin (alter_enum_add_value in dbwarden-pg-extras)")


def test_irreversible_acknowledgement_allows_placeholder_rollback():
    pytest.skip("Irreversible acknowledgement tests moved to plugin (drop_role in dbwarden-pg-extras)")


def test_unknown_irreversible_rollback_fails_closed_set_contract():
    stmt = MigrationStatement(
        order=StatementOrder.ALTER_TABLE_OPTIONS,
        upgrade_sql="ALTER TABLE t MODIFY x;",
        rollback_sql="-- irreversible custom rollback",
        rollback_kind="irreversible",
    )

    with pytest.raises(RollbackContractError, match="not in the known irreversible set"):
        _enforce_rollback_contract(
            stmt,
            "custom_op",
            "t",
            "irreversible",
            enforce_rollback_contract=True,
        )


def test_real_rollback_cannot_emit_sql_comment():
    stmt = MigrationStatement(
        order=StatementOrder.ALTER_TABLE_OPTIONS,
        upgrade_sql="ALTER TABLE t MODIFY x;",
        rollback_sql="-- Revert manually",
        rollback_kind="real",
    )

    with pytest.raises(RollbackContractError, match="classified real but emits a SQL comment"):
        _enforce_rollback_contract(
            stmt,
            "alter_table",
            "t",
            "real",
            enforce_rollback_contract=True,
        )


@pytest.mark.parametrize(
    "op",
    [
        {"type": "refresh_matview", "table": "events_mv"},
        {
            "type": "apply_data_op",
            "name": "backfill",
            "data_op": data_op(
                name="backfill",
                forward="ALTER TABLE events DELETE WHERE id = 1",
                rollback=None,
            ),
        },
    ],
)
def test_known_irreversible_comment_rollbacks_pass_closed_set_contract(op):
    _up_sql, rb_sql, _changes = snapshot_diff_to_sql(
        [op],
        [],
        db_name=None,
        enforce_rollback_contract=True,
    )

    assert rb_sql.strip().startswith("--")


@pytest.mark.parametrize(
    "op",
    [
        {"type": "drop_ch_named_collection", "name": "cfg"},
        {"type": "drop_ch_settings_profile", "name": "readonly"},
        {"type": "drop_ch_row_policy", "name": "tenant_policy", "table": "events"},
        {"type": "drop_ch_user", "name": "svc"},
        {"type": "drop_ch_role", "name": "reader"},
        {"type": "drop_ch_quota", "name": "api_quota"},
    ],
)
def test_promoted_ch_conditional_paths_fail_strict_contract_without_prior_state(op):
    pytest.skip("CH RBAC handlers moved to dbwarden-ch-extras plugin")


def test_pg_column_statistics_rollback_restores_prior_target():
    handler = StatisticsHandler()
    stmts = handler.emit(Op(
        object_type="alter_column_statistics",
        upgrade_attrs={"table": "events", "column": "payload", "statistics": 1000},
        rollback_attrs={"table": "events", "column": "payload", "statistics": 100},
    ))

    assert stmts[0].upgrade_sql == "ALTER TABLE events ALTER COLUMN payload SET STATISTICS 1000;"
    assert stmts[0].rollback_sql == "ALTER TABLE events ALTER COLUMN payload SET STATISTICS 100;"


def test_pg_column_statistics_rollback_restores_default_target():
    handler = StatisticsHandler()
    stmts = handler.emit(Op(
        object_type="alter_column_statistics",
        upgrade_attrs={"table": "events", "column": "payload", "statistics": 1000},
        rollback_attrs={"table": "events", "column": "payload", "statistics": None},
    ))

    assert stmts[0].rollback_sql == "ALTER TABLE events ALTER COLUMN payload SET STATISTICS -1;"


def test_pg_role_alter_rollback_restores_prior_attrs():
    pytest.skip("RoleHandler moved to dbwarden-pg-extras plugin")


def test_pg_role_drop_rollback_recreates_prior_role():
    pytest.skip("RoleHandler moved to dbwarden-pg-extras plugin")


def test_pg_role_and_statistics_pass_strict_contract_with_prior_state():
    _up_sql, rb_sql, _changes = snapshot_diff_to_sql(
        [
            {
                "type": "alter_column_statistics",
                "table": "events",
                "column": "payload",
                "statistics": 1000,
                "__rollback_attrs": {"table": "events", "column": "payload", "statistics": 100},
            },
        ],
        [],
        db_name=None,
        enforce_rollback_contract=True,
    )

    assert "ALTER TABLE events ALTER COLUMN payload SET STATISTICS 100;" in rb_sql


class TestStatementOrder:
    def test_ordering(self):
        assert StatementOrder.CREATE_EXTENSION < StatementOrder.CREATE_SCHEMA
        assert StatementOrder.CREATE_SCHEMA < StatementOrder.CREATE_DOMAIN
        assert StatementOrder.CREATE_DOMAIN < StatementOrder.CREATE_SEQUENCE
        assert StatementOrder.CREATE_SEQUENCE < StatementOrder.RENAME_TABLE
        assert StatementOrder.RENAME_TABLE == 1
        assert StatementOrder.RENAME_TABLE < StatementOrder.RENAME_COLUMN
        assert StatementOrder.RENAME_COLUMN < StatementOrder.ALTER_COLUMN_TYPE
        assert StatementOrder.ALTER_COLUMN_TYPE < StatementOrder.ALTER_COLUMN_NULLABLE
        assert StatementOrder.ALTER_COLUMN_NULLABLE < StatementOrder.ALTER_COLUMN_DEFAULT
        assert StatementOrder.ALTER_COLUMN_DEFAULT < StatementOrder.CREATE_TYPE
        assert StatementOrder.CREATE_TYPE < StatementOrder.CREATE_TABLE
        assert StatementOrder.CREATE_TABLE < StatementOrder.CREATE_VIEW
        assert StatementOrder.CREATE_VIEW < StatementOrder.ADD_COLUMN
        assert StatementOrder.ADD_COLUMN < StatementOrder.ALTER_FOREIGN_KEY
        assert StatementOrder.ALTER_FOREIGN_KEY < StatementOrder.DROP_VIEW
        assert StatementOrder.DROP_VIEW < StatementOrder.ALTER_INDEX
        assert StatementOrder.ALTER_INDEX < StatementOrder.DROP_COLUMN
        assert StatementOrder.DROP_COLUMN < StatementOrder.DROP_TABLE


class TestAssembleMigration:
    def test_sorts_by_order(self):
        stmts = [
            MigrationStatement(StatementOrder.ADD_COLUMN, "add bar", "drop bar"),
            MigrationStatement(StatementOrder.CREATE_TABLE, "create foo", "drop foo"),
            MigrationStatement(StatementOrder.RENAME_COLUMN, "rename x y", "rename y x"),
        ]
        upgrade, rollback = _assemble_migration(stmts)
        parts = upgrade.split("\n\n")
        assert parts[0] == "rename x y"
        assert parts[1] == "create foo"
        assert parts[2] == "add bar"

    def test_rollback_is_reversed(self):
        stmts = [
            MigrationStatement(StatementOrder.ADD_COLUMN, "add bar", "drop bar"),
            MigrationStatement(StatementOrder.CREATE_TABLE, "create foo", "drop foo"),
        ]
        _, rollback = _assemble_migration(stmts)
        parts = rollback.split("\n\n")
        assert parts[0] == "drop bar"
        assert parts[1] == "drop foo"

    def test_empty_list_returns_empty_strings(self):
        upgrade, rollback = _assemble_migration([])
        assert upgrade == ""
        assert rollback == ""

    def test_single_statement(self):
        stmts = [
            MigrationStatement(StatementOrder.CREATE_TABLE, "create foo", "drop foo"),
        ]
        upgrade, rollback = _assemble_migration(stmts)
        assert upgrade == "create foo"
        assert rollback == "drop foo"

    def test_duplicate_order_values(self):
        stmts = [
            MigrationStatement(StatementOrder.ADD_COLUMN, "add bar 1", "drop bar 1"),
            MigrationStatement(StatementOrder.CREATE_TABLE, "create foo", "drop foo"),
            MigrationStatement(StatementOrder.ADD_COLUMN, "add bar 2", "drop bar 2"),
        ]
        upgrade, rollback = _assemble_migration(stmts)
        parts = upgrade.split("\n\n")
        assert parts[0] == "create foo"
        assert parts[1] == "add bar 1"
        assert parts[2] == "add bar 2"

    def test_rollback_only_statement(self):
        stmts = [
            MigrationStatement(StatementOrder.ADD_COLUMN, upgrade_sql="", rollback_sql="drop col"),
        ]
        upgrade, rollback = _assemble_migration(stmts)
        assert upgrade == ""
        assert rollback == "drop col"


class TestHandlerConvergence:

    def test_all_op_types_emit(self, monkeypatch):
        """Convergence test: every handler-registered op type emits SQL without error."""
        with tempfile.TemporaryDirectory() as tmpdir:
            monkeypatch.chdir(tmpdir)
            Path("dbwarden/schemas").mkdir(parents=True)
            Path("dbwarden.py").write_text(
                "from dbwarden import database_config\n"
                "database_config(database_name='test', default=True, database_type='postgresql', "
                "database_url_sync='postgresql:///test', model_paths=['models'])\n"
            )
            monkeypatch.setattr(
                "dbwarden.engine.snapshot._get_backend",
                lambda db_name=None: "postgresql",
            )
            ops = [
                {"type": "rename_table", "old_table": "users", "new_table": "accounts"},
                {"type": "rename_column", "table": "accounts", "old_name": "name", "new_name": "full_name"},
                {"type": "alter_column_type", "table": "accounts", "column": "bio", "model_type": "TEXT"},
                {"type": "alter_column_nullable", "table": "accounts", "column": "email", "nullable": False, "col_type": "VARCHAR"},
                {"type": "alter_column_default", "table": "accounts", "column": "role", "default": "'admin'"},
                {"type": "alter_column_autoincrement", "table": "accounts", "column": "id", "autoincrement": False, "col_type": "INTEGER", "nullable": False},
                {"type": "alter_column_comment", "table": "accounts", "column": "email", "comment": "user email", "col_type": "VARCHAR", "nullable": True, "autoincrement": False, "my_meta": {}},
                {"type": "add_column", "table": "accounts", "column": "phone", "model_column": ModelColumn("phone", "VARCHAR", True, False, False, None, None)},
                {"type": "drop_column", "table": "accounts", "column": "legacy", "definition": {"type": "varchar"}},
                {"type": "alter_pg_column_meta", "table": "accounts", "column": "id", "col_type": "INTEGER", "snap_type": "integer", "from_pg_column": {}, "to_pg_column": {"default": "1"}},
                {"type": "alter_my_column_meta", "table": "accounts", "column": "id", "col_type": "INTEGER", "snap_type": "integer", "from_my_column": {}, "to_my_column": {"my_col": "val"}, "nullable": True, "default": None, "comment": None, "autoincrement": False, "snap_nullable": True, "snap_default": None, "snap_comment": None},
                {"type": "alter_ch_column", "table": "accounts", "column": "ts", "col_type": "DateTime", "snap_type": "DateTime", "from_ch_column": {}, "to_ch_column": {"codec": "LZ4"}},
                {"type": "add_unique_constraint", "table": "accounts", "columns": ["email"], "name": "uq_accounts_email"},
                {"type": "drop_unique_constraint", "table": "accounts", "columns": ["email"], "name": "uq_accounts_email"},
                {"type": "rename_unique_constraint", "table": "accounts", "old_name": "uq_old", "new_name": "uq_new"},
                {"type": "add_check_constraint", "table": "accounts", "name": "ck_age", "expression": "age > 0"},
                {"type": "drop_check_constraint", "table": "accounts", "name": "ck_age"},
                {"type": "add_foreign_key", "table": "accounts", "columns": ["group_id"], "referenced_table": "groups", "referenced_columns": ["id"]},
                {"type": "drop_foreign_key", "table": "accounts", "columns": ["group_id"], "referenced_table": "groups", "referenced_columns": ["id"], "name": "fk_accounts_group"},
                {"type": "add_index", "table": "accounts", "columns": ["email"], "unique": True},
                {"type": "drop_index", "table": "accounts", "columns": ["email"], "unique": True, "index_name": "ix_accounts_email"},
                {"type": "alter_table_comment", "table": "accounts", "comment": "user accounts table"},
                {"type": "drop_table", "table": "old_thing"},
                {"type": "alter_pg_table", "table": "accounts", "key": "fillfactor", "from_value": None, "to_value": "70"},
                {"type": "alter_ch_options", "table": "events", "ch_engine": "MergeTree()", "ch_order_by": "(id)", "ch_partition_by": "()"},
                {"type": "alter_my_table", "table": "accounts", "key": "my_engine", "from_value": None, "to_value": "InnoDB"},
                {"type": "create_schema", "schema": "app"},
                {"type": "drop_schema", "schema": "old_schema"},
                {"type": "alter_pg_storage_param", "table": "accounts", "param": "fillfactor", "from_value": None, "to_value": "80"},
                {"type": "alter_pg_rls", "table": "accounts", "enabled": True},
                {"type": "alter_view", "table": "active_users", "definition": "SELECT * FROM users WHERE active"},
                {"type": "refresh_matview", "table": "user_stats"},
            ]
            rollback_ops = [dict(op) for op in ops]
            sql, rb_sql, changes = snapshot_diff_to_sql(ops, rollback_ops, db_name="test")
            assert sql
            assert rb_sql
            assert len(changes) >= len(ops)

    def test_handler_diff_to_sql_pipeline(self, monkeypatch):
        """End-to-end: diff_models_against_snapshot -> snapshot_diff_to_sql with handler ops."""
        monkeypatch.setattr("dbwarden.engine.snapshot._get_backend", lambda db_name=None: "postgresql")
        monkeypatch.setattr("dbwarden.engine.model_discovery.type_mapping._get_backend_name", lambda db_name=None: "postgresql")

        snapshot = {
            "tables": {
                "users": {
                    "columns": {
                        "id": {"type": "integer", "nullable": False, "primary_key": True},
                        "name": {"type": "varchar", "nullable": True},
                        "group_id": {"type": "integer", "nullable": True},
                    },
                    "primary_key": ["id"],
                    "comment": None,
                },
                "groups": {
                    "columns": {
                        "id": {"type": "integer", "nullable": False, "primary_key": True},
                    },
                    "primary_key": ["id"],
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
                    ModelColumn("id", "INTEGER", False, True, False, None, None),
                    ModelColumn("full_name", "VARCHAR(255)", True, False, False, None, None),
                    ModelColumn("group_id", "INTEGER", True, False, False, None, None),
                ],
                foreign_keys=[{"columns": ["group_id"], "referred_table": "groups", "referred_columns": ["id"]}],
                comment="User accounts",
            ),
            ModelTable(
                name="groups",
                columns=[ModelColumn("id", "INTEGER", False, True, False, None, None)],
            ),
        ]
        upgrade, rollback = diff_models_against_snapshot(model_tables, snapshot)
        assert any(op["type"] == "rename_column" for op in upgrade)
        assert any(op["type"] == "add_foreign_key" for op in upgrade)
        assert any(op["type"] == "alter_table_comment" for op in upgrade)

        sql, rb_sql, changes = snapshot_diff_to_sql(upgrade, rollback, db_name=None)
        assert "RENAME COLUMN" in sql
        assert "ADD CONSTRAINT" in sql
        assert "COMMENT ON TABLE" in sql
        assert len(changes) == len(upgrade)

    def test_online_offline_pipeline_equivalence(self, monkeypatch):
        """Online and offline diff paths must render identical SQL per backend."""
        monkeypatch.setattr("dbwarden.engine.snapshot._get_backend", lambda db_name=None: db_name or "postgresql")
        monkeypatch.setattr("dbwarden.engine.model_discovery.type_mapping._get_backend_name", lambda db_name=None: db_name or "postgresql")

        prev_tables = [
            ModelTable(
                name="accounts",
                columns=[
                    ModelColumn("id", "INTEGER", False, True, False, None, None, autoincrement=False),
                    ModelColumn("email", "VARCHAR(255)", True, False, False, None, None),
                    ModelColumn("role", "VARCHAR(50)", True, False, False, None, None),
                    ModelColumn("status", "INTEGER", True, False, False, None, None),
                    ModelColumn("bio", "VARCHAR", True, False, False, None, None),
                    ModelColumn("age", "INTEGER", True, False, False, None, None),
                    ModelColumn("group_id", "INTEGER", True, False, False, None, None),
                    ModelColumn("handle", "VARCHAR(50)", True, False, False, None, None),
                ],
                pg_table={"pg_fillfactor": 70},
            ),
            ModelTable(
                name="groups",
                columns=[
                    ModelColumn("id", "INTEGER", False, True, False, None, None),
                ],
            ),
            ModelTable(
                name="inventory",
                columns=[
                    ModelColumn("id", "INTEGER", False, True, False, None, None),
                    ModelColumn("sku", "VARCHAR(64)", False, False, False, None, None),
                    ModelColumn("qty", "INTEGER", False, False, False, None, None),
                ],
                my_table={
                    "my_engine": "innodb",
                    "my_charset": "utf8mb4",
                    "my_collate": "utf8mb4_general_ci",
                    "my_row_format": "dynamic",
                },
            ),
            ModelTable(
                name="events",
                columns=[
                    ModelColumn("id", "UInt64", False, True, False, None, None),
                    ModelColumn("ts", "DateTime", False, False, False, None, None),
                    ModelColumn("payload", "String", True, False, False, None, None),
                ],
                clickhouse_options={
                    "ch_engine": "MergeTree",
                    "ch_order_by": ["id"],
                    "ch_partition_by": "toYYYYMM(ts)",
                },
            ),
            ModelTable(
                name="event_logs",
                columns=[
                    ModelColumn("id", "UInt64", False, True, False, None, None),
                    ModelColumn("ts", "DateTime", False, False, False, None, None),
                ],
                clickhouse_options={
                    "ch_engine": "MergeTree",
                    "ch_order_by": ["ts"],
                },
            ),
        ]

        curr_tables = [
            ModelTable(
                name="accounts",
                columns=[
                    ModelColumn("id", "INTEGER", False, True, False, None, None, autoincrement=True),
                    ModelColumn("email", "VARCHAR(255)", False, False, False, None, None, comment="contact email"),
                    ModelColumn("role", "VARCHAR(50)", True, False, False, None, None),
                    ModelColumn("status", "INTEGER", True, False, False, 1, None),
                    ModelColumn("bio", "TEXT", True, False, False, None, None),
                    ModelColumn("age", "INTEGER", True, False, False, None, None),
                    ModelColumn("group_id", "INTEGER", True, False, False, None, None),
                    ModelColumn("handle", "VARCHAR(50)", True, False, False, None, None),
                ],
                indexes=[IndexInfo(name="ix_accounts_email", columns=["email"], unique=False)],
                uniques=[{"name": "uq_accounts_handle", "columns": ["handle"]}],
                comment="User accounts",
                pg_table={"pg_fillfactor": 80},
            ),
            ModelTable(
                name="groups",
                columns=[
                    ModelColumn("id", "INTEGER", False, True, False, None, None),
                ],
            ),
            ModelTable(
                name="inventory",
                columns=[
                    ModelColumn("id", "INTEGER", False, True, False, None, None),
                    ModelColumn("sku", "VARCHAR(64)", False, False, False, None, None),
                    ModelColumn("qty", "INTEGER", False, False, False, None, None),
                ],
                my_table={
                    "my_engine": "myisam",
                    "my_charset": "latin1",
                    "my_collate": "latin1_swedish_ci",
                    "my_row_format": "compact",
                    "my_auto_increment": 42,
                },
            ),
            ModelTable(
                name="events",
                columns=[
                    ModelColumn("id", "UInt64", False, True, False, None, None),
                    ModelColumn("ts", "DateTime", False, False, False, None, None),
                    ModelColumn("payload", "String", True, False, False, None, None),
                ],
                clickhouse_options={
                    "ch_engine": "ReplacingMergeTree",
                    "ch_order_by": ["id", "ts"],
                    "ch_partition_by": "toYYYYMM(ts)",
                    "ch_ttl": ["ts + INTERVAL 1 DAY"],
                },
            ),
            ModelTable(
                name="event_logs",
                columns=[
                    ModelColumn("id", "UInt64", False, True, False, None, None),
                    ModelColumn("ts", "DateTime", False, False, False, None, None),
                ],
                clickhouse_options={
                    "ch_engine": "MergeTree",
                    "ch_order_by": ["ts", "id"],
                    "ch_ttl": ["ts + INTERVAL 1 DAY"],
                },
            ),
        ]

        prev_state = model_state_to_dict(prev_tables)
        curr_state = model_state_to_dict(curr_tables)
        snapshot = model_state_to_dict(prev_tables)
        for table in prev_tables:
            snapshot["tables"][table.name]["ch_options"] = dict(table.clickhouse_options)
            snapshot["tables"][table.name]["pg_table"] = dict(table.pg_table)
            snapshot["tables"][table.name]["my_table"] = dict(table.my_table)

        def _backend_for_table(state: dict, table_name: str) -> str | None:
            table = state.get("tables", {}).get(table_name, {})
            return (table.get("backend_table_spec") or {}).get("backend")

        def _sorted_ops(ops: list[dict], state: dict, backend: str) -> list[dict]:
            relevant = [
                op for op in ops
                if _backend_for_table(state, op.get("table", "")) == backend
            ]
            return sorted(
                relevant,
                key=lambda op: json.dumps(
                    {k: v for k, v in op.items() if not k.startswith("__")},
                    sort_keys=True,
                    default=str,
                ),
            )

        def _render(ops: list[dict], rollback_ops: list[dict], state: dict, backend: str) -> tuple[str, str]:
            return snapshot_diff_to_sql(
                _sorted_ops(ops, state, backend),
                _sorted_ops(rollback_ops, state, backend),
                db_name=backend,
            )[:2]

        online_upgrade, online_rollback = diff_models_against_snapshot(
            curr_tables,
            snapshot,
            db_name="postgresql",
            clickhouse_engine_recreate=True,
        )
        offline_upgrade, offline_rollback = diff_model_states(prev_state, curr_state)

        for backend in ("postgresql", "mysql", "clickhouse"):
            online_sql, online_rb_sql = _render(online_upgrade, online_rollback, prev_state, backend)
            offline_sql, offline_rb_sql = _render(offline_upgrade, offline_rollback, prev_state, backend)
            assert online_sql == offline_sql
            assert online_rb_sql == offline_rb_sql
    def test_compute_overlap_high(self):
        snapshot = {
            "tables": {
                "users": {
                    "columns": {
                        "id": {"type": "integer", "nullable": False},
                        "name": {"type": "varchar", "nullable": True},
                        "email": {"type": "varchar", "nullable": True},
                    }
                }
            }
        }
        model_tables = [
            ModelTable(
                name="accounts",
                columns=[
                    ModelColumn("id", "INTEGER", False, True, False, None, None),
                    ModelColumn("name", "VARCHAR", True, False, False, None, None),
                    ModelColumn("email", "VARCHAR", True, False, False, None, None),
                ],
            )
        ]
        ratio = _compute_table_overlap("users", "accounts", snapshot, model_tables)
        assert ratio >= 0.6

    def test_compute_overlap_low(self):
        snapshot = {
            "tables": {
                "users": {
                    "columns": {
                        "id": {"type": "integer", "nullable": False},
                        "legacy": {"type": "varchar", "nullable": True},
                    }
                }
            }
        }
        model_tables = [
            ModelTable(
                name="accounts",
                columns=[
                    ModelColumn("id", "INTEGER", False, True, False, None, None),
                    ModelColumn("name", "VARCHAR", True, False, False, None, None),
                    ModelColumn("email", "VARCHAR", True, False, False, None, None),
                ],
            )
        ]
        ratio = _compute_table_overlap("users", "accounts", snapshot, model_tables)
        assert ratio < 0.6

    def test_compute_overlap_empty_tables(self):
        snapshot = {
            "tables": {
                "empty_snap": {"columns": {}},
            }
        }
        model_tables = [
            ModelTable(name="empty_model", columns=[]),
        ]
        ratio = _compute_table_overlap("empty_snap", "empty_model", snapshot, model_tables)
        assert ratio == 0.0

    def test_rename_table_sql_generates_statement(self):
        intent = TableRenameIntent(old_table="users", new_table="accounts")
        stmt = _rename_table_sql(intent, "postgresql")
        assert stmt.order == StatementOrder.RENAME_TABLE
        assert "ALTER TABLE users RENAME TO accounts;" in stmt.upgrade_sql
        assert "ALTER TABLE accounts RENAME TO users;" in stmt.rollback_sql

    def test_rename_table_sql_clickhouse_rename(self):
        intent = TableRenameIntent(old_table="users", new_table="accounts")
        stmt = _rename_table_sql(intent, "clickhouse")
        assert "RENAME TABLE users TO accounts;" in stmt.upgrade_sql
        assert "RENAME TABLE accounts TO users;" in stmt.rollback_sql

    def test_rename_table_sql_sqlite(self):
        intent = TableRenameIntent(old_table="users", new_table="accounts")
        stmt = _rename_table_sql(intent, "sqlite")
        assert stmt.order == StatementOrder.RENAME_TABLE
        assert "ALTER TABLE users RENAME TO accounts;" in stmt.upgrade_sql

    def test_rename_table_op_in_snapshot_diff_to_sql(self):
        ops = [
            {"type": "rename_table", "old_table": "users", "new_table": "accounts"},
        ]
        rollback_ops = [
            {"type": "rename_table", "old_table": "accounts", "new_table": "users"},
        ]
        sql, rb_sql, changes = snapshot_diff_to_sql(ops, rollback_ops, db_name=None)
        assert "ALTER TABLE users RENAME TO accounts;" in sql
        assert "ALTER TABLE accounts RENAME TO users;" in rb_sql
        assert any(c.operation == "rename_table" for c in changes)

    def test_rename_table_ordering_first(self):
        ops = [
            {"type": "add_column", "table": "accounts", "column": "email", "model_column": ModelColumn("email", "VARCHAR", True, False, False, None, None)},
            {"type": "rename_table", "old_table": "users", "new_table": "accounts"},
        ]
        rollback_ops = [
            {"type": "drop_column", "table": "accounts", "column": "email"},
            {"type": "rename_table", "old_table": "accounts", "new_table": "users"},
        ]
        sql, rb_sql, changes = snapshot_diff_to_sql(ops, rollback_ops, db_name=None)
        parts = sql.split("\n\n")
        assert "ALTER TABLE users RENAME TO accounts;" in parts[0]
        assert "ALTER TABLE accounts RENAME TO users;" in rb_sql



class TestClickHouseDiff:
    def test_snapshot_diff_to_sql_emits_clickhouse_projection_rollback(self):
        ops = [{
            "type": "alter_ch_projection",
            "table": "events",
            "add": [{"name": "by_id", "query": "(SELECT id ORDER BY id)"}],
            "drop": [],
            "replace": [],
            "__rollback_attrs": {
                "table": "events",
                "add": [],
                "drop": ["by_id"],
                "replace": [],
            },
        }]

        sql, rb_sql, _changes = snapshot_diff_to_sql(ops, [], db_name="clickhouse")

        assert "ADD PROJECTION by_id" in sql
        assert "DROP PROJECTION by_id" in rb_sql

    def test_snapshot_diff_to_sql_emits_clickhouse_skip_index_rollback(self):
        ops = [{
            "type": "alter_ch_skip_index",
            "table": "events",
            "add": [{
                "name": "ix_id",
                "columns": ["id"],
                "clickhouse_type": "minmax",
                "clickhouse_granularity": 1,
            }],
            "drop": [],
            "replace": [],
            "__rollback_attrs": {
                "table": "events",
                "add": [],
                "drop": ["ix_id"],
                "replace": [],
            },
        }]

        sql, rb_sql, _changes = snapshot_diff_to_sql(ops, [], db_name="clickhouse")

        assert "ADD INDEX ix_id" in sql
        assert "DROP INDEX ix_id" in rb_sql

    def test_diff_models_detects_clickhouse_option_change(self):
        model_tables = [
            ModelTable(
                name="events",
                columns=[
                    ModelColumn("id", "UInt64", False, True, False, None, None, ch_meta={"ch_type": "UInt64"}),
                ],
                clickhouse_options={
                    "ch_engine": "MergeTree",
                    "ch_order_by": ["id"],
                },
            )
        ]
        snapshot = {
            "tables": {
                "events": {
                    "object_type": "table",
                    "columns": {
                        "id": {
                            "type": "UInt64",
                            "nullable": False,
                            "default": None,
                            "ch_column": {"ch_type": "UInt64"},
                        }
                    },
                    "ch_options": {
                        "ch_engine": "ReplacingMergeTree",
                        "ch_order_by": ["id"],
                    },
                    "indexes": {},
                }
            },
            "indexes": {},
            "constraints": {},
        }

        upgrade, rollback = diff_models_against_snapshot(model_tables, snapshot, clickhouse_engine_recreate=True)

        assert any(op["type"] == "recreate_ch_table" for op in upgrade)
        assert any(op["type"] == "recreate_ch_table" for op in rollback)

    def test_recreate_preserves_projections(self):
        model_tables = [
            ModelTable(
                name="events",
                columns=[
                    ModelColumn("id", "UInt64", False, True, False, None, None, ch_meta={"ch_type": "UInt64"}),
                ],
                clickhouse_options={
                    "ch_engine": "MergeTree",
                    "ch_order_by": ["id"],
                },
            )
        ]
        snapshot = {
            "tables": {
                "events": {
                    "object_type": "table",
                    "columns": {
                        "id": {
                            "type": "UInt64",
                            "nullable": False,
                            "default": None,
                            "ch_column": {"ch_type": "UInt64"},
                        }
                    },
                    "ch_options": {
                        "ch_engine": "ReplacingMergeTree",
                        "ch_order_by": ["id"],
                        "ch_projections": [{"name": "by_id", "query": "SELECT id ORDER BY id"}],
                    },
                    "indexes": {},
                }
            },
            "indexes": {},
            "constraints": {},
        }
        upgrade, rollback = diff_models_against_snapshot(model_tables, snapshot, clickhouse_engine_recreate=True)
        assert any(op["type"] == "recreate_ch_table" for op in upgrade)

    def test_recreate_inline_materialized_view(self):
        """Inline MV (no TO target) now recreates with DROP VIEW + CREATE MATERIALIZED VIEW."""
        model_tables = [
            ModelTable(
                name="events",
                columns=[
                    ModelColumn("id", "UInt64", False, True, False, None, None, ch_meta={"ch_type": "UInt64"}),
                ],
                clickhouse_options={
                    "ch_engine": "MergeTree",
                    "ch_order_by": ["id"],
                },
            )
        ]
        snapshot = {
            "tables": {
                "events": {
                    "object_type": "materialized_view",
                    "columns": {
                        "id": {
                            "type": "UInt64",
                            "nullable": False,
                            "default": None,
                            "ch_column": {"ch_type": "UInt64"},
                        }
                    },
                    "ch_options": {
                        "ch_engine": "ReplicatedMergeTree",
                        "ch_order_by": ["id"],
                        "ch_select_statement": "SELECT id FROM source",
                    },
                    "indexes": {},
                }
            },
            "indexes": {},
            "constraints": {},
        }
        upgrade, rollback = diff_models_against_snapshot(model_tables, snapshot, clickhouse_engine_recreate=True)
        recreate = next(op for op in upgrade if op["type"] == "recreate_ch_table")
        assert recreate is not None

    def test_recreate_dictionary(self):
        model_tables = [
            ModelTable(
                name="events",
                columns=[
                    ModelColumn("id", "UInt64", False, True, False, None, None, ch_meta={"ch_type": "UInt64"}),
                ],
                clickhouse_options={
                    "ch_engine": "MergeTree",
                    "ch_order_by": ["id"],
                },
            )
        ]
        snapshot = {
            "tables": {
                "events": {
                    "object_type": "dictionary",
                    "columns": {
                        "id": {
                            "type": "UInt64",
                            "nullable": False,
                            "default": None,
                            "ch_column": {"ch_type": "UInt64"},
                        }
                    },
                    "ch_options": {
                        "ch_engine": "ReplicatedMergeTree",
                        "ch_order_by": ["id"],
                        "ch_dictionary": True,
                    },
                    "indexes": {},
                }
            },
            "indexes": {},
            "constraints": {},
        }
        upgrade, rollback = diff_models_against_snapshot(model_tables, snapshot, clickhouse_engine_recreate=True)
        assert any(op["type"] == "recreate_ch_table" for op in upgrade)

    def test_recreate_annotates_dependent_mvs(self):
        model_tables = [
            ModelTable(
                name="events",
                columns=[
                    ModelColumn("id", "UInt64", False, True, False, None, None, ch_meta={"ch_type": "UInt64"}),
                ],
                clickhouse_options={
                    "ch_engine": "MergeTree",
                    "ch_order_by": ["id"],
                },
            ),
            ModelTable(
                name="events_mv",
                columns=[
                    ModelColumn("id", "UInt64", False, True, False, None, None, ch_meta={"ch_type": "UInt64"}),
                ],
                clickhouse_options={
                    "ch_engine": "MergeTree",
                    "ch_select_statement": "SELECT id FROM source",
                    "ch_to_table": "events",
                },
            ),
        ]
        snapshot = {
            "tables": {
                "events": {
                    "object_type": "table",
                    "columns": {
                        "id": {
                            "type": "UInt64",
                            "nullable": False,
                            "default": None,
                            "ch_column": {"ch_type": "UInt64"},
                        }
                    },
                    "ch_options": {
                        "ch_engine": "ReplicatedMergeTree",
                        "ch_order_by": ["id"],
                    },
                    "indexes": {},
                },
                "events_mv": {
                    "object_type": "materialized_view",
                    "columns": {
                        "id": {
                            "type": "UInt64",
                            "nullable": False,
                            "default": None,
                            "ch_column": {"ch_type": "UInt64"},
                        }
                    },
                    "ch_options": {
                        "ch_engine": "MergeTree",
                        "ch_select_statement": "SELECT id FROM source",
                        "ch_to_table": "events",
                    },
                    "indexes": {},
                },
            },
            "indexes": {},
            "constraints": {},
        }
        upgrade, rollback = diff_models_against_snapshot(model_tables, snapshot, clickhouse_engine_recreate=True)
        recreate = next(op for op in upgrade if op["type"] == "recreate_ch_table")
        assert recreate.get("dependent_mvs") == ["events_mv"]

    def test_snapshot_diff_to_sql_clickhouse_ch_ops(self, monkeypatch):
        monkeypatch.setattr("dbwarden.engine.snapshot._get_backend", lambda db_name=None: "clickhouse")

        ops = [
            {
                "type": "alter_ch_options",
                "table": "events",
                "changes": {"ch_ttl": {"from": ["old_ttl"], "to": ["new_ttl"]}},
            },
            {
                "type": "alter_ch_column",
                "table": "events",
                "column": "payload",
                "from_ch_column": {"ch_type": "String"},
                "to_ch_column": {"ch_type": "LowCardinality(String)", "ch_codec": "ZSTD(3)"},
            },
            {
                "type": "drop_table",
                "table": "dict_events",
                "object_type": "dictionary",
            },
        ]

        sql, rollback_sql, changes = snapshot_diff_to_sql(ops, [], db_name="clickhouse")

        assert "ALTER TABLE events MODIFY TTL new_ttl" in sql
        assert "ALTER TABLE events MODIFY COLUMN payload LowCardinality(String)" in sql
        assert "DROP DICTIONARY dict_events" in sql
        assert changes

    def test_snapshot_diff_to_sql_recreate_with_mixed_ops(self, monkeypatch):
        """recreate_ch_table coexists with other op types for different tables."""
        monkeypatch.setattr("dbwarden.engine.model_discovery.type_mapping._get_backend_name", lambda db_name=None: "clickhouse")
        state_events = {
            "name": "events",
            "object_type": "table",
            "columns": {
                "id": {
                    "name": "id", "type": "UInt64",
                    "nullable": False, "primary_key": True,
                    "unique": False, "default": None, "foreign_key": None,
                    "comment": None, "autoincrement": None,
                    "pg_column": {}, "ch_column": {"ch_type": "UInt64"},
                }
            },
            "indexes": [], "foreign_keys": [], "checks": [], "uniques": [],
            "comment": None, "backend": "clickhouse",
            "backend_table_spec": {
                "backend": "clickhouse",
                "ch_engine": "MergeTree", "ch_order_by": ["id"],
            },
        }
        new_state_events = dict(state_events)
        new_state_events["backend_table_spec"] = {
            "backend": "clickhouse",
            "ch_engine": "ReplicatedMergeTree", "ch_order_by": ["id"],
        }

        ops = [
            {
                "type": "recreate_ch_table",
                "table": "events",
                "reason": "ch_engine",
                "from_table": state_events,
                "to_table": new_state_events,
                "drop_old_after_swap": False,
                "preserve_old_suffix": "__dbw_old",
                "failed_suffix": "__dbw_failed",
            },
            {
                "type": "add_column",
                "table": "audit_log",
                "column": "action",
                "definition": {"type": "varchar"},
            },
        ]
        rollback_ops = [
            {
                "type": "recreate_ch_table",
                "table": "events",
                "reason": "ch_engine",
                "from_table": new_state_events,
                "to_table": state_events,
                "drop_old_after_swap": False,
                "preserve_old_suffix": "__dbw_old",
                "failed_suffix": "__dbw_failed",
            },
        ]
        sql, rb_sql, changes = snapshot_diff_to_sql(ops, rollback_ops, db_name="clickhouse")
        assert "CREATE TABLE IF NOT EXISTS events__dbw_new" in sql
        assert "RENAME TABLE events TO events__dbw_old, events__dbw_new TO events;" in sql
        assert "ALTER TABLE audit_log ADD COLUMN action" in sql
        assert len([c for c in changes if c.operation == "recreate_ch_table"]) == 1

    def test_snapshot_parse_settings(self):
        from dbwarden.engine.snapshot import _parse_settings
        result = _parse_settings("CREATE TABLE t (id UInt64) ENGINE = MergeTree() ORDER BY id SETTINGS index_granularity=8192, min_rows_for_wide_part=0")
        assert result == {"index_granularity": "8192", "min_rows_for_wide_part": "0"}
        assert _parse_settings("CREATE TABLE t (id UInt64) ENGINE = MergeTree() ORDER BY id") is None

    def test_snapshot_parse_ttl_expressions(self):
        from dbwarden.engine.snapshot import _parse_ttl_expressions
        result = _parse_ttl_expressions(
            "CREATE TABLE t (id UInt64) ENGINE = MergeTree() ORDER BY id TTL created_at + INTERVAL 30 DAY"
        )
        assert "created_at + INTERVAL 30 DAY" in result
        assert _parse_ttl_expressions("CREATE TABLE t (id UInt64) ENGINE = MergeTree() ORDER BY id") == []

    def test_snapshot_parse_ttl_preserves_function_arguments(self):
        from dbwarden.engine.snapshot import _parse_ttl_expressions
        result = _parse_ttl_expressions(
            "CREATE TABLE t ENGINE = MergeTree() ORDER BY id TTL if(flag, created_at, updated_at) + INTERVAL 30 DAY"
        )
        assert result == ["if(flag, created_at, updated_at) + INTERVAL 30 DAY"]

    def test_snapshot_parse_projection_names(self):
        from dbwarden.engine.snapshot import _parse_projection_names
        result = _parse_projection_names(
            "CREATE TABLE t (id UInt64) ENGINE = MergeTree() ORDER BY id PROJECTION p1 (SELECT * ORDER BY id)"
        )
        assert "p1" in result

    def test_snapshot_parse_mv_query(self):
        from dbwarden.engine.snapshot import _parse_mv_query
        result = _parse_mv_query(
            "CREATE MATERIALIZED VIEW mv ENGINE = MergeTree() AS SELECT id FROM source"
        )
        assert result == "SELECT id FROM source"
        assert _parse_mv_query("CREATE TABLE t (id UInt64) ENGINE = MergeTree()") is None

    def test_snapshot_parse_zookeeper_path(self):
        from dbwarden.engine.snapshot import _parse_zookeeper_path
        result = _parse_zookeeper_path(
            "CREATE TABLE t (id UInt64) ENGINE = ReplicatedMergeTree('/zk/path', '{replica}') ORDER BY id", "ReplicatedMergeTree"
        )
        assert result == "/zk/path"

    def test_snapshot_parse_dict_layout(self):
        from dbwarden.engine.snapshot import _parse_dict_layout
        result = _parse_dict_layout(
            "CREATE DICTIONARY d (id UInt64) PRIMARY KEY id SOURCE(CLICKHOUSE()) LIFETIME(300) LAYOUT(FLAT())"
        )
        assert result is not None
        assert "FLAT" in result
        assert _parse_dict_layout("CREATE TABLE t (id UInt64) ENGINE = MergeTree()") is None

    def test_snapshot_parse_dict_source(self):
        from dbwarden.engine.snapshot import _parse_dict_source
        result = _parse_dict_source(
            "CREATE DICTIONARY d (id UInt64) PRIMARY KEY id SOURCE(CLICKHOUSE(HOST 'localhost')) LIFETIME(300) LAYOUT(FLAT())"
        )
        assert result is not None
        assert "CLICKHOUSE(HOST 'localhost'" in result

    def test_snapshot_parse_dict_lifetime(self):
        from dbwarden.engine.snapshot import _parse_dict_lifetime
        result = _parse_dict_lifetime(
            "CREATE DICTIONARY d (id UInt64) PRIMARY KEY id SOURCE(CLICKHOUSE()) LIFETIME(300) LAYOUT(FLAT())"
        )
        assert result == 300
        assert _parse_dict_lifetime("CREATE TABLE t (id UInt64) ENGINE = MergeTree()") is None

    def test_snapshot_parse_dict_primary_key(self):
        from dbwarden.engine.snapshot import _parse_dict_primary_key
        result = _parse_dict_primary_key(
            "CREATE DICTIONARY d (id UInt64, name String) PRIMARY KEY id SOURCE(CLICKHOUSE()) LIFETIME(300) LAYOUT(FLAT())"
        )
        assert result is not None
        assert result.startswith("id")

    def test_diff_models_ch_add_table(self):
        model_tables = [
            ModelTable(
                name="events",
                columns=[ModelColumn("id", "UInt64", False, True, False, None, None)],
                clickhouse_options={"ch_engine": "MergeTree", "ch_order_by": ["id"]},
            )
        ]
        upgrade, rollback = diff_models_against_snapshot(model_tables, {"tables": {}, "indexes": {}, "constraints": {}})
        create_ops = [op for op in upgrade if op["type"] == "create_table"]
        assert len(create_ops) == 1



class TestBugFixRegression:
    """Regression tests for bug fixes in v0.12.1."""

    @pytest.mark.parametrize("backend", ("mysql", "mariadb"))
    def test_mysql_alter_default_with_col_type(self, backend):
        """Bug 2c/5: MySQL default changes use MODIFY COLUMN when type is available."""
        from dbwarden.engine.snapshot import _build_alter_default_sql
        upgrade, rollback = _build_alter_default_sql(
            "t", "c", "hello", backend,
            col_type="VARCHAR(255)", nullable=False,
        )
        assert "MODIFY COLUMN" in upgrade
        assert "VARCHAR(255)" in upgrade
        assert "NOT NULL" in upgrade
        assert "DEFAULT 'hello'" in upgrade
        assert "MODIFY COLUMN" in rollback

    @pytest.mark.parametrize("backend", ("postgresql", "sqlite"))
    def test_non_mysql_alter_default_unchanged(self, backend):
        """Non-MySQL backends still use ALTER COLUMN SET DEFAULT."""
        from dbwarden.engine.snapshot import _build_alter_default_sql
        upgrade, rollback = _build_alter_default_sql(
            "t", "c", "42", backend,
        )
        assert "ALTER COLUMN" in upgrade
        assert "SET DEFAULT 42" in upgrade
        assert "ALTER COLUMN" in rollback
        assert "DROP DEFAULT" in rollback

    def test_clickhouse_alter_default_uses_modify_column(self):
        """ClickHouse default changes use MODIFY COLUMN DEFAULT / REMOVE DEFAULT."""
        from dbwarden.engine.snapshot import _build_alter_default_sql
        upgrade, rollback = _build_alter_default_sql(
            "t", "c", "42", "clickhouse", col_type="Int64",
        )
        assert "ALTER TABLE t MODIFY COLUMN c Int64 DEFAULT 42" == upgrade
        assert "ALTER TABLE t MODIFY COLUMN c Int64 REMOVE DEFAULT" == rollback

    def test_clickhouse_alter_default_drop_uses_remove_default(self):
        """ClickHouse DROP DEFAULT uses REMOVE DEFAULT; rollback restores the old default."""
        from dbwarden.engine.snapshot import _build_alter_default_sql
        upgrade, rollback = _build_alter_default_sql(
            "t", "c", None, "clickhouse", col_type="String",
        )
        assert "ALTER TABLE t MODIFY COLUMN c String REMOVE DEFAULT" == upgrade
        assert "DEFAULT <original_def_unavailable>" in rollback
        assert "REMOVE DEFAULT" not in rollback

    def test_clickhouse_alter_nullable_uses_ch_type(self):
        """ClickHouse nullability changes use the CH type, not an SA type."""
        from dbwarden.engine.snapshot import _build_alter_nullable_sql
        upgrade, rollback = _build_alter_nullable_sql(
            "t", "c", nullable=True, col_type="Int64", backend="clickhouse",
        )
        assert "ALTER TABLE t MODIFY COLUMN c Nullable(Int64)" == upgrade
        assert "ALTER TABLE t MODIFY COLUMN c Int64" == rollback

    def test_clickhouse_alter_nullable_sa_type_is_commented(self):
        """Passing a generic SQLAlchemy type to the CH branch emits a safe comment."""
        from dbwarden.engine.snapshot import _build_alter_nullable_sql
        upgrade, rollback = _build_alter_nullable_sql(
            "t", "c", nullable=False, col_type="VARCHAR(255)", backend="clickhouse",
        )
        assert upgrade.startswith("-- ClickHouse:")
        assert "non-CH type" in upgrade
        assert rollback.startswith("-- ClickHouse:")

    def test_mysql_alter_default_fallback_no_col_type(self):
        """MySQL emits a hard-failure comment when type info is missing."""
        from dbwarden.engine.snapshot import _build_alter_default_sql
        upgrade, rollback = _build_alter_default_sql(
            "t", "c", "42", "mysql",
        )
        assert upgrade.startswith("-- MANUAL ACTION REQUIRED:")
        assert rollback.startswith("-- MANUAL ACTION REQUIRED:")
        assert "REQUIRES MANUAL COLUMN DEFINITION" in upgrade

    def test_mysql_alter_default_drop_with_col_type(self):
        """MySQL DROP DEFAULT emits MODIFY COLUMN with full definition."""
        from dbwarden.engine.snapshot import _build_alter_default_sql
        upgrade, rollback = _build_alter_default_sql(
            "t", "c", None, "mysql",
            col_type="INT", nullable=True,
        )
        assert "MODIFY COLUMN c INT NULL" in upgrade
        assert "REQUIRES MANUAL COLUMN DEFINITION" in rollback

    def test_mysql_alter_default_drop_without_col_type(self):
        from dbwarden.engine.snapshot import _build_alter_default_sql
        upgrade, rollback = _build_alter_default_sql("t", "c", None, "mysql")
        assert upgrade.startswith("-- MANUAL ACTION REQUIRED:")
        assert rollback.startswith("-- MANUAL ACTION REQUIRED:")

    def test_missing_def_placeholder_returns_non_executable(self):
        """Placeholder values should not look like valid SQL."""
        from dbwarden.engine.snapshot import _missing_def_placeholder
        mysql_pl = _missing_def_placeholder("mysql")
        assert "/*" in mysql_pl and "*/" in mysql_pl
        assert "???" not in mysql_pl
        assert "REQUIRES" in mysql_pl
        other_pl = _missing_def_placeholder("postgresql")
        assert "<original_def_unavailable>" in other_pl
        assert "???" not in other_pl

    def test_assert_complete_mysql_type_valid(self):
        """Valid MySQL types should not raise."""
        from dbwarden.engine.snapshot import _assert_complete_mysql_type
        _assert_complete_mysql_type("VARCHAR(255)")
        _assert_complete_mysql_type("INT")
        _assert_complete_mysql_type("BIGINT UNSIGNED")
        _assert_complete_mysql_type("TEXT")
        _assert_complete_mysql_type("DECIMAL(10,2)")
        _assert_complete_mysql_type("ENUM('a','b')")

    def test_assert_complete_mysql_type_invalid(self):
        """Bare VARCHAR/CHAR without length should raise ValueError."""
        from dbwarden.engine.snapshot import _assert_complete_mysql_type
        with pytest.raises(ValueError, match="VARCHAR"):
            _assert_complete_mysql_type("VARCHAR")
        with pytest.raises(ValueError, match="CHAR"):
            _assert_complete_mysql_type("CHAR")
        with pytest.raises(ValueError, match="ENUM"):
            _assert_complete_mysql_type("ENUM")

    def test_quote_default_for_sql_string_literals(self):
        """String literals are quoted; numbers/keywords/functions are not."""
        from dbwarden.engine.snapshot import _quote_default_for_sql as q
        assert q("hello") == "'hello'"
        assert q("42") == "42"
        assert q("TRUE") == "TRUE"
        assert q("now()") == "now()"
        assert q("CURRENT_TIMESTAMP") == "CURRENT_TIMESTAMP"
        assert q("it's") == "'it''s'"
        assert q("") == "NULL"
        assert q("  ") == "NULL"

    def test_mysql_alter_type_rejects_bare_varchar(self):
        """MySQL type-change generation should reject bare VARCHAR."""
        from dbwarden.engine.snapshot import _build_alter_type_sql
        with pytest.raises(ValueError, match="VARCHAR"):
            _build_alter_type_sql(
                "t", "c", "VARCHAR", "mysql",
                old_type="VARCHAR(255)",
            )

    def test_mysql_alter_type_rejects_bare_old_type(self):
        from dbwarden.engine.snapshot import _build_alter_type_sql
        with pytest.raises(ValueError, match="VARCHAR"):
            _build_alter_type_sql(
                "t", "c", "TEXT", "mysql",
                old_type="VARCHAR",
            )

    def test_mysql_composite_pk_autoincrement_uses_snapshot_pk_count(self):
        import dbwarden.engine.snapshot as snapshot_module
        model_tables = [
            ModelTable(
                name="join_table",
                columns=[
                    ModelColumn("left_id", "BIGINT", False, True, False, None, None, autoincrement=False),
                    ModelColumn("right_id", "BIGINT", False, True, False, None, None, autoincrement=False),
                ],
            )
        ]
        snapshot = {
            "tables": {
                "join_table": {
                    "columns": {
                        "left_id": {"type": "BIGINT", "nullable": False, "primary_key": True, "autoincrement": True},
                        "right_id": {"type": "BIGINT", "nullable": False, "primary_key": True, "autoincrement": True},
                    }
                }
            }
        }
        original_get_backend = snapshot_module._get_backend
        snapshot_module._get_backend = lambda _name: "mysql"
        try:
            upgrade_ops, rollback_ops = diff_models_against_snapshot(model_tables, snapshot, db_name="primary")
        finally:
            snapshot_module._get_backend = original_get_backend
        assert not any(op["type"] == "alter_column_autoincrement" for op in upgrade_ops)
        assert not any(op["type"] == "alter_column_autoincrement" for op in rollback_ops)

    def test_clickhouse_new_table_suppresses_alter_ch_options(self, monkeypatch):
        """A brand-new ClickHouse table must not emit redundant alter_ch_options."""
        monkeypatch.setattr("dbwarden.engine.model_discovery.type_mapping._get_backend_name", lambda db_name=None: "clickhouse")
        model_tables = [
            ModelTable(
                name="events",
                columns=[ModelColumn("id", "UInt64", False, True, False, None, None)],
                clickhouse_options={"ch_engine": "MergeTree", "ch_order_by": ["id"]},
            )
        ]
        upgrade, rollback = diff_models_against_snapshot(
            model_tables, {"tables": {}, "indexes": {}, "constraints": {}}, db_name="analytics"
        )
        assert any(op["type"] == "create_table" for op in upgrade)
        assert not any(op["type"] == "alter_ch_options" for op in upgrade)
        assert not any(op["type"] == "alter_ch_options" for op in rollback)

    def test_clickhouse_inner_tables_filtered_from_diff(self, monkeypatch):
        """ClickHouse implicit MV storage tables (.inner_id.*) must not appear in diffs."""
        monkeypatch.setattr("dbwarden.engine.model_discovery.type_mapping._get_backend_name", lambda db_name=None: "clickhouse")
        model_tables = [
            ModelTable(
                name="events",
                columns=[ModelColumn("id", "UInt64", False, True, False, None, None)],
                clickhouse_options={"ch_engine": "MergeTree", "ch_order_by": ["id"]},
            )
        ]
        snapshot = {
            "tables": {
                "events": {
                    "columns": {"id": {"type": "UInt64", "nullable": False, "primary_key": True}},
                    "ch_options": {"ch_engine": "MergeTree", "ch_order_by": ["id"]},
                },
                ".inner_id.640454c2-38b8-479e-b241-eff4ea257209": {
                    "columns": {},
                    "ch_options": {"ch_engine": "MergeTree", "ch_order_by": ["id"]},
                },
            },
            "indexes": {},
            "constraints": {},
        }
        upgrade, rollback = diff_models_against_snapshot(model_tables, snapshot, db_name="analytics")
        assert not any(op.get("table", "").startswith(".inner") for op in upgrade + rollback)
        assert not any(op.get("type") == "drop_table" and op.get("table", "").startswith(".inner") for op in upgrade)

    def test_check_migration_scope_warns_large_drift(self):
        """Large migrations produce warnings."""
        from dbwarden.commands.make_migrations import _check_migration_scope
        ops = []
        for i in range(10):
            ops.append({"type": "create_table", "table": f"t{i}"})
        for i in range(8):
            ops.append({"type": "drop_table", "table": f"t{i}"})
        for i in range(6):
            ops.append({"type": "drop_column", "table": "t", "column": f"c{i}"})
        warnings = _check_migration_scope(ops)
        assert any("creates 10 tables" in w for w in warnings)
        assert any("drops 8 tables" in w for w in warnings)
        assert any("drops 6 columns" in w for w in warnings)

    def test_check_migration_scope_no_warnings_small(self):
        """Small focused migrations produce no warnings."""
        from dbwarden.commands.make_migrations import _check_migration_scope
        ops = [{"type": "create_table", "table": "t"}]
        warnings = _check_migration_scope(ops)
        assert len(warnings) == 0

    def test_build_migration_plan_has_summary(self):
        """Migration plan includes summary with operation counts."""
        from dbwarden.commands.make_migrations import build_migration_plan
        from dbwarden.engine.migration_name import Change
        changes = [
            Change(operation="create_table", table="users"),
            Change(operation="add_column", table="users", target="email"),
        ]
        plan = build_migration_plan("test_migration", changes, "SELECT 1")
        assert "summary" in plan
        summary = plan["summary"]
        assert summary["total_operations"] == 2
        assert summary["create_tables"] == 1
        assert summary["drop_tables"] == 0
        assert summary["drop_columns"] == 0



class TestPGBugRegression:
    """Regression tests for PostgreSQL bug fixes A, C, D, and autoincrement normalization."""

    def test_unique_index_backing_constraint_filtered(self):
        """Bug A: unique index backing a unique constraint is not emitted as drop_index."""
        snapshot = {
            "tables": {
                "users": {
                    "columns": {
                        "id": {"type": "integer", "nullable": False, "primary_key": True},
                        "email": {"type": "varchar(255)", "nullable": True, "primary_key": False},
                    },
                    "primary_key": ["id"],
                    "comment": None,
                }
            },
            "enums": {},
            "indexes": {
                "ix_users_email": {
                    "table": "users",
                    "columns": ["email"],
                    "unique": True,
                    "name": "ix_users_email",
                },
            },
            "constraints": {
                "ix_users_email": {
                    "type": "unique",
                    "table": "users",
                    "columns": ["email"],
                    "name": "ix_users_email",
                },
            },
        }
        model_tables = [
            ModelTable(
                name="users",
                columns=[_mc("id", "INTEGER", pk=True), _mc("email", "VARCHAR(255)")],
            )
        ]
        upgrade, rollback = diff_models_against_snapshot(model_tables, snapshot)
        drop_idx = [op for op in upgrade if op["type"] == "drop_index"]
        assert len(drop_idx) == 0, f"Expected no drop_index ops, got: {drop_idx}"

    def test_view_ignores_index_diff(self):
        """Bug C: view models do not produce index diff ops."""
        snapshot = {
            "tables": {
                "active_users": {
                    "columns": {
                        "id": {"type": "integer", "nullable": False, "primary_key": True},
                    },
                    "primary_key": ["id"],
                    "comment": None,
                }
            },
            "enums": {},
            "indexes": {
                "ix_active_users_id": {
                    "table": "active_users",
                    "columns": ["id"],
                    "unique": False,
                    "name": "ix_active_users_id",
                },
            },
            "constraints": {},
        }
        model_tables = [
            ModelTable(
                name="active_users",
                columns=[_mc("id", "INTEGER", pk=True)],
                object_type="view",
                pg_view_definition="SELECT id FROM users WHERE active = true",
            )
        ]
        upgrade, rollback = diff_models_against_snapshot(model_tables, snapshot)
        drop_idx = [op for op in upgrade if op["type"] == "drop_index"]
        add_idx = [op for op in upgrade if op["type"] == "add_index"]
        assert len(drop_idx) == 0, f"Expected no drop_index for views, got: {drop_idx}"
        assert len(add_idx) == 0, f"Expected no add_index for views, got: {add_idx}"

    def test_view_ignores_constraint_diff(self):
        """Bug C: view models do not produce unique constraint diff ops."""
        snapshot = {
            "tables": {
                "active_users": {
                    "columns": {
                        "id": {"type": "integer", "nullable": False, "primary_key": True},
                    },
                    "primary_key": ["id"],
                    "comment": None,
                }
            },
            "enums": {},
            "indexes": {},
            "constraints": {
                "uq_active_users_id": {
                    "type": "unique",
                    "table": "active_users",
                    "columns": ["id"],
                    "name": "uq_active_users_id",
                },
            },
        }
        model_tables = [
            ModelTable(
                name="active_users",
                columns=[_mc("id", "INTEGER", pk=True)],
                object_type="view",
                pg_view_definition="SELECT id FROM users WHERE active = true",
            )
        ]
        upgrade, rollback = diff_models_against_snapshot(model_tables, snapshot)
        drop_uq = [op for op in upgrade if op["type"] == "drop_unique_constraint"]
        add_uq = [op for op in upgrade if op["type"] == "add_unique_constraint"]
        assert len(drop_uq) == 0, f"Expected no drop_unique_constraint for views, got: {drop_uq}"
        assert len(add_uq) == 0, f"Expected no add_unique_constraint for views, got: {add_uq}"

    def test_autoincrement_default_normalization(self, monkeypatch):
        """SERIAL column autoincrement: nextval default in snapshot is suppressed when model has autoincrement=True."""
        monkeypatch.setattr("dbwarden.engine.snapshot._get_backend", lambda db_name=None: "postgresql")

        snapshot = {
            "tables": {
                "users": {
                    "columns": {
                        "id": {
                            "type": "integer",
                            "nullable": False,
                            "primary_key": True,
                            "default": "nextval('users_id_seq'::regclass)",
                            "autoincrement": True,
                        },
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
                columns=[_mc("id", "INTEGER", pk=True)],
            )
        ]
        model_tables[0].columns[0].autoincrement = True
        upgrade, rollback = diff_models_against_snapshot(model_tables, snapshot, db_name="primary")
        alter_default = [op for op in upgrade if op["type"] == "alter_column_default"]
        assert len(alter_default) == 0, (
            f"Expected no alter_column_default for autoincrement SERIAL column, got: {alter_default}"
        )

    def test_matview_auto_refresh_emits_refresh_op(self, monkeypatch):
        """pg_view_auto_refresh=True on a materialized view emits refresh_matview op."""
        monkeypatch.setattr("dbwarden.engine.snapshot._get_backend", lambda db_name=None: "postgresql")
        snapshot = {
            "tables": {
                "user_summary": {
                    "columns": {
                        "id": {"type": "integer", "nullable": False, "primary_key": True},
                        "cnt": {"type": "bigint", "nullable": True, "primary_key": False},
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
                name="user_summary",
                columns=[
                    _mc("id", "INTEGER", pk=True, nullable=False),
                    _mc("cnt", "BIGINT"),
                ],
                object_type="materialized_view",
                pg_view_definition="SELECT id, COUNT(*) FROM users GROUP BY id",
                pg_view_materialized=True,
                pg_view_auto_refresh=True,
            )
        ]
        upgrade, rollback = diff_models_against_snapshot(model_tables, snapshot)
        refresh_ops = [op for op in upgrade if op["type"] == "refresh_matview"]
        assert len(refresh_ops) == 1
        assert refresh_ops[0]["table"] == "user_summary"

    def test_view_auto_refresh_no_op_for_regular_views(self, monkeypatch):
        """pg_view_auto_refresh=True on a non-materialized view does NOT emit refresh_matview."""
        monkeypatch.setattr("dbwarden.engine.snapshot._get_backend", lambda db_name=None: "postgresql")
        snapshot = {
            "tables": {
                "active_users": {
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
                name="active_users",
                columns=[_mc("id", "INTEGER", pk=True, nullable=False)],
                object_type="view",
                pg_view_definition="SELECT id FROM users WHERE active = true",
                pg_view_materialized=False,
                pg_view_auto_refresh=True,
            )
        ]
        upgrade, rollback = diff_models_against_snapshot(model_tables, snapshot)
        refresh_ops = [op for op in upgrade if op["type"] == "refresh_matview"]
        assert len(refresh_ops) == 0

    def test_matview_no_auto_refresh_no_op(self, monkeypatch):
        """Materialized view with pg_view_auto_refresh=False does NOT emit refresh_matview."""
        monkeypatch.setattr("dbwarden.engine.snapshot._get_backend", lambda db_name=None: "postgresql")
        snapshot = {
            "tables": {
                "user_summary": {
                    "columns": {
                        "id": {"type": "integer", "nullable": False, "primary_key": True},
                        "cnt": {"type": "bigint", "nullable": True, "primary_key": False},
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
                name="user_summary",
                columns=[
                    _mc("id", "INTEGER", pk=True, nullable=False),
                    _mc("cnt", "BIGINT"),
                ],
                object_type="materialized_view",
                pg_view_definition="SELECT id, COUNT(*) FROM users GROUP BY id",
                pg_view_materialized=True,
                pg_view_auto_refresh=False,
            )
        ]
        upgrade, rollback = diff_models_against_snapshot(model_tables, snapshot)
        refresh_ops = [op for op in upgrade if op["type"] == "refresh_matview"]
        assert len(refresh_ops) == 0





class TestPGSnapshotDiffToSql:
    def test_schema_creation_detected(self):
        """CREATE SCHEMA IF NOT EXISTS is emitted when ops carry a schema key."""
        ops = [
            {
                "type": "alter_view",
                "table": "active_users",
                "schema": "app",
                "pg_view_definition": "SELECT id FROM users",
                "pg_view_materialized": False,
                "snap_pg_view_definition": "SELECT id, name FROM users",
                "snap_pg_view_materialized": False,
            },
        ]
        rollback_ops = [
            {
                "type": "alter_view",
                "table": "active_users",
                "schema": "app",
                "pg_view_definition": "SELECT id, name FROM users",
                "pg_view_materialized": False,
                "snap_pg_view_definition": "SELECT id FROM users",
                "snap_pg_view_materialized": False,
            },
        ]
        sql, rb_sql, changes = snapshot_diff_to_sql(ops, rollback_ops, db_name=None)
        assert 'CREATE SCHEMA IF NOT EXISTS "app";' in sql
        assert 'DROP SCHEMA IF EXISTS "app";' in rb_sql

    def test_matview_upgrade_and_rollback_both_materialized(self):
        """Bug D: matview->matview emits CREATE MATERIALIZED VIEW in both upgrade and rollback."""
        ops = [
            {
                "type": "alter_view",
                "table": "user_summary",
                "schema": "public",
                "pg_view_definition": "SELECT id, COUNT(*) FROM users GROUP BY id",
                "pg_view_materialized": True,
                "snap_pg_view_definition": "SELECT id, COUNT(*) FROM old_users GROUP BY id",
                "snap_pg_view_materialized": True,
            },
        ]
        rollback_ops = [
            {
                "type": "alter_view",
                "table": "user_summary",
                "schema": "public",
                "pg_view_definition": "SELECT id, COUNT(*) FROM old_users GROUP BY id",
                "pg_view_materialized": True,
                "snap_pg_view_definition": "SELECT id, COUNT(*) FROM users GROUP BY id",
                "snap_pg_view_materialized": True,
            },
        ]
        sql, rb_sql, changes = snapshot_diff_to_sql(ops, rollback_ops, db_name=None)
        assert "DROP MATERIALIZED VIEW IF EXISTS" in sql
        assert "CREATE MATERIALIZED VIEW" in sql
        assert "DROP MATERIALIZED VIEW IF EXISTS" in rb_sql
        assert "CREATE MATERIALIZED VIEW" in rb_sql

    def test_matview_to_view_rollback_uses_matview(self):
        """Bug D: when snapshot had matview but model has view, rollback_sql uses DROP+CREATE MATERIALIZED VIEW."""
        ops = [
            {
                "type": "alter_view",
                "table": "user_summary",
                "pg_view_definition": "SELECT id FROM users",
                "pg_view_materialized": False,
                "snap_pg_view_definition": "SELECT id, COUNT(*) FROM users GROUP BY id",
                "snap_pg_view_materialized": True,
            },
        ]
        rollback_ops = [
            {
                "type": "alter_view",
                "table": "user_summary",
                "pg_view_definition": "SELECT id, COUNT(*) FROM users GROUP BY id",
                "pg_view_materialized": True,
                "snap_pg_view_definition": "SELECT id FROM users",
                "snap_pg_view_materialized": False,
            },
        ]
        sql, rb_sql, changes = snapshot_diff_to_sql(ops, rollback_ops, db_name=None)
        # upgrade: current=matview, new=view → DROP MATERIALIZED, CREATE VIEW
        assert "DROP MATERIALIZED VIEW IF EXISTS" in sql
        assert "CREATE VIEW" in sql
        # rollback_sql is derived from upgrade op's snap_mat=True → DROP MATERIALIZED + CREATE MATERIALIZED
        assert "DROP MATERIALIZED VIEW IF EXISTS" in rb_sql
        assert "CREATE MATERIALIZED VIEW" in rb_sql

    def test_alter_view_ordering_last(self):
        """ALTER_VIEW ops are emitted at position 99, after all other DDL."""
        ops = [
            {"type": "rename_column", "table": "users", "old_name": "name", "new_name": "full_name"},
            {
                "type": "alter_view",
                "table": "active_users",
                "pg_view_definition": "SELECT id, email FROM users",
                "pg_view_materialized": False,
                "snap_pg_view_definition": "SELECT id FROM users",
                "snap_pg_view_materialized": False,
            },
        ]
        rollback_ops = [
            {"type": "rename_column", "table": "users", "old_name": "full_name", "new_name": "name"},
            {
                "type": "alter_view",
                "table": "active_users",
                "pg_view_definition": "SELECT id FROM users",
                "pg_view_materialized": False,
                "snap_pg_view_definition": "SELECT id, email FROM users",
                "snap_pg_view_materialized": False,
            },
        ]
        sql, rb_sql, changes = snapshot_diff_to_sql(ops, rollback_ops, db_name=None)
        parts = [p.strip() for p in sql.split("\n\n") if p.strip()]
        alter_idx = next(i for i, p in enumerate(parts) if "CREATE VIEW" in p or "DROP VIEW" in p)
        rename_idx = next(i for i, p in enumerate(parts) if "RENAME COLUMN" in p)
        assert alter_idx > rename_idx, "ALTER_VIEW must be ordered after RENAME COLUMN"

    def test_refresh_matview_emitted_for_auto_refresh_matview(self):
        """REFRESH MATERIALIZED VIEW is emitted for matviews with pg_view_auto_refresh=True."""
        ops = [
            {
                "type": "refresh_matview",
                "table": "user_summary",
                "schema": "public",
            },
        ]
        rollback_ops = [
            {
                "type": "refresh_matview",
                "table": "user_summary",
                "schema": "public",
            },
        ]
        sql, rb_sql, changes = snapshot_diff_to_sql(ops, rollback_ops, db_name=None)
        assert "REFRESH MATERIALIZED VIEW" in sql
        assert "user_summary" in sql
        assert "REFRESH MATERIALIZED VIEW" in rb_sql

    def test_refresh_matview_schema_qualified(self):
        """REFRESH MATERIALIZED VIEW uses schema-qualified name when schema is set."""
        ops = [
            {
                "type": "refresh_matview",
                "table": "user_summary",
                "schema": "app",
            },
        ]
        sql, rb_sql, changes = snapshot_diff_to_sql(ops, [], db_name=None)
        assert '"app".user_summary' in sql or "app.user_summary" in sql

    def test_refresh_matview_change_operation(self):
        """refresh_matview produces a Change with operation='refresh_matview'."""
        ops = [
            {
                "type": "refresh_matview",
                "table": "user_summary",
            },
        ]
        sql, rb_sql, changes = snapshot_diff_to_sql(ops, [], db_name=None)
        assert len(changes) == 1
        assert changes[0].operation == "refresh_matview"
        assert changes[0].table == "user_summary"




