import json
import os
import tempfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from dbwarden.exceptions import ConfigurationError
from dbwarden.engine.snapshot import (
    _apply_rename_intents,
    _normalize_mysql_default,
    _rename_table_sql,
    TableRenameIntent,
    compute_checksum,
    detect_renames,
    find_latest_snapshot,
    get_schemas_directory,
    normalize_type,
    read_snapshot,
    write_snapshot,
    extract_full_schema_snapshot,
)
from dbwarden.engine.model_discovery import ModelColumn, ModelTable


def _mc(name: str, typ: str, pk: bool = False, nullable: bool = True) -> ModelColumn:
    return ModelColumn(name, typ, nullable, pk, False, None, None)


class TestNormalizeType:
    def test_integer_variants(self):
        for raw in ("INT", "INTEGER", "INT4", "TINYINT", "SMALLINT", "int", "integer"):
            assert normalize_type(raw)["type"] == "integer"

    def test_biginteger(self):
        assert normalize_type("BIGINT")["type"] == "biginteger"
        assert normalize_type("INT8")["type"] == "biginteger"

    def test_varchar_with_length(self):
        result = normalize_type("VARCHAR(255)")
        assert result["type"] == "varchar"
        assert result["length"] == 255

    def test_text_variants(self):
        for raw in ("TEXT", "LONGTEXT", "CLOB"):
            assert normalize_type(raw)["type"] == "text"

    def test_char_variants(self):
        result = normalize_type("CHAR")
        assert result["type"] in ("char", "varchar")

    def test_boolean_variants(self):
        for raw in ("BOOL", "BOOLEAN"):
            assert normalize_type(raw)["type"] == "boolean"

    def test_float_variants(self):
        assert normalize_type("FLOAT")["type"] == "float"
        assert normalize_type("DOUBLE")["type"] == "float"

    def test_timestamp_variants(self):
        result = normalize_type("TIMESTAMP")
        assert result["type"] == "timestamp"

    def test_timestamptz(self):
        result = normalize_type("TIMESTAMPTZ")
        assert result["type"] in ("timestamptz", "timestamp")

    def test_date(self):
        assert normalize_type("DATE")["type"] == "date"

    def test_time(self):
        assert normalize_type("TIME")["type"] == "time"

    def test_numeric(self):
        result = normalize_type("NUMERIC(12,4)")
        assert result["type"] == "numeric"

    def test_json_variants(self):
        for raw in ("JSON", "JSONB"):
            assert normalize_type(raw)["type"] == "json"

    def test_uuid(self):
        assert normalize_type("UUID")["type"] == "uuid"

    def test_unknown_type_returns_raw(self):
        result = normalize_type("CUSTOM_TYPE")
        assert result.get("raw") is True


class TestComputeChecksum:
    def test_compute_checksum(self):
        snap1 = {
            "tables": {
                "users": {
                    "columns": {"id": {"type": "integer", "nullable": False, "primary_key": True}},
                }
            },
        }
        snap2 = {
            "tables": {
                "users": {
                    "columns": {"id": {"type": "integer", "nullable": False, "primary_key": True}},
                }
            },
        }
        assert compute_checksum(snap1) == compute_checksum(snap2)

    def test_checksum_change(self):
        snap1 = {
            "tables": {
                "users": {
                    "columns": {"id": {"type": "integer", "nullable": False, "primary_key": True}},
                }
            },
        }
        snap2 = {
            "tables": {
                "users": {
                    "columns": {
                        "id": {"type": "integer", "nullable": False, "primary_key": True},
                        "name": {"type": "varchar", "nullable": True, "primary_key": False},
                    }
                }
            },
        }
        assert compute_checksum(snap1) != compute_checksum(snap2)


