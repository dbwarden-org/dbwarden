import pytest
from dbwarden.engine.model_discovery import ModelColumn, ModelTable
from dbwarden.engine.safety import analyze_schema
from dbwarden.commands.check import check_cmd


def test_analyze_schema_detect_add_column():
    model_tables = [
        ModelTable(
            name="users",
            columns=[ModelColumn("email", "VARCHAR(255)", True, False, False, None, None)],
        )
    ]

    snapshot = {
        "users": {
            "columns": {},
            "object_type": "table",
        }
    }

    issues = analyze_schema(model_tables, snapshot)

    assert len(issues) == 1
    assert issues[0].change_type == "add_column"


def test_analyze_schema_detect_column_type_change():
    model_tables = [
        ModelTable(
            name="users",
            columns=[ModelColumn("email", "TEXT", True, False, False, None, None)],
        )
    ]

    snapshot = {
        "users": {
            "columns": {"email": {"type": "VARCHAR(255)", "nullable": True, "default": None}},
            "object_type": "table",
        }
    }

    issues = analyze_schema(model_tables, snapshot)

    assert len(issues) == 1
    assert issues[0].change_type == "change_column_type"


def test_analyze_schema_detect_add_comment():
    model_tables = [
        ModelTable(
            name="users",
            columns=[ModelColumn("email", "VARCHAR(255)", True, False, False, None, None)],
            comment="User email addresses",
        )
    ]

    snapshot = {
        "users": {
            "columns": {"email": {"type": "VARCHAR(255)", "nullable": True, "default": None}},
            "object_type": "table",
        }
    }

    issues = analyze_schema(model_tables, snapshot)

    assert any(i.change_type == "change_table_comment" for i in issues)


def test_analyze_schema_detect_pg_column_meta_change():
    col = ModelColumn("email", "VARCHAR(255)", False, False, False, None, None,
                       comment="New email",
                       pg_meta={"pg_collation": "en_US.UTF-8"})

    model_tables = [
        ModelTable(name="users", columns=[col])
    ]

    snapshot = {
        "users": {
            "columns": {
                "email": {"type": "VARCHAR(255)", "nullable": False, "default": None,
                          "comment": "Old email", "pg_column": {"collation": "C"}}
            },
            "object_type": "table",
        }
    }

    issues = analyze_schema(model_tables, snapshot)

    assert any(i.change_type == "change_pg_column_meta" for i in issues)


def test_analyze_schema_add_projection_is_info():
    model_tables = [
        ModelTable(
            name="posts",
            columns=[ModelColumn("author", "String", False, False, False, None, None)],
            clickhouse_options={
                "ch_projections": [
                    {"name": "by_author", "query": "SELECT * ORDER BY author"}
                ]
            },
        )
    ]

    snapshot = {
        "posts": {
            "object_type": "table",
            "columns": {"author": {"type": "String", "nullable": False, "default": None}},
            "ch_options": {},
        }
    }

    issues = analyze_schema(model_tables, snapshot)

    assert len(issues) == 1
    assert issues[0].severity == "INFO"
    assert issues[0].change_type == "add_projection"


def test_analyze_schema_ttl_change_is_warning():
    model_tables = [
        ModelTable(
            name="events",
            columns=[ModelColumn("id", "UInt64", False, True, False, None, None)],
            clickhouse_options={
                "ch_ttl": ["event_time + INTERVAL 1 MONTH DELETE"],
            },
        )
    ]

    snapshot = {
        "events": {
            "object_type": "table",
            "columns": {"id": {"type": "UInt64", "nullable": False, "default": None}},
            "ch_options": {
                "ch_ttl": ["event_time + INTERVAL 1 YEAR DELETE"],
            },
        }
    }

    issues = analyze_schema(model_tables, snapshot)

    assert len(issues) == 1
    assert issues[0].severity == "WARNING"
    assert issues[0].required_flag == "--force"


def test_analyze_schema_ttl_change_is_warning():
    model_tables = [
        ModelTable(
            name="events",
            columns=[ModelColumn("id", "UInt64", False, True, False, None, None)],
            clickhouse_options={
                "ch_ttl": "event_time + INTERVAL 30 DAY",
            },
        )
    ]

    snapshot = {
        "events": {
            "object_type": "table",
            "columns": {"id": {"type": "UInt64", "nullable": False, "default": None}},
            "ch_options": {
                "ch_ttl": "event_time + INTERVAL 90 DAY",
            },
        }
    }

    issues = analyze_schema(model_tables, snapshot)

    assert len(issues) == 1
    assert issues[0].severity == "WARNING"


