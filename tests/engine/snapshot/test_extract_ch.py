from __future__ import annotations

from unittest.mock import MagicMock, PropertyMock


def _mock_row(**kwargs):
    row = MagicMock()
    for k, v in kwargs.items():
        setattr(row, k, v)
    return row


def _make_conn(*result_lists):
    """Create a connection mock that returns the given result lists on successive execute calls."""
    conn = MagicMock()
    results = []
    for rows in result_lists:
        r = MagicMock()
        r.fetchall.return_value = rows
        results.append(r)
    conn.execute.side_effect = results
    return conn


class TestExtractSettingDefaults:
    def test_returns_defaults(self):
        from dbwarden.engine.snapshot.extract_ch import _extract_setting_defaults

        row1 = _mock_row(name="max_block_size", value="65536")
        row2 = _mock_row(name="min_insert_block_size_rows", value="1048448")
        conn = _make_conn([row1, row2])
        result = _extract_setting_defaults(conn)
        assert result == {"max_block_size": "65536", "min_insert_block_size_rows": "1048448"}

    def test_no_rows(self):
        from dbwarden.engine.snapshot.extract_ch import _extract_setting_defaults

        conn = _make_conn([])
        result = _extract_setting_defaults(conn)
        assert result == {}


class TestExtractClickhouseSchemaSnapshot:
    def test_minimal(self):
        from dbwarden.engine.snapshot.extract_ch import _extract_clickhouse_schema_snapshot

        conn = MagicMock()
        conn.execute.return_value.fetchall.return_value = []
        result = _extract_clickhouse_schema_snapshot(conn, "test_db")
        assert result["database_name"] == "test_db"
        assert result["database_type"] == "clickhouse"
        assert result["tables"] == {}
        assert result["named_collections"] == {}
        assert result["roles"] == {}
        assert result["users"] == {}
        assert result["settings_profiles"] == {}
        assert result["quotas"] == {}
        assert result["row_policies"] == {}
        assert result["grants"] == {}

    def test_with_named_collections(self):
        from dbwarden.engine.snapshot.extract_ch import _extract_clickhouse_schema_snapshot

        row = _mock_row(name="s3_creds", collection={"key": "val"})
        conn = MagicMock()
        conn.execute.return_value.fetchall.return_value = []
        conn.execute.side_effect = None

        def _execute_side(*args, **kwargs):
            result = MagicMock()
            sql = str(args[0]) if args else ""
            if "named_collections" in sql:
                result.fetchall.return_value = [row]
            else:
                result.fetchall.return_value = []
            return result

        conn.execute.side_effect = _execute_side
        result = _extract_clickhouse_schema_snapshot(conn, "test_db")
        assert "s3_creds" in result["named_collections"]
        assert result["named_collections"]["s3_creds"]["entries"] == {"key": "val"}

    def test_named_collections_empty_collection(self):
        from dbwarden.engine.snapshot.extract_ch import _extract_clickhouse_schema_snapshot

        row = _mock_row(name="s3_creds", collection=None)
        conn = MagicMock()

        def _execute_side(*args, **kwargs):
            result = MagicMock()
            sql = str(args[0]) if args else ""
            if "named_collections" in sql:
                result.fetchall.return_value = [row]
            else:
                result.fetchall.return_value = []
            return result

        conn.execute.side_effect = _execute_side
        result = _extract_clickhouse_schema_snapshot(conn, "test_db")
        assert result["named_collections"]["s3_creds"]["entries"] == {}

    def test_named_collections_exception(self):
        from dbwarden.engine.snapshot.extract_ch import _extract_clickhouse_schema_snapshot

        conn = MagicMock()

        def _execute_side(*args, **kwargs):
            sql = str(args[0]) if args else ""
            if "named_collections" in sql:
                raise Exception("no such table: system.named_collections")
            result = MagicMock()
            result.fetchall.return_value = []
            return result

        conn.execute.side_effect = _execute_side
        result = _extract_clickhouse_schema_snapshot(conn, "test_db")
        assert result["named_collections"] == {}

    def test_with_roles(self):
        from dbwarden.engine.snapshot.extract_ch import _extract_clickhouse_schema_snapshot

        row = _mock_row(name="admin", storage="local")
        conn = MagicMock()

        def _execute_side(*args, **kwargs):
            result = MagicMock()
            sql = str(args[0]) if args else ""
            if "roles" in sql:
                result.fetchall.return_value = [row]
            else:
                result.fetchall.return_value = []
            return result

        conn.execute.side_effect = _execute_side
        result = _extract_clickhouse_schema_snapshot(conn, "test_db")
        assert "admin" in result["roles"]
        assert result["roles"]["admin"]["storage"] == "local"

    def test_roles_exception(self):
        from dbwarden.engine.snapshot.extract_ch import _extract_clickhouse_schema_snapshot

        conn = MagicMock()

        def _execute_side(*args, **kwargs):
            sql = str(args[0]) if args else ""
            if "roles" in sql:
                raise Exception("no such table: system.roles")
            result = MagicMock()
            result.fetchall.return_value = []
            return result

        conn.execute.side_effect = _execute_side
        result = _extract_clickhouse_schema_snapshot(conn, "test_db")
        assert result["roles"] == {}

    def test_with_users(self):
        from dbwarden.engine.snapshot.extract_ch import _extract_clickhouse_schema_snapshot

        row = _mock_row(name="alice", storage="local", auth_type="password",
                        host_ip=None, host_names=None, host_regexp=None, host_like=None,
                        default_roles=None, settings_profile=None, grantees=None)
        conn = MagicMock()

        def _execute_side(*args, **kwargs):
            result = MagicMock()
            sql = str(args[0]) if args else ""
            if "system.users" in sql:
                result.fetchall.return_value = [row]
            else:
                result.fetchall.return_value = []
            return result

        conn.execute.side_effect = _execute_side
        result = _extract_clickhouse_schema_snapshot(conn, "test_db")
        assert "alice" in result["users"]
        assert result["users"]["alice"]["auth"] == "password"

    def test_users_exception(self):
        from dbwarden.engine.snapshot.extract_ch import _extract_clickhouse_schema_snapshot

        conn = MagicMock()

        def _execute_side(*args, **kwargs):
            sql = str(args[0]) if args else ""
            if "system.users" in sql:
                raise Exception("no such table")
            result = MagicMock()
            result.fetchall.return_value = []
            return result

        conn.execute.side_effect = _execute_side
        result = _extract_clickhouse_schema_snapshot(conn, "test_db")
        assert result["users"] == {}

    def test_with_settings_profiles(self):
        from dbwarden.engine.snapshot.extract_ch import _extract_clickhouse_schema_snapshot

        row = _mock_row(name="readonly", storage="local", settings={"max_memory_usage": "10000000"}, to_roles=["admin"])
        conn = MagicMock()

        def _execute_side(*args, **kwargs):
            result = MagicMock()
            sql = str(args[0]) if args else ""
            if "settings_profiles" in sql:
                result.fetchall.return_value = [row]
            else:
                result.fetchall.return_value = []
            return result

        conn.execute.side_effect = _execute_side
        result = _extract_clickhouse_schema_snapshot(conn, "test_db")
        assert "readonly" in result["settings_profiles"]
        assert result["settings_profiles"]["readonly"]["to_roles"] == ["admin"]

    def test_settings_profiles_exception(self):
        from dbwarden.engine.snapshot.extract_ch import _extract_clickhouse_schema_snapshot

        conn = MagicMock()

        def _execute_side(*args, **kwargs):
            sql = str(args[0]) if args else ""
            if "settings_profiles" in sql:
                raise Exception("no such table")
            result = MagicMock()
            result.fetchall.return_value = []
            return result

        conn.execute.side_effect = _execute_side
        result = _extract_clickhouse_schema_snapshot(conn, "test_db")
        assert result["settings_profiles"] == {}

    def test_with_quotas(self):
        from dbwarden.engine.snapshot.extract_ch import _extract_clickhouse_schema_snapshot

        row = _mock_row(name="default", storage="local", interval="MONTHLY",
                        queries=1000, errors=0, result_rows=0, read_rows=0, execution_time=0)
        conn = MagicMock()

        def _execute_side(*args, **kwargs):
            result = MagicMock()
            sql = str(args[0]) if args else ""
            if "quotas" in sql and "system" in sql.lower():
                result.fetchall.return_value = [row]
            else:
                result.fetchall.return_value = []
            return result

        conn.execute.side_effect = _execute_side
        result = _extract_clickhouse_schema_snapshot(conn, "test_db")
        assert "default" in result["quotas"]

    def test_quotas_exception(self):
        from dbwarden.engine.snapshot.extract_ch import _extract_clickhouse_schema_snapshot

        conn = MagicMock()

        def _execute_side(*args, **kwargs):
            sql = str(args[0]) if args else ""
            if "quotas" in sql:
                raise Exception("no such table")
            result = MagicMock()
            result.fetchall.return_value = []
            return result

        conn.execute.side_effect = _execute_side
        result = _extract_clickhouse_schema_snapshot(conn, "test_db")
        assert result["quotas"] == {}

    def test_with_row_policies(self):
        from dbwarden.engine.snapshot.extract_ch import _extract_clickhouse_schema_snapshot

        row = _mock_row(name="pol1", short_name="pol1", storage="local",
                        database="mydb", table="mytable", select_filter="id > 0",
                        is_permissive=True, roles=["admin"])
        conn = MagicMock()

        def _execute_side(*args, **kwargs):
            result = MagicMock()
            sql = str(args[0]) if args else ""
            if "row_policies" in sql:
                result.fetchall.return_value = [row]
            else:
                result.fetchall.return_value = []
            return result

        conn.execute.side_effect = _execute_side
        result = _extract_clickhouse_schema_snapshot(conn, "test_db")
        assert "pol1" in result["row_policies"]
        assert result["row_policies"]["pol1"]["to_roles"] == ["admin"]

    def test_row_policies_exception(self):
        from dbwarden.engine.snapshot.extract_ch import _extract_clickhouse_schema_snapshot

        conn = MagicMock()

        def _execute_side(*args, **kwargs):
            sql = str(args[0]) if args else ""
            if "row_policies" in sql:
                raise Exception("no such table")
            result = MagicMock()
            result.fetchall.return_value = []
            return result

        conn.execute.side_effect = _execute_side
        result = _extract_clickhouse_schema_snapshot(conn, "test_db")
        assert result["row_policies"] == {}

    def test_with_grants_grant_option(self):
        from dbwarden.engine.snapshot.extract_ch import _extract_clickhouse_schema_snapshot

        row = _mock_row(user_name="alice", role_name=None, access_type="SELECT",
                        database="mydb", table="mytable",
                        is_partial_revoke=False, grant_option=True)
        conn = MagicMock()

        def _execute_side(*args, **kwargs):
            result = MagicMock()
            sql = str(args[0]) if args else ""
            if "grants" in sql:
                result.fetchall.return_value = [row]
            else:
                result.fetchall.return_value = []
            return result

        conn.execute.side_effect = _execute_side
        result = _extract_clickhouse_schema_snapshot(conn, "test_db")
        assert any(g["with_grant_option"] for g in result["grants"].values())

    def test_grants_exception(self):
        from dbwarden.engine.snapshot.extract_ch import _extract_clickhouse_schema_snapshot

        conn = MagicMock()

        def _execute_side(*args, **kwargs):
            sql = str(args[0]) if args else ""
            if "grants" in sql:
                raise Exception("no such table")
            result = MagicMock()
            result.fetchall.return_value = []
            return result

        conn.execute.side_effect = _execute_side
        result = _extract_clickhouse_schema_snapshot(conn, "test_db")
        assert result["grants"] == {}

    def test_with_dictionary_object(self):
        from dbwarden.engine.snapshot.extract_ch import _extract_clickhouse_schema_snapshot

        table_row = _mock_row(name="my_dict", engine="Dictionary",
                              engine_full="", create_table_query="",
                              sorting_key=None, primary_key=None,
                              partition_key=None, sampling_key=None,
                              comment=None)
        conn = MagicMock()

        def _execute_side(*args, **kwargs):
            result = MagicMock()
            sql = str(args[0]) if args else ""
            if "named_collections" in sql or "roles" in sql or "users" in sql or "system.users" in sql:
                result.fetchall.return_value = []
            elif "settings_profiles" in sql:
                result.fetchall.return_value = []
            elif "quotas" in sql:
                result.fetchall.return_value = []
            elif "row_policies" in sql:
                result.fetchall.return_value = []
            elif "grants" in sql:
                result.fetchall.return_value = []
            elif "system.tables" in sql:
                result.fetchall.return_value = [table_row]
            elif "system.columns" in sql and "ttl_expression" not in sql:
                result.fetchall.return_value = []
            elif "data_skipping_indices" in sql:
                result.fetchall.return_value = []
            elif "merge_tree_settings" in sql:
                result.fetchall.return_value = []
            elif "ttl_expression" in sql:
                result.fetchall.return_value = []
            else:
                result.fetchall.return_value = []
            return result

        conn.execute.side_effect = _execute_side
        result = _extract_clickhouse_schema_snapshot(conn, "test_db")
        assert "my_dict" in result["tables"]
        assert result["tables"]["my_dict"]["ch_options"]["ch_object_type"] == "dictionary"
        assert result["tables"]["my_dict"]["ch_options"]["ch_dictionary"] is True

    def test_with_materialized_view(self):
        from dbwarden.engine.snapshot.extract_ch import _extract_clickhouse_schema_snapshot

        table_row = _mock_row(name="my_mv", engine="ReplicatedReplacingMergeTree",
                              engine_full="",
                              create_table_query="CREATE MATERIALIZED VIEW my_mv ENGINE = ReplicatedReplacingMergeTree('/a', '1') ORDER BY id AS SELECT * FROM src",
                              sorting_key="id", primary_key=None,
                              partition_key=None, sampling_key=None,
                              comment=None)
        conn = MagicMock()

        def _execute_side(*args, **kwargs):
            result = MagicMock()
            sql = str(args[0]) if args else ""
            if "system.tables" in sql:
                result.fetchall.return_value = [table_row]
            elif "system.columns" in sql and "ttl_expression" not in sql:
                result.fetchall.return_value = []
            elif "data_skipping_indices" in sql:
                result.fetchall.return_value = []
            elif "merge_tree_settings" in sql:
                result.fetchall.return_value = []
            else:
                result.fetchall.return_value = []
            return result

        conn.execute.side_effect = _execute_side
        result = _extract_clickhouse_schema_snapshot(conn, "test_db")
        assert "my_mv" in result["tables"]
        assert result["tables"]["my_mv"]["ch_options"]["ch_object_type"] == "materialized_view"

    def test_with_data_skipping_indices(self):
        from dbwarden.engine.snapshot.extract_ch import _extract_clickhouse_schema_snapshot

        table_row = _mock_row(name="events", engine="MergeTree",
                              engine_full="MergeTree()",
                              create_table_query="CREATE TABLE events (id UInt32) ENGINE = MergeTree() ORDER BY id",
                              sorting_key="id", primary_key=None,
                              partition_key=None, sampling_key=None,
                              comment=None)
        idx_row = _mock_row(table="events", name="idx_val", type="MINMAX", expr="value", granularity=1)
        conn = MagicMock()

        def _execute_side(*args, **kwargs):
            result = MagicMock()
            sql = str(args[0]) if args else ""
            if "system.tables" in sql:
                result.fetchall.return_value = [table_row]
            elif "system.columns" in sql and "ttl_expression" not in sql:
                result.fetchall.return_value = []
            elif "data_skipping_indices" in sql:
                result.fetchall.return_value = [idx_row]
            elif "merge_tree_settings" in sql:
                result.fetchall.return_value = []
            else:
                result.fetchall.return_value = []
            return result

        conn.execute.side_effect = _execute_side
        result = _extract_clickhouse_schema_snapshot(conn, "test_db")
        assert "events" in result["tables"]
        assert len(result["tables"]["events"]["indexes"]) == 1
        assert result["tables"]["events"]["indexes"][0]["name"] == "idx_val"

    def test_materialized_view_no_engine_match(self):
        from dbwarden.engine.snapshot.extract_ch import _extract_clickhouse_schema_snapshot

        table_row = _mock_row(name="my_mv", engine="MaterializedView",
                              engine_full="",
                              create_table_query="CREATE MATERIALIZED VIEW my_mv ORDER BY id AS SELECT * FROM src",
                              sorting_key=None, primary_key=None,
                              partition_key=None, sampling_key=None,
                              comment=None)
        conn = MagicMock()

        def _execute_side(*args, **kwargs):
            result = MagicMock()
            sql = str(args[0]) if args else ""
            if "system.tables" in sql:
                result.fetchall.return_value = [table_row]
            elif "system.columns" in sql and "ttl_expression" not in sql:
                result.fetchall.return_value = []
            elif "data_skipping_indices" in sql:
                result.fetchall.return_value = []
            elif "merge_tree_settings" in sql:
                result.fetchall.return_value = []
            else:
                result.fetchall.return_value = []
            return result

        conn.execute.side_effect = _execute_side
        result = _extract_clickhouse_schema_snapshot(conn, "test_db")
        assert "my_mv" in result["tables"]
        assert result["tables"]["my_mv"]["ch_options"]["ch_object_type"] == "materialized_view"
        assert result["tables"]["my_mv"]["ch_options"]["ch_engine"] is None

    def test_materialized_view_fallback_sorting_key(self):
        from dbwarden.engine.snapshot.extract_ch import _extract_clickhouse_schema_snapshot

        table_row = _mock_row(name="my_mv", engine="MaterializedView",
                              engine_full="",
                              create_table_query="CREATE MATERIALIZED VIEW my_mv ENGINE = MergeTree() AS SELECT * FROM src",
                              sorting_key="id", primary_key=None,
                              partition_key=None, sampling_key=None,
                              comment=None)
        conn = MagicMock()

        def _execute_side(*args, **kwargs):
            result = MagicMock()
            sql = str(args[0]) if args else ""
            if "system.tables" in sql:
                result.fetchall.return_value = [table_row]
            elif "system.columns" in sql and "ttl_expression" not in sql:
                result.fetchall.return_value = []
            elif "data_skipping_indices" in sql:
                result.fetchall.return_value = []
            elif "merge_tree_settings" in sql:
                result.fetchall.return_value = []
            else:
                result.fetchall.return_value = []
            return result

        conn.execute.side_effect = _execute_side
        result = _extract_clickhouse_schema_snapshot(conn, "test_db")
        assert "my_mv" in result["tables"]
        assert result["tables"]["my_mv"]["ch_options"]["ch_object_type"] == "materialized_view"
        assert result["tables"]["my_mv"]["ch_options"]["ch_order_by"] == "id"

    def test_materialized_view_with_order_by(self):
        from dbwarden.engine.snapshot.extract_ch import _extract_clickhouse_schema_snapshot

        table_row = _mock_row(name="my_mv", engine="MaterializedView",
                              engine_full="",
                              create_table_query="CREATE MATERIALIZED VIEW my_mv ENGINE = MergeTree() ORDER BY id AS SELECT * FROM src",
                              sorting_key=None, primary_key=None,
                              partition_key=None, sampling_key=None,
                              comment=None)
        conn = MagicMock()

        def _execute_side(*args, **kwargs):
            result = MagicMock()
            sql = str(args[0]) if args else ""
            if "system.tables" in sql:
                result.fetchall.return_value = [table_row]
            elif "system.columns" in sql and "ttl_expression" not in sql:
                result.fetchall.return_value = []
            elif "data_skipping_indices" in sql:
                result.fetchall.return_value = []
            elif "merge_tree_settings" in sql:
                result.fetchall.return_value = []
            else:
                result.fetchall.return_value = []
            return result

        conn.execute.side_effect = _execute_side
        result = _extract_clickhouse_schema_snapshot(conn, "test_db")
        assert result["tables"]["my_mv"]["ch_options"]["ch_order_by"] == "id"

    def test_with_regular_table(self):
        from dbwarden.engine.snapshot.extract_ch import _extract_clickhouse_schema_snapshot

        table_row = _mock_row(name="events", engine="MergeTree",
                              engine_full="MergeTree()",
                              create_table_query="CREATE TABLE events (id UInt32) ENGINE = MergeTree() ORDER BY id",
                              sorting_key="id", primary_key="id",
                              partition_key="toYYYYMM(event_date)", sampling_key=None,
                              comment="event log")
        column_row = _mock_row(table="events", name="id", type="UInt32",
                               default_kind=None, default_expression=None,
                               codec_expression=None, comment=None,
                               is_in_primary_key=True, is_in_sorting_key=True, is_in_partition_key=False)
        conn = MagicMock()

        def _execute_side(*args, **kwargs):
            result = MagicMock()
            sql = str(args[0]) if args else ""
            if "system.tables" in sql:
                result.fetchall.return_value = [table_row]
            elif "system.columns" in sql and "ttl_expression" not in sql:
                result.fetchall.return_value = [column_row]
            elif "data_skipping_indices" in sql:
                result.fetchall.return_value = []
            elif "merge_tree_settings" in sql:
                result.fetchall.return_value = []
            elif "ttl_expression" in sql:
                result.fetchall.return_value = []
            else:
                result.fetchall.return_value = []
            return result

        conn.execute.side_effect = _execute_side
        result = _extract_clickhouse_schema_snapshot(conn, "test_db")
        assert "events" in result["tables"]
        assert result["tables"]["events"]["ch_options"]["ch_object_type"] == "table"
        assert result["tables"]["events"]["comment"] == "event log"

    def test_with_ttl_expressions(self):
        from dbwarden.engine.snapshot.extract_ch import _extract_clickhouse_schema_snapshot

        table_row = _mock_row(name="events", engine="MergeTree",
                              engine_full="MergeTree()",
                              create_table_query="CREATE TABLE events (id UInt32) ENGINE = MergeTree() ORDER BY id",
                              sorting_key="id", primary_key=None,
                              partition_key=None, sampling_key=None,
                              comment=None)
        col_row = _mock_row(table="events", name="created_at", type="DateTime",
                            default_kind=None, default_expression=None,
                            codec_expression=None, comment=None,
                            is_in_primary_key=False, is_in_sorting_key=False,
                            is_in_partition_key=False)
        ttl_row = _mock_row(table="events", name="created_at", ttl_expression="created_at + INTERVAL 30 DAY")
        conn = MagicMock()

        def _execute_side(*args, **kwargs):
            result = MagicMock()
            sql = str(args[0]) if args else ""
            if "system.tables" in sql:
                result.fetchall.return_value = [table_row]
            elif "system.columns" in sql and "ttl_expression" not in sql:
                result.fetchall.return_value = [col_row]
            elif "ttl_expression" in sql:
                result.fetchall.return_value = [ttl_row]
            elif "data_skipping_indices" in sql:
                result.fetchall.return_value = []
            elif "merge_tree_settings" in sql:
                result.fetchall.return_value = []
            else:
                result.fetchall.return_value = []
            return result

        conn.execute.side_effect = _execute_side
        result = _extract_clickhouse_schema_snapshot(conn, "test_db")
        assert "events" in result["tables"]
        col_entry = result["tables"]["events"]["columns"]["created_at"]
        assert col_entry["ch_column"]["ch_ttl"] == "created_at + INTERVAL 30 DAY"

    def test_ttl_exception(self):
        from dbwarden.engine.snapshot.extract_ch import _extract_clickhouse_schema_snapshot

        conn = MagicMock()

        def _execute_side(*args, **kwargs):
            sql = str(args[0]) if args else ""
            if "ttl_expression" in sql:
                raise Exception("no such column")
            result = MagicMock()
            result.fetchall.return_value = []
            return result

        conn.execute.side_effect = _execute_side
        result = _extract_clickhouse_schema_snapshot(conn, "test_db")
        assert "tables" in result