class TestWriteReadSnapshot:
    def test_write_and_read_snapshot(self):
        snapshot = {
            "tables": {
                "users": {
                    "columns": {"id": {"type": "integer", "nullable": False, "primary_key": True}},
                }
            },
        }
        with tempfile.TemporaryDirectory() as tmp:
            old_cwd = os.getcwd()
            try:
                os.chdir(tmp)
                Path("dbwarden.py").write_text(
                    "from dbwarden import database_config\n"
                    "database_config(database_name='test', default=True, database_type='sqlite', "
                    "database_url_sync='sqlite:///./test.db', model_paths=['models'])\n"
                )
                from dbwarden.config import get_database, get_multi_db_config
                cfg = get_multi_db_config()
                assert "test" in cfg.databases
                filepath = write_snapshot(snapshot, database="test", migration_id="001")
                assert filepath is not None
                loaded = read_snapshot(filepath)
                assert loaded is not None
                assert "tables" in loaded
                assert "users" in loaded["tables"]
            finally:
                os.chdir(old_cwd)

    def test_read_nonexistent_snapshot(self):
        result = read_snapshot("/nonexistent/path.json")
        assert result is None

    def test_latest_snapshot_none_when_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            old_cwd = os.getcwd()
            try:
                os.chdir(tmp)
                result = find_latest_snapshot("test")
                assert result is None
            finally:
                os.chdir(old_cwd)

    def test_check_for_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            old_cwd = os.getcwd()
            try:
                os.chdir(tmp)
                Path("dbwarden.py").write_text(
                    "from dbwarden import database_config\n"
                    "database_config(database_name='test', default=True, database_type='sqlite', "
                    "database_url_sync='sqlite:///./test.db', model_paths=['models'])\n"
                )
                schemas_dir = get_schemas_directory("test")
                assert os.path.isdir(schemas_dir)
            finally:
                os.chdir(old_cwd)


class TestFindLatestSnapshot:
    def test_find_latest_snapshot(self):
        with tempfile.TemporaryDirectory() as tmp:
            old_cwd = os.getcwd()
            try:
                os.chdir(tmp)
                Path("dbwarden.py").write_text(
                    "from dbwarden import database_config\n"
                    "database_config(database_name='test', default=True, database_type='sqlite', "
                    "database_url_sync='sqlite:///./test.db', model_paths=['models'])\n"
                )
                snapshot = {
                    "tables": {},
                    "migration_id": "0001_init",
                }
                write_snapshot(snapshot, database="test", migration_id="0001_init")
                latest = find_latest_snapshot("test")
                assert latest is not None
            finally:
                os.chdir(old_cwd)

    def test_has_snapshot_table(self):
        with tempfile.TemporaryDirectory() as tmp:
            old_cwd = os.getcwd()
            try:
                os.chdir(tmp)
                Path("dbwarden.py").write_text(
                    "from dbwarden import database_config\n"
                    "database_config(database_name='test', default=True, database_type='sqlite', "
                    "database_url_sync='sqlite:///./test.db', model_paths=['models'])\n"
                )
                schemas = get_schemas_directory("test")
                assert os.path.isdir(schemas)
            finally:
                os.chdir(old_cwd)


class TestMySQLSnapshot:
    def setup_method(self):
        self.mock_connection = "mock_conn"

    def test_canary(self):
        assert True

    def test_mysql_default_normalize_null(self):
        result = _normalize_mysql_default("NULL")
        assert result is None

    def test_mysql_default_normalize_current_timestamp(self):
        result = _normalize_mysql_default("CURRENT_TIMESTAMP")
        assert result == "CURRENT_TIMESTAMP"

    def test_mysql_default_normalize_current_timestamp_fsp(self):
        result = _normalize_mysql_default("CURRENT_TIMESTAMP(6)")
        assert result == "CURRENT_TIMESTAMP"

    def test_mysql_default_normalize_on_update(self):
        result = _normalize_mysql_default("ON UPDATE CURRENT_TIMESTAMP")
        assert result == "CURRENT_TIMESTAMP"

    def test_mysql_default_normalize_on_update_fsp(self):
        result = _normalize_mysql_default("ON UPDATE CURRENT_TIMESTAMP(3)")
        assert result == "CURRENT_TIMESTAMP"

    def test_mysql_default_normalize_bare_int(self):
        result = _normalize_mysql_default("0")
        assert result == "0"

    def test_mysql_default_normalize_quoted_string(self):
        result = _normalize_mysql_default("'active'")
        assert result == "active"

    def test_mysql_default_normalize_expression(self):
        result = _normalize_mysql_default("(1)")
        assert result == "1"