def test_check_cmd_requires_force_for_warning(monkeypatch):
    monkeypatch.setattr(
        "dbwarden.commands.check.load_issues",
        lambda database=None: [
            analyze_schema(
                [
                    ModelTable(
                        name="events",
                        columns=[ModelColumn("id", "UInt64", False, True, False, None, None)],
                        clickhouse_options={"ch_ttl": ["new_ttl"]},
                    )
                ],
                {
                    "events": {
                        "object_type": "table",
                        "columns": {"id": {"type": "UInt64", "nullable": False, "default": None}},
                        "ch_options": {"ch_ttl": ["old_ttl"]},
                    }
                },
            )[0]
        ],
    )

    try:
        check_cmd(output_format="txt", force=False)
    except RuntimeError as exc:
        assert "require --force" in str(exc)
    else:
        raise AssertionError("Expected RuntimeError")


def test_check_cmd_allows_warning_with_force(monkeypatch):
    monkeypatch.setattr(
        "dbwarden.commands.check.load_issues",
        lambda database=None: [
            analyze_schema(
                [
                    ModelTable(
                        name="events",
                        columns=[ModelColumn("id", "UInt64", False, True, False, None, None)],
                        clickhouse_options={"ch_ttl": ["new_ttl"]},
                    )
                ],
                {
                    "events": {
                        "object_type": "table",
                        "columns": {"id": {"type": "UInt64", "nullable": False, "default": None}},
                        "ch_options": {"ch_ttl": ["old_ttl"]},
                    }
                },
            )[0]
        ],
    )

    check_cmd(output_format="json", force=True)


def test_analyze_schema_replicated_engine_change_is_warning():
    model_tables = [
        ModelTable(
            name="events",
            columns=[ModelColumn("id", "UInt64", False, True, False, None, None)],
            clickhouse_options={
                "ch_engine": "ReplicatedMergeTree",
                "ch_zookeeper_path": "'/clickhouse/tables/shard1'",
                "ch_replica_name": "'{replica}'",
            },
        )
    ]

    snapshot = {
        "events": {
            "object_type": "table",
            "columns": {"id": {"type": "UInt64", "nullable": False, "default": None}},
            "ch_options": {
                "ch_zookeeper_path": "'/clickhouse/tables/old_path'",
                "ch_replica_name": "'{replica}'",
            },
        }
    }

    issues = analyze_schema(model_tables, snapshot)

    zk_issues = [i for i in issues if i.change_type == "clickhouse_zookeeper_path"]
    assert len(zk_issues) == 1
    assert zk_issues[0].severity == "WARNING"
    assert zk_issues[0].required_flag == "--force"


def test_analyze_schema_dictionary_create_is_info():
    model_tables = [
        ModelTable(
            name="country_codes",
            columns=[ModelColumn("code", "String", False, True, False, None, None)],
            clickhouse_options={
                "ch_dictionary": True,
                "ch_dict_layout": "FLAT()",
                "ch_dict_source": "CLICKHOUSE(HOST 'localhost' TABLE 'countries')",
                "ch_dict_lifetime": "MIN 0 MAX 3600",
            },
            object_type="dictionary",
        )
    ]

    snapshot = {}

    issues = analyze_schema(model_tables, snapshot)

    create_issues = [i for i in issues if i.change_type == "create_table"]
    assert len(create_issues) == 1
    assert create_issues[0].severity == "INFO"
    assert "dictionary" in create_issues[0].message


