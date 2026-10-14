"""Tests for Phase 1 operational safety primitives and Phase 2 type system/index expansion."""

import pytest

from dbwarden.engine.file_parser import DBWARDEN_AUTOCOMMIT_MARKER, _extract_section_statements
from dbwarden.engine.snapshot import StatementOrder
from dbwarden.databases.pgsql import PGTableMeta


class TestAutocommitMarker:
    def test_autocommit_marker_parsed_in_upgrade(self):
        content = """-- upgrade

-- @dbwarden:autocommit
CREATE INDEX CONCURRENTLY ix ON t (col);

CREATE TABLE foo (id int);

-- rollback

DROP TABLE foo;
"""
        statements = _extract_section_statements(content, "-- upgrade")
        assert len(statements) == 2
        assert DBWARDEN_AUTOCOMMIT_MARKER in statements[0]
        assert "CREATE INDEX CONCURRENTLY" in statements[0]
        assert DBWARDEN_AUTOCOMMIT_MARKER not in statements[1]
        assert "CREATE TABLE" in statements[1]

    def test_autocommit_marker_isolated_statement(self):
        content = """-- upgrade

-- @dbwarden:autocommit
ALTER TABLE t VALIDATE CONSTRAINT ck;

-- rollback
"""
        statements = _extract_section_statements(content, "-- upgrade")
        assert len(statements) == 1
        assert DBWARDEN_AUTOCOMMIT_MARKER in statements[0]
        assert "VALIDATE CONSTRAINT" in statements[0]

    def test_no_autocommit_marker_normal(self):
        content = """-- upgrade

CREATE TABLE foo (id int);

-- rollback
"""
        statements = _extract_section_statements(content, "-- upgrade")
        assert len(statements) == 1
        assert DBWARDEN_AUTOCOMMIT_MARKER not in statements[0]
        assert "CREATE TABLE" in statements[0]

    def test_multiple_autocommit_markers(self):
        content = """-- upgrade

-- @dbwarden:autocommit
VALIDATE CONSTRAINT ck1;

-- @dbwarden:autocommit
VALIDATE CONSTRAINT ck2;

-- rollback
"""
        statements = _extract_section_statements(content, "-- upgrade")
        assert len(statements) == 2
        assert all(DBWARDEN_AUTOCOMMIT_MARKER in s for s in statements)
        assert "ck1" in statements[0]
        assert "ck2" in statements[1]