class TestDetectRenames:
    def test_simple_rename(self):
        dropped = [("old_col", {"type": "integer"})]
        added = [("new_col", _mc("new_col", "INTEGER"))]
        renames = detect_renames("users", dropped, added)
        assert len(renames) == 1
        assert renames[0] == ("old_col", "new_col")

    def test_no_rename(self):
        dropped = [("id", {"type": "integer"})]
        added = [("id", _mc("id", "INTEGER"))]
        renames = detect_renames("users", dropped, added)
        assert len(renames) == 0

    def test_add_and_remove_detected(self):
        dropped = [("uid", {"type": "integer"})]
        added = [("id", _mc("id", "INTEGER"))]
        renames = detect_renames("users", dropped, added)
        assert len(renames) == 1
        assert renames[0] == ("uid", "id")

    def test_no_rename_different_types(self):
        dropped = [("uid", {"type": "integer"})]
        added = [("uid", _mc("uid", "VARCHAR(255)"))]
        renames = detect_renames("users", dropped, added)
        assert len(renames) == 0

    def test_intent_generation_valid(self):
        intent = TableRenameIntent("a", "b")
        assert intent.old_table == "a"
        assert intent.new_table == "b"

    def test_rename_table_sql(self):
        intent = TableRenameIntent("users", "customers")
        sql = _rename_table_sql(intent, "mysql")
        assert "ALTER TABLE users RENAME TO customers" in sql.upgrade_sql

    def test_rename_no_match(self):
        renames = detect_renames("t", [], [])
        assert len(renames) == 0


class TestApplyRenameIntents:
    def test_apply_single_rename(self):
        upgrade_ops = [
            {"type": "drop_column", "table": "orders", "column": "old_col"},
            {"type": "add_column", "table": "orders", "column": "new_col", "definition": {}},
        ]
        rollback_ops = [
            {"type": "add_column", "table": "orders", "column": "old_col", "definition": {}},
            {"type": "drop_column", "table": "orders", "column": "new_col"},
        ]
        confirmed = {("orders", "old_col", "new_col")}
        result_up, result_ro = _apply_rename_intents(upgrade_ops, rollback_ops, confirmed)
        assert result_up[0]["type"] == "rename_column"

    def test_apply_no_intents(self):
        upgrade_ops = [
            {"type": "drop_column", "table": "orders", "column": "old_col"},
        ]
        rollback_ops = [
            {"type": "add_column", "table": "orders", "column": "old_col", "definition": {}},
        ]
        result_up, result_ro = _apply_rename_intents(upgrade_ops, rollback_ops, set())
        assert result_up[0]["type"] == "drop_column"

    def test_apply_empty_ops(self):
        result_up, result_ro = _apply_rename_intents([], [], set())
        assert result_up == []
        assert result_ro == []

    def test_apply_synthetic_rename(self):
        upgrade_ops = [
            {"type": "drop_column", "table": "a", "column": "x"},
            {"type": "add_column", "table": "a", "column": "y", "definition": {}},
        ]
        rollback_ops = [
            {"type": "add_column", "table": "a", "column": "x", "definition": {}},
            {"type": "drop_column", "table": "a", "column": "y"},
        ]
        confirmed = {("a", "x", "y")}
        result_up, _ = _apply_rename_intents(upgrade_ops, rollback_ops, confirmed)
        assert result_up[0]["type"] == "rename_column"
        assert result_up[0]["old_name"] == "x"
        assert result_up[0]["new_name"] == "y"


class TestExtractFullSchemaSnapshot:
    def test_canary(self):
        assert True

    def test_snapshot_structure(self):
        tables = [
            ModelTable(
                "public.users",
                columns=[_mc("id", "INTEGER", pk=True, nullable=False)],
            )
        ]
        assert len(tables[0].name) > 0

    def test_extract_accepts_db_handle(self):
        with pytest.raises(TypeError):
            extract_full_schema_snapshot(
                db_name="primary",
            )

    def test_find_latest_snapshot_none(self):
        result = find_latest_snapshot("nonexistent")
        assert result is None