def test_analyze_schema_dictionary_source_change_is_warning():
    model_tables = [
        ModelTable(
            name="country_codes",
            columns=[ModelColumn("code", "String", False, True, False, None, None)],
            clickhouse_options={
                "ch_dictionary": True,
                "ch_dict_layout": "FLAT()",
                "ch_dict_source": "CLICKHOUSE(HOST 'new_host' TABLE 'countries')",
                "ch_dict_lifetime": "MIN 0 MAX 3600",
            },
            object_type="dictionary",
        )
    ]

    snapshot = {
        "country_codes": {
            "object_type": "dictionary",
            "columns": {"code": {"type": "String", "nullable": False, "default": None}},
            "ch_options": {
                "ch_dict_source": "CLICKHOUSE(HOST 'old_host' TABLE 'countries')",
            },
        }
    }

    issues = analyze_schema(model_tables, snapshot)

    source_issues = [i for i in issues if i.change_type == "clickhouse_dict_source"]
    assert len(source_issues) == 1
    assert source_issues[0].severity == "WARNING"
    assert source_issues[0].required_flag == "--force"


def test_ch_engine_change_raises_safety_issue():
    """Changes to ch_engine should raise a safety warning."""
    model_tables = [
        ModelTable(
            name="events",
            columns=[ModelColumn("id", "UInt64", False, True, False, None, None)],
            clickhouse_options={"ch_engine": "ReplacingMergeTree"},
        )
    ]
    snapshot = {
        "events": {
            "columns": {"id": {"type": "UInt64", "nullable": False, "default": None, "ch_column": {"ch_type": "UInt64"}}},
            "ch_options": {"ch_engine": "MergeTree"},
            "object_type": "table",
            "indexes": {},
        }
    }
    issues = analyze_schema(model_tables, snapshot)
    engine_issues = [i for i in issues if i.change_type == "clickhouse_engine"]
    assert len(engine_issues) > 0


def test_ch_order_by_change_raises_safety_issue():
    model_tables = [
        ModelTable(
            name="events",
            columns=[ModelColumn("id", "UInt64", False, True, False, None, None)],
            clickhouse_options={"ch_order_by": ["id", "name"]},
        )
    ]
    snapshot = {
        "events": {
            "columns": {"id": {"type": "UInt64", "nullable": False, "default": None, "ch_column": {"ch_type": "UInt64"}}},
            "ch_options": {"ch_order_by": ["id"]},
            "object_type": "table",
            "indexes": {},
        }
    }
    issues = analyze_schema(model_tables, snapshot)
    order_by_issues = [i for i in issues if i.change_type == "clickhouse_order_by"]
    assert len(order_by_issues) > 0


def test_ch_ttl_change_raises_safety_warning():
    model_tables = [
        ModelTable(
            name="events",
            columns=[ModelColumn("id", "UInt64", False, True, False, None, None)],
            clickhouse_options={"ch_ttl": ["created_at + INTERVAL 90 DAY"]},
        )
    ]
    snapshot = {
        "events": {
            "columns": {"id": {"type": "UInt64", "nullable": False, "default": None, "ch_column": {"ch_type": "UInt64"}}},
            "ch_options": {"ch_ttl": ["created_at + INTERVAL 30 DAY"]},
            "object_type": "table",
            "indexes": {},
        }
    }
    issues = analyze_schema(model_tables, snapshot)
    ttl_issues = [i for i in issues if i.change_type == "clickhouse_ttl"]
    assert len(ttl_issues) > 0


def test_ch_add_column_no_safety_issue():
    """Adding a column to a ClickHouse table should only raise informational, not ERROR."""
    model_tables = [
        ModelTable(
            name="events",
            columns=[
                ModelColumn("id", "UInt64", False, True, False, None, None),
                ModelColumn("name", "String", True, False, False, None, None),
            ],
            clickhouse_options={"ch_engine": "MergeTree", "ch_order_by": ["id"]},
        )
    ]
    snapshot = {
        "events": {
            "columns": {"id": {"type": "UInt64", "nullable": False, "default": None, "ch_column": {"ch_type": "UInt64"}}},
            "ch_options": {"ch_engine": "MergeTree", "ch_order_by": ["id"]},
            "object_type": "table",
            "indexes": {},
        }
    }
    issues = analyze_schema(model_tables, snapshot)
    assert len(issues) == 1
    assert issues[0].change_type == "add_column"
    assert issues[0].severity == "INFO"


