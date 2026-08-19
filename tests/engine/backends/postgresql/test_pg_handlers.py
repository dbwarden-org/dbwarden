from __future__ import annotations

import pytest

from dbwarden.engine.backends.postgresql.handlers import TypeHandler
from dbwarden.engine.backends.postgresql.sql_build import _build_pg_meta_sql
from dbwarden.engine.core.models import ModelColumn, ModelTable
from dbwarden.engine.core.protocol import Op


class TestTypeHandler:
    def test_model_spec_collects_enum_columns(self):
        table = ModelTable(
            name="events",
            columns=[
                ModelColumn(
                    name="status",
                    type="ENUM",
                    nullable=False,
                    primary_key=False,
                    unique=False,
                    default=None,
                    foreign_key=None,
                    pg_meta={
                        "pg_type": {
                            "kind": "enum",
                            "type_name": "event_status",
                            "values": ["active", "inactive"],
                        }
                    },
                ),
            ],
        )
        handler = TypeHandler()
        spec = handler.model_spec_from_tables([table])
        assert spec == {"event_status": ["active", "inactive"]}

    def test_extract_collects_enum_columns_from_snapshot(self):
        snapshot = {
            "tables": {
                "events": {
                    "columns": {
                        "status": {
                            "type": "enum",
                            "pg_type": {
                                "kind": "enum",
                                "type_name": "event_status",
                                "values": ["active", "inactive"],
                            },
                        },
                    },
                },
            },
        }
        handler = TypeHandler()
        spec = handler.extract(snapshot)
        assert spec == {"event_status": ["active", "inactive"]}

    def test_diff_emits_create_type_for_new_enum(self):
        handler = TypeHandler()
        up, rb = handler.diff({}, {"event_status": ["active", "inactive"]})
        assert len(up) == 1
        assert up[0].object_type == "create_type"
        assert up[0].upgrade_attrs["enum_name"] == "event_status"
        assert up[0].upgrade_attrs["values"] == ["active", "inactive"]
        assert rb[0].object_type == "drop_type"

    def test_diff_emits_drop_type_for_removed_enum(self):
        handler = TypeHandler()
        up, rb = handler.diff({"event_status": ["active", "inactive"]}, {})
        assert len(up) == 1
        assert up[0].object_type == "drop_type"
        assert rb[0].object_type == "create_type"

    def test_emit_create_type_sql(self):
        handler = TypeHandler()
        op = Op(
            object_type="create_type",
            upgrade_attrs={"enum_name": "event_status", "values": ["active", "inactive"]},
            rollback_attrs={"enum_name": "event_status"},
        )
        stmts = handler.emit(op, db_name="primary")
        assert len(stmts) == 1
        assert 'CREATE TYPE "event_status" AS ENUM (' in stmts[0].upgrade_sql
        assert "DROP TYPE IF EXISTS" in stmts[0].rollback_sql


class TestPgMetaSqlBuild:
    def test_storage_not_changed_when_model_omits_it(self):
        """Regression: omitting pg_storage in the model must not force SET STORAGE EXTENDED."""
        stmts = _build_pg_meta_sql(
            table="events",
            column="amount",
            col_type="NUMERIC(10, 2)",
            snap_type="numeric",
            to_pg_column={},
            from_pg_column={"storage": "MAIN"},
            backend="postgresql",
        )
        assert not stmts

    def test_storage_changed_when_model_specifies_it(self):
        stmts = _build_pg_meta_sql(
            table="events",
            column="amount",
            col_type="NUMERIC(10, 2)",
            snap_type="numeric",
            to_pg_column={"pg_storage": "PLAIN"},
            from_pg_column={"storage": "MAIN"},
            backend="postgresql",
        )
        assert len(stmts) == 1
        assert "SET STORAGE PLAIN" in stmts[0].upgrade_sql

    def test_identity_defaults_not_emitted_when_model_omits_them(self):
        """Regression: identity params defaults from the snapshot should not produce noise ALTERs."""
        stmts = _build_pg_meta_sql(
            table="orders",
            column="id",
            col_type="INTEGER",
            snap_type="integer",
            to_pg_column={"pg_identity": "always"},
            from_pg_column={
                "identity": "always",
                "identity_start": 1,
                "identity_increment": 1,
                "identity_min": 1,
                "identity_max": 2147483647,
            },
            backend="postgresql",
        )
        assert not stmts