class TestNotValidValidate:
    def test_fk_not_valid_emits_not_valid_and_validate(self, monkeypatch):
        from dbwarden.engine.file_parser import DBWARDEN_AUTOCOMMIT_MARKER
        from dbwarden.engine.backends.postgresql.handlers import ConstraintHandler
        from dbwarden.engine.core.protocol import Op

        monkeypatch.setattr("dbwarden.engine.snapshot._get_backend", lambda db_name=None: "postgresql")

        op = Op(
            object_type="add_foreign_key",
            upgrade_attrs={
                "table": "orders",
                "columns": ["user_id"],
                "referenced_table": "users",
                "referenced_columns": ["id"],
                "on_delete": "NO ACTION",
                "on_update": "NO ACTION",
                "deferrable": False,
                "validated": False,
            },
        )
        stmts = ConstraintHandler().emit(op, db_name="primary")
        assert len(stmts) == 2
        assert "NOT VALID" in stmts[0].upgrade_sql
        assert DBWARDEN_AUTOCOMMIT_MARKER in stmts[1].upgrade_sql
        assert "VALIDATE CONSTRAINT" in stmts[1].upgrade_sql
        assert stmts[0].order == StatementOrder.ALTER_FOREIGN_KEY
        assert stmts[1].order == StatementOrder.VALIDATE_CONSTRAINT

    def test_fk_validated_does_not_emit_not_valid(self, monkeypatch):
        from dbwarden.engine.backends.postgresql.handlers import ConstraintHandler
        from dbwarden.engine.core.protocol import Op

        monkeypatch.setattr("dbwarden.engine.snapshot._get_backend", lambda db_name=None: "postgresql")

        op = Op(
            object_type="add_foreign_key",
            upgrade_attrs={
                "table": "orders",
                "columns": ["user_id"],
                "referenced_table": "users",
                "referenced_columns": ["id"],
                "on_delete": "CASCADE",
                "on_update": "NO ACTION",
                "deferrable": False,
                "validated": True,
            },
        )
        stmts = ConstraintHandler().emit(op, db_name="primary")
        assert len(stmts) == 1
        assert "NOT VALID" not in stmts[0].upgrade_sql
        assert "CASCADE" in stmts[0].upgrade_sql

    def test_fk_default_validated_true(self, monkeypatch):
        from dbwarden.engine.backends.postgresql.handlers import ConstraintHandler
        from dbwarden.engine.core.protocol import Op

        monkeypatch.setattr("dbwarden.engine.snapshot._get_backend", lambda db_name=None: "postgresql")

        op = Op(
            object_type="add_foreign_key",
            upgrade_attrs={
                "table": "orders",
                "columns": ["user_id"],
                "referenced_table": "users",
                "referenced_columns": ["id"],
            },
        )
        stmts = ConstraintHandler().emit(op, db_name="primary")
        assert len(stmts) == 1
        assert "NOT VALID" not in stmts[0].upgrade_sql

    def test_fk_drop_rollback_preserves_not_valid(self, monkeypatch):
        from dbwarden.engine.backends.postgresql.handlers import ConstraintHandler
        from dbwarden.engine.core.protocol import Op

        monkeypatch.setattr("dbwarden.engine.snapshot._get_backend", lambda db_name=None: "postgresql")

        op = Op(
            object_type="drop_foreign_key",
            upgrade_attrs={
                "table": "orders",
                "columns": ["user_id"],
                "referenced_table": "users",
                "referenced_columns": ["id"],
                "validated": False,
            },
        )
        stmts = ConstraintHandler().emit(op, db_name="primary")
        assert len(stmts) == 1
        assert stmts[0].upgrade_sql.startswith("ALTER TABLE orders DROP CONSTRAINT")
        assert "NOT VALID" in stmts[0].rollback_sql


class TestLockTimeout:
    def test_lock_timeout_config_field(self):
        """pg_migration_lock_timeout should be a valid config field."""
        from dbwarden.config_schema import DatabaseEntry
        import cattrs

        entry = DatabaseEntry(
            database_name="test",
            database_type="postgresql",
            database_url_sync="postgresql://localhost/test",
            pg_migration_lock_timeout=5000,
        )
        assert entry.pg_migration_lock_timeout == 5000

    def test_lock_timeout_default_none(self):
        from dbwarden.config_schema import DatabaseEntry

        entry = DatabaseEntry(
            database_name="test",
            database_type="postgresql",
            database_url_sync="postgresql://localhost/test",
        )
        assert entry.pg_migration_lock_timeout is None


class TestStatementOrder:
    def test_validate_constraint_order(self):
        """VALIDATE_CONSTRAINT should be after ALTER_TABLE_CONSTRAINT."""
        assert StatementOrder.VALIDATE_CONSTRAINT == 19
        assert StatementOrder.ALTER_TABLE_CONSTRAINT == 18
        assert StatementOrder.VALIDATE_CONSTRAINT > StatementOrder.ALTER_TABLE_CONSTRAINT