class TestCHSafetyClassifiers:
    def test_classify_ch_column_change_critical(self):
        from dbwarden.engine.safety import classify_ch_column_change
        assert classify_ch_column_change("ch_type") == "CRITICAL"
        assert classify_ch_column_change("ch_low_cardinality") == "CRITICAL"
        assert classify_ch_column_change("ch_nullable") == "CRITICAL"

    def test_classify_ch_column_change_warn(self):
        from dbwarden.engine.safety import classify_ch_column_change
        assert classify_ch_column_change("ch_codec") == "WARN"
        assert classify_ch_column_change("ch_default_expression") == "WARN"
        assert classify_ch_column_change("ch_materialized") == "WARN"
        assert classify_ch_column_change("ch_alias") == "WARN"
        assert classify_ch_column_change("ch_ephemeral") == "WARN"
        assert classify_ch_column_change("ch_ttl") == "WARN"

    def test_classify_ch_column_change_info(self):
        from dbwarden.engine.safety import classify_ch_column_change
        assert classify_ch_column_change("comment") == "INFO"
        assert classify_ch_column_change("unknown_key") == "INFO"

    def test_classify_ch_options_change_critical(self):
        from dbwarden.engine.safety import classify_ch_options_change
        assert classify_ch_options_change("ch_engine_raw") == "CRITICAL"
        assert classify_ch_options_change("ch_order_by") == "CRITICAL"
        assert classify_ch_options_change("ch_object_type") == "CRITICAL"
        assert classify_ch_options_change("ch_select_statement") == "CRITICAL"
        assert classify_ch_options_change("ch_to_table") == "CRITICAL"
        assert classify_ch_options_change("ch_dictionary") == "CRITICAL"

    def test_classify_ch_options_change_warn(self):
        from dbwarden.engine.safety import classify_ch_options_change
        assert classify_ch_options_change("ch_settings") == "WARN"
        assert classify_ch_options_change("ch_zookeeper_path") == "WARN"
        assert classify_ch_options_change("ch_zookeeper_path") == "WARN"
        assert classify_ch_options_change("ch_replica_name") == "WARN"
        assert classify_ch_options_change("ch_dict_layout") == "WARN"
        assert classify_ch_options_change("ch_dict_source") == "WARN"
        assert classify_ch_options_change("ch_dict_lifetime") == "WARN"
        assert classify_ch_options_change("ch_dict_primary_key") == "WARN"

    def test_classify_ch_options_change_info(self):
        from dbwarden.engine.safety import classify_ch_options_change
        assert classify_ch_options_change("ch_sample_by") == "INFO"
        assert classify_ch_options_change("ch_ttl") == "INFO"
        assert classify_ch_options_change("unknown") == "INFO"

    def test_classify_ch_safety_alter_ch_column(self):
        from dbwarden.engine.safety import classify_ch_safety
        op = {
            "type": "alter_ch_column",
            "table": "events",
            "column": "payload",
            "ch_options": {
                "ch_codec": {"from": None, "to": "ZSTD(3)"},
                "ch_type": {"from": "String", "to": "Int64"},
            },
        }
        issues = classify_ch_safety(op)
        assert len(issues) == 2
        severities = {i.change_type: i.severity for i in issues}
        assert severities["change_ch_column"] in ("WARN", "CRITICAL")

    def test_classify_ch_safety_alter_ch_options(self):
        from dbwarden.engine.safety import classify_ch_safety
        op = {
            "type": "alter_ch_options",
            "table": "events",
            "ch_options": {
                "ch_engine_raw": {"from": {"name": "MergeTree"}, "to": {"name": "ReplicatedMergeTree"}},
                "ch_settings": {"from": None, "to": {"a": "1"}},
            },
        }
        issues = classify_ch_safety(op)
        assert len(issues) == 2

    def test_classify_ch_safety_drop_materialized_view(self):
        from dbwarden.engine.safety import classify_ch_safety
        op = {
            "type": "drop_table",
            "table": "daily_mv",
            "object_type": "materialized_view",
        }
        issues = classify_ch_safety(op)
        assert len(issues) == 1
        assert issues[0].severity == "CRITICAL"
        assert issues[0].change_type == "drop_materialized_view"