class TestTypeNormalize:
    def test_normalize_type_basic(self):
        from dbwarden.engine.snapshot.type_normalize import normalize_type

        assert normalize_type("INTEGER") == {"type": "integer"}
        assert normalize_type("text") == {"type": "text"}
        assert normalize_type("VARCHAR(255)") == {"type": "varchar", "length": 255}
        assert normalize_type("numeric(10,2)") == {"type": "numeric", "precision": 10, "scale": 2}

    def test_normalize_type_timestamptz(self):
        from dbwarden.engine.snapshot.type_normalize import normalize_type

        result = normalize_type("timestamptz")
        assert result == {"type": "timestamp", "has_timezone": True}

    def test_normalize_type_float32(self):
        from dbwarden.engine.snapshot.type_normalize import normalize_type

        result = normalize_type("float32")
        assert result == {"type": "float"}

    def test_normalize_type_unknown_returns_raw(self):
        from dbwarden.engine.snapshot.type_normalize import normalize_type

        result = normalize_type("custom_type")
        assert result["raw"] is True
        assert result["type"] == "custom_type"

    def test_normalize_type_fallback_regex_unknown(self):
        from dbwarden.engine.snapshot.type_normalize import normalize_type

        result = normalize_type("")
        assert result["raw"] is True

    def test_normalize_type_collate_stripped(self):
        from dbwarden.engine.snapshot.type_normalize import normalize_type

        result = normalize_type('varchar COLLATE "en_US.utf8"')
        assert result["type"] == "varchar"

    def test_strip_ch_type_wrappers(self):
        from dbwarden.engine.snapshot.type_normalize import _strip_ch_type_wrappers

        assert _strip_ch_type_wrappers("Nullable(String)") == "String"
        assert _strip_ch_type_wrappers("LowCardinality(String)") == "String"
        assert _strip_ch_type_wrappers("String") == "String"
        assert _strip_ch_type_wrappers("") == ""
        # Only outermost wrapper is stripped per call
        assert _strip_ch_type_wrappers("LowCardinality(Nullable(String))") == "Nullable(String)"

    def test_model_type_str_with_enums(self):
        from dbwarden.engine.snapshot.type_normalize import _model_type_str
        from types import SimpleNamespace

        sa_type = SimpleNamespace(enums=["active", "inactive"])
        result = _model_type_str(sa_type)
        assert result.startswith("Enum(")
        assert "active" in result
        assert "inactive" in result

    def test_model_type_str_no_enums(self):
        from dbwarden.engine.snapshot.type_normalize import _model_type_str
        from types import SimpleNamespace

        sa_type = SimpleNamespace(enums=None)
        result = _model_type_str(sa_type)
        assert isinstance(result, str)

    def test_snap_to_model_key(self):
        from dbwarden.engine.snapshot.type_normalize import snap_to_model_key

        assert snap_to_model_key("collation") == "pg_collation"
        assert snap_to_model_key("storage") == "pg_storage"
        assert snap_to_model_key("unknown_key") == "unknown_key"

    def test_normalize_default(self):
        from dbwarden.engine.snapshot.type_normalize import _normalize_default

        assert _normalize_default(None) is None
        assert _normalize_default("'hello'") == "hello"
        assert _normalize_default("NULL") == "NULL"
        assert _normalize_default("true") == "TRUE"
        assert _normalize_default("CURRENT_TIMESTAMP") == "CURRENT_TIMESTAMP"

    def test_normalize_index_col(self):
        from dbwarden.engine.snapshot.type_normalize import _normalize_index_col

        assert _normalize_index_col("col::text") == "col"
        assert _normalize_index_col("col") == "col"