class TestPgIndexOps:
    def test_postgresql_ops_in_index_info(self):
        """IndexInfo dataclass should have postgresql_ops field."""
        from dbwarden.engine.model_discovery import IndexInfo
        idx = IndexInfo(columns=["email"])
        assert hasattr(idx, "postgresql_ops")
        assert idx.postgresql_ops is None
        idx2 = IndexInfo(columns=["email"], postgresql_ops={"email": "varchar_pattern_ops"})
        assert idx2.postgresql_ops == {"email": "varchar_pattern_ops"}

    def test_postgresql_ops_in_index_sig(self):
        """postgresql_ops should be part of the index signature."""
        from dbwarden.engine.snapshot import _index_sig
        from dbwarden.engine.model_discovery import IndexInfo
        idx1 = IndexInfo(columns=["email"], postgresql_ops={"email": "varchar_pattern_ops"})
        idx2 = IndexInfo(columns=["email"], postgresql_ops={"email": "text_pattern_ops"})
        idx3 = IndexInfo(columns=["email"], postgresql_ops={"email": "varchar_pattern_ops"})
        assert _index_sig(idx1) != _index_sig(idx2)
        assert _index_sig(idx1) == _index_sig(idx3)

    def test_postgresql_ops_in_index_op_from_info(self):
        """_index_op_from_info should include postgresql_ops."""
        from dbwarden.engine.snapshot import _index_op_from_info
        from dbwarden.engine.model_discovery import IndexInfo
        idx = IndexInfo(columns=["email"], postgresql_ops={"email": "varchar_pattern_ops"})
        op = _index_op_from_info(idx, "users")
        assert op["postgresql_ops"] == {"email": "varchar_pattern_ops"}

    def test_postgresql_ops_in_index_info_to_dict(self):
        """IndexInfo.to_dict should include postgresql_ops."""
        from dbwarden.engine.model_discovery import IndexInfo
        idx = IndexInfo(columns=["email"], postgresql_ops={"email": "varchar_pattern_ops"})
        d = idx.to_dict()
        assert d["postgresql_ops"] == {"email": "varchar_pattern_ops"}

    def test_postgresql_ops_in_index_info_from_dict(self):
        """IndexInfo.from_dict should restore postgresql_ops."""
        from dbwarden.engine.model_discovery import IndexInfo
        d = {"columns": ["email"], "name": "ix_users_email",
             "postgresql_ops": {"email": "varchar_pattern_ops"}}
        idx = IndexInfo.from_dict(d)
        assert idx.postgresql_ops == {"email": "varchar_pattern_ops"}
        assert idx.name == "ix_users_email"

    def test_postgresql_ops_in_model_discovery(self):
        """Model discovery should pass postgresql_ops from PgIndexSpec to IndexInfo."""
        from dbwarden.databases.pgsql import PgIndexSpec
        spec = PgIndexSpec(
            name="ix_users_email",
            columns=["email"],
            unique=True,
            postgresql_ops={"email": "varchar_pattern_ops"},
        )
        d = spec.to_dict()
        assert d["postgresql_ops"] == {"email": "varchar_pattern_ops"}

    def test_postgresql_ops_sql_emission_add_index(self, monkeypatch):
        """_build_index_sql should emit operator classes in CREATE INDEX."""
        monkeypatch.setattr("dbwarden.engine.snapshot._get_backend", lambda db_name=None: "postgresql")
        from dbwarden.engine.snapshot import _build_index_sql

        op = {
            "type": "add_index",
            "table": "users",
            "columns": ["email"],
            "using": "btree",
            "postgresql_ops": {"email": "varchar_pattern_ops"},
        }
        stmts = _build_index_sql(op, "postgresql")
        upgrade = stmts[0].upgrade_sql
        assert "varchar_pattern_ops" in upgrade
        assert "email varchar_pattern_ops" in upgrade

    def test_postgresql_ops_sql_emission_drop_index_rollback(self, monkeypatch):
        """_build_index_sql drop_index rollback should include operator classes."""
        monkeypatch.setattr("dbwarden.engine.snapshot._get_backend", lambda db_name=None: "postgresql")
        from dbwarden.engine.snapshot import _build_index_sql

        op = {
            "type": "drop_index",
            "table": "users",
            "index_name": "ix_users_email",
            "columns": ["email"],
            "using": "btree",
            "postgresql_ops": {"email": "varchar_pattern_ops"},
        }
        stmts = _build_index_sql(op, "postgresql")
        rollback = stmts[0].rollback_sql
        assert "varchar_pattern_ops" in rollback
        assert "email varchar_pattern_ops" in rollback


class TestDomainSnapshot:
    def test_extract_full_schema_snapshot_returns_domains(self):
        """Snapshot dict should have a domains key."""
        from dbwarden.engine.snapshot import extract_full_schema_snapshot

        snapshot = extract_full_schema_snapshot(
            sqlalchemy_url="sqlite://",
            database_type="postgresql",
        )
        assert "domains" in snapshot
        assert isinstance(snapshot["domains"], dict)