class TestAnalyzeSchemaDropTable:
    def test_drop_table_detected(self):
        issues = analyze_schema(
            model_tables=[],
            schema_snapshot={"orphan": {"columns": {}}},
        )
        assert len(issues) == 1
        assert issues[0].change_type == "drop_table"

    def test_change_object_type_detected(self):
        model_tables = [
            ModelTable(name="t", columns=[], object_type="view"),
        ]
        snapshot = {
            "t": {"columns": {}, "object_type": "table"},
        }
        issues = analyze_schema(model_tables, snapshot)
        change_issues = [i for i in issues if i.change_type == "change_object_type"]
        assert len(change_issues) >= 1

    def test_drop_column_detected(self):
        model_tables = [
            ModelTable(name="t", columns=[]),
        ]
        snapshot = {
            "t": {"columns": {"old_col": {"type": "INTEGER"}}},
        }
        issues = analyze_schema(model_tables, snapshot)
        drop_issues = [i for i in issues if i.change_type == "drop_column"]
        assert len(drop_issues) >= 1

    def test_pg_table_changes_detected(self):
        model_tables = [
            ModelTable(name="t", columns=[], pg_table={"fillfactor": 90}),
        ]
        snapshot = {
            "t": {"columns": {}, "pg_table": {"fillfactor": 80}},
        }
        issues = analyze_schema(model_tables, snapshot)
        fillfactor_issues = [i for i in issues if "fillfactor" in i.message]
        assert len(fillfactor_issues) >= 1

    def test_pg_storage_params_changes_detected(self):
        model_tables = [
            ModelTable(name="t", columns=[], pg_table={"pg_storage_params": {"autovacuum_enabled": "off"}}),
        ]
        snapshot = {
            "t": {"columns": {}, "pg_table": {"pg_storage_params": {"autovacuum_enabled": "on"}}},
        }
        issues = analyze_schema(model_tables, snapshot)
        storage_issues = [i for i in issues if "storage parameter" in i.message]
        assert len(storage_issues) >= 1

    def test_pg_type_with_meta(self):
        model_tables = [
            ModelTable(
                name="t",
                columns=[
                    ModelColumn("c", "INTEGER", True, False, False, None, None,
                                pg_meta={"pg_type": {"kind": "integer"}}),
                ],
            ),
        ]
        snapshot = {
            "t": {"columns": {"c": {"type": "INTEGER"}}, "database_type": "postgresql"},
        }
        issues = analyze_schema(model_tables, snapshot)
        assert isinstance(issues, list)

    def test_pg_enum_override(self):
        model_tables = [
            ModelTable(
                name="t",
                columns=[
                    ModelColumn("c", "VARCHAR(50)", True, False, False, None, None,
                                pg_meta={"pg_enum_name": "mood"}),
                ],
            ),
        ]
        snapshot = {
            "t": {"columns": {"c": {"type": "VARCHAR(50)"}}, "database_type": "postgresql"},
        }
        issues = analyze_schema(model_tables, snapshot)
        assert isinstance(issues, list)


class TestSnapshotColumnTypeSignature:
    def test_basic_type(self):
        from dbwarden.engine.safety.classifiers import _snapshot_column_type_signature

        sig = _snapshot_column_type_signature({"type": "VARCHAR"})
        assert sig["type"] == "varchar"

    def test_with_length(self):
        from dbwarden.engine.safety.classifiers import _snapshot_column_type_signature

        sig = _snapshot_column_type_signature({"type": "VARCHAR", "length": 255})
        assert sig["type"] == "VARCHAR"
        assert sig["length"] == 255

    def test_with_precision_and_scale(self):
        from dbwarden.engine.safety.classifiers import _snapshot_column_type_signature

        sig = _snapshot_column_type_signature({"type": "NUMERIC", "precision": 10, "scale": 2})
        assert sig["type"] == "NUMERIC"
        assert sig["precision"] == 10
        assert sig["scale"] == 2

    def test_with_pg_type(self):
        from dbwarden.engine.safety.classifiers import _snapshot_column_type_signature

        sig = _snapshot_column_type_signature({"type": "MYDOMAIN", "pg_type": {"kind": "varchar"}})
        assert sig["type"] == "MYDOMAIN"
        assert sig["pg_type"]["kind"] == "varchar"

    def test_with_enum_name(self):
        from dbwarden.engine.safety.classifiers import _snapshot_column_type_signature

        sig = _snapshot_column_type_signature({"type": "ENUM", "enum_name": "mood"})
        assert sig["type"] == "ENUM"
        assert sig["enum_name"] == "mood"