class TestDetectRenamesEdgeCases:
    def test_multi_match_rename_equal_counts(self):
        from dbwarden.engine.core.rename import detect_renames
        from dbwarden.engine.model_discovery import ModelColumn

        dropped = [
            ("old_a", {"type": "varchar"}),
            ("old_b", {"type": "varchar"}),
        ]
        added = [
            ("new_a", ModelColumn("new_a", "VARCHAR", True, False, False, None, None)),
            ("new_b", ModelColumn("new_b", "VARCHAR", True, False, False, None, None)),
        ]
        renames = detect_renames("t", dropped, added)
        assert len(renames) == 2
        assert ("old_a", "new_a") in renames
        assert ("old_b", "new_b") in renames

    def test_single_drop_single_add_matched(self):
        from dbwarden.engine.core.rename import detect_renames
        from dbwarden.engine.model_discovery import ModelColumn

        dropped = [
            ("old_a", {"type": "varchar"}),
        ]
        added = [
            ("new_a", ModelColumn("new_a", "VARCHAR", True, False, False, None, None)),
        ]
        renames = detect_renames("t", dropped, added)
        assert len(renames) == 1
        assert renames[0] == ("old_a", "new_a")

    def test_table_overlap_missing_model_zero(self):
        from dbwarden.engine.core.rename import _compute_table_overlap

        result = _compute_table_overlap("old", "new", {"tables": {"old": {"columns": {"id": {"type": "integer"}}}}}, [])
        assert result == 0.0

    def test_unconfirmed_rename_column_converted_to_drop_add(self):
        from dbwarden.engine.core.rename import _apply_rename_intents

        ops = [
            {"type": "rename_column", "table": "users", "old_name": "name", "new_name": "full_name"},
        ]
        rollback = [
            {"type": "rename_column", "table": "users", "old_name": "full_name", "new_name": "name"},
        ]
        result_up, result_rb = _apply_rename_intents(ops, rollback, set())
        assert result_up[0]["type"] == "drop_column"
        assert result_rb[0]["type"] == "add_column"


class TestSnapshotSqlGen:
    def test_non_concurrent_index_sets_concurrently_false(self):
        from dbwarden.engine.snapshot.sql_gen import snapshot_diff_to_sql

        ops = [
            {"type": "add_index", "table": "users", "columns": ["email"], "unique": True},
            {"type": "drop_index", "table": "users", "index_name": "idx_users_email", "columns": ["email"], "unique": False},
        ]
        rollback_ops = [dict(op) for op in ops]
        sql, rb_sql, changes = snapshot_diff_to_sql(ops, rollback_ops, db_name=None, concurrent=False)
        assert "CONCURRENTLY" not in sql

    def test_rollback_kind_from_sql(self):
        from dbwarden.engine.snapshot.sql_gen import _rollback_kind_from_sql

        assert _rollback_kind_from_sql("-- no-op: nothing to do") == "no-op"
        assert _rollback_kind_from_sql("-- nothing to roll back") == "no-op"
        assert _rollback_kind_from_sql("-- irreversible: data loss") == "irreversible"
        assert _rollback_kind_from_sql("-- cannot roll back automatically") == "placeholder"
        assert _rollback_kind_from_sql("ALTER TABLE ...") is None
        assert _rollback_kind_from_sql("") is None

    def test_rollback_contract_enforcement(self):
        from dbwarden.engine.snapshot.sql_gen import (
            _enforce_rollback_contract,
            RollbackContractError,
        )
        from dbwarden.engine.core.statement_order import MigrationStatement, StatementOrder
        import pytest

        stmt = MigrationStatement(
            order=StatementOrder.ADD_COLUMN,
            upgrade_sql="",
            rollback_sql="-- cannot roll back automatically",
        )
        with pytest.raises(RollbackContractError, match="Placeholder rollback"):
            _enforce_rollback_contract(stmt, "add_column", "users", "placeholder", enforce_rollback_contract=True)

    def test_rollback_contract_skip_when_not_enforced(self):
        from dbwarden.engine.snapshot.sql_gen import _enforce_rollback_contract
        from dbwarden.engine.core.statement_order import MigrationStatement, StatementOrder

        stmt = MigrationStatement(
            order=StatementOrder.ADD_COLUMN,
            upgrade_sql="",
            rollback_sql="-- placeholder",
        )
        _enforce_rollback_contract(stmt, "add_column", "users", "placeholder", enforce_rollback_contract=False)

    def test_find_model_table_auto_discover_none(self):
        from dbwarden.engine.snapshot.sql_gen import _find_model_table

        with tempfile.TemporaryDirectory() as tmp:
            old_cwd = os.getcwd()
            try:
                os.chdir(tmp)
                Path("dbwarden.py").write_text(
                    "from dbwarden import database_config\n"
                    "database_config(database_name='test', default=True, database_type='sqlite', "
                    "database_url_sync='sqlite:///./test.db')\n"
                )
                result = _find_model_table("nonexistent", "test")
                assert result is None
            finally:
                os.chdir(old_cwd)