class TestPgCreateTableInline:
    def test_unlogged_in_create_table(self, monkeypatch):
        monkeypatch.setattr("dbwarden.engine.model_discovery.type_mapping._get_backend_name", lambda db_name=None: "postgresql")
        from dbwarden.engine.model_discovery import generate_create_table_sql, ModelTable
        table = ModelTable(
            name="test_table",
            columns=[],
            pg_table={"pg_unlogged": True},
        )
        sql = generate_create_table_sql(table)
        assert "CREATE UNLOGGED TABLE IF NOT EXISTS test_table" in sql

    def test_logged_default_no_unlogged(self, monkeypatch):
        monkeypatch.setattr("dbwarden.engine.model_discovery.type_mapping._get_backend_name", lambda db_name=None: "postgresql")
        from dbwarden.engine.model_discovery import generate_create_table_sql, ModelTable
        table = ModelTable(
            name="test_table",
            columns=[],
            pg_table={},
        )
        sql = generate_create_table_sql(table)
        assert sql.startswith("CREATE TABLE IF NOT EXISTS test_table")

    def test_no_pg_table_no_unlogged(self, monkeypatch):
        monkeypatch.setattr("dbwarden.engine.model_discovery.type_mapping._get_backend_name", lambda db_name=None: "postgresql")
        from dbwarden.engine.model_discovery import generate_create_table_sql, ModelTable
        table = ModelTable(
            name="test_table",
            columns=[],
        )
        sql = generate_create_table_sql(table)
        assert sql.startswith("CREATE TABLE IF NOT EXISTS test_table")

    def test_inherits_in_create_table(self, monkeypatch):
        monkeypatch.setattr("dbwarden.engine.model_discovery.type_mapping._get_backend_name", lambda db_name=None: "postgresql")
        from dbwarden.engine.model_discovery import generate_create_table_sql, ModelTable
        table = ModelTable(
            name="child_table",
            columns=[],
            pg_table={"pg_inherits": "parent_table"},
        )
        sql = generate_create_table_sql(table)
        assert "INHERITS (parent_table)" in sql

    def test_tablespace_in_create_table(self, monkeypatch):
        monkeypatch.setattr("dbwarden.engine.model_discovery.type_mapping._get_backend_name", lambda db_name=None: "postgresql")
        from dbwarden.engine.model_discovery import generate_create_table_sql, ModelTable
        table = ModelTable(
            name="test_table",
            columns=[],
            pg_table={"pg_tablespace": "fast_ssd"},
        )
        sql = generate_create_table_sql(table)
        assert "TABLESPACE fast_ssd" in sql

    def test_all_inline_attrs_together(self, monkeypatch):
        monkeypatch.setattr("dbwarden.engine.model_discovery.type_mapping._get_backend_name", lambda db_name=None: "postgresql")
        from dbwarden.engine.model_discovery import generate_create_table_sql, ModelTable
        table = ModelTable(
            name="test_table",
            columns=[],
            pg_table={
                "pg_unlogged": True,
                "pg_inherits": "parent_table",
                "pg_tablespace": "fast_ssd",
                "pg_partition": {"strategy": "RANGE", "columns": ["created_at"]},
            },
        )
        sql = generate_create_table_sql(table)
        assert sql.startswith("CREATE UNLOGGED TABLE IF NOT EXISTS test_table")
        assert "PARTITION BY RANGE (created_at)" in sql
        assert "INHERITS (parent_table)" in sql
        assert "TABLESPACE fast_ssd" in sql


class TestMySQLForeignKeyRollback:
    def test_add_foreign_key_rollback_uses_drop_foreign_key(self, monkeypatch):
        # MySQL 5.7 rejects DROP CONSTRAINT; a foreign key is dropped with
        # DROP FOREIGN KEY, including when dropping is the rollback of an add.
        from dbwarden.engine.backends.postgresql.handlers import ConstraintHandler
        from dbwarden.engine.core.protocol import Op

        monkeypatch.setattr(
            "dbwarden.engine.snapshot._get_backend", lambda db_name=None: "mysql"
        )
        op = Op(
            object_type="add_foreign_key",
            upgrade_attrs={
                "table": "zonas_tokko",
                "columns": ["id_cym"],
                "referenced_table": "zonas",
                "referenced_columns": ["id"],
            },
        )
        stmts = ConstraintHandler().emit(op, db_name="primary")
        assert len(stmts) == 1
        assert stmts[0].rollback_sql == (
            "ALTER TABLE zonas_tokko DROP FOREIGN KEY fk_zonas_tokko_id_cym;"
        )
