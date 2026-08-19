from __future__ import annotations

from dbwarden.engine.snapshot.ch_utils import (
    _clean_clickhouse_expression,
    _serialize_clickhouse_engine,
    _pick_clickhouse_codec,
    _check_ch_engine_recreate_allowed,
    _clickhouse_engine_family,
    _diff_ch_column_extras,
)
from types import SimpleNamespace
import pytest


class TestCleanClickhouseExpression:
    def test_none(self):
        assert _clean_clickhouse_expression(None) is None

    def test_normal_string(self):
        assert _clean_clickhouse_expression("SELECT 1") == "SELECT 1"

    def test_strips_surrounding_null_bytes(self):
        assert _clean_clickhouse_expression("\x00SELECT 1\x00") == "SELECT 1"

    def test_strips_whitespace(self):
        assert _clean_clickhouse_expression("  SELECT 1  ") == "SELECT 1"

    def test_empty_after_strip(self):
        assert _clean_clickhouse_expression("  ") is None


class TestSerializeClickhouseEngine:
    def test_none(self):
        assert _serialize_clickhouse_engine(None) is None

    def test_dict_no_name(self):
        assert _serialize_clickhouse_engine({"args": []}) is None

    def test_dict_name_no_args(self):
        result = _serialize_clickhouse_engine({"name": "MergeTree"})
        assert result == "MergeTree"

    def test_dict_with_args(self):
        result = _serialize_clickhouse_engine({"name": "ReplicatedMergeTree", "args": ["/table"]})
        assert result == ("ReplicatedMergeTree", "/table")

    def test_dict_with_zookeeper_path(self):
        result = _serialize_clickhouse_engine({
            "name": "ReplicatedMergeTree",
            "zookeeper_path": "/zk/path",
        })
        assert result == ("ReplicatedMergeTree", "/zk/path")

    def test_dict_with_zookeeper_and_replica(self):
        result = _serialize_clickhouse_engine({
            "name": "ReplicatedMergeTree",
            "zookeeper_path": "/zk/path",
            "replica_name": "r1",
        })
        assert result == ("ReplicatedMergeTree", "/zk/path", "r1")

    def test_dict_with_replica_no_zookeeper(self):
        result = _serialize_clickhouse_engine({
            "name": "ReplicatedMergeTree",
            "replica_name": "r1",
        })
        assert result == ("ReplicatedMergeTree", "r1")

    def test_dict_with_zookeeper_replica_and_args(self):
        result = _serialize_clickhouse_engine({
            "name": "ReplicatedMergeTree",
            "args": ["/table"],
            "zookeeper_path": "/zk/path",
            "replica_name": "r1",
        })
        assert result == ("ReplicatedMergeTree", "/zk/path", "r1", "/table")

    def test_object_with_name(self):
        obj = SimpleNamespace(name="MergeTree")
        assert _serialize_clickhouse_engine(obj) == "MergeTree"

    def test_object_with_name_and_args(self):
        obj = SimpleNamespace(name="ReplicatedMergeTree", zookeeper_path=None, replica_name=None, args=("/zk",))
        result = _serialize_clickhouse_engine(obj)
        assert result == ("ReplicatedMergeTree", "/zk")

    def test_object_with_zookeeper_path(self):
        obj = SimpleNamespace(name="ReplicatedMergeTree", zookeeper_path="/zk/path", replica_name=None, args=())
        result = _serialize_clickhouse_engine(obj)
        assert result == ("ReplicatedMergeTree", "/zk/path")

    def test_object_with_replica_name(self):
        obj = SimpleNamespace(name="ReplicatedMergeTree", zookeeper_path=None, replica_name="r1", args=())
        result = _serialize_clickhouse_engine(obj)
        assert result == ("ReplicatedMergeTree", "r1")

    def test_string_fallback(self):
        assert _serialize_clickhouse_engine("MergeTree") == "MergeTree"


class TestPickClickhouseCodec:
    def test_none(self):
        assert _pick_clickhouse_codec(None) is None

    def test_empty_string(self):
        assert _pick_clickhouse_codec("") is None

    def test_whitespace_only(self):
        assert _pick_clickhouse_codec("   ") is None

    def test_comma_only(self):
        assert _pick_clickhouse_codec(",") is None

    def test_lz4_only_default(self):
        # Default LZ4 is not a meaningful explicit codec.
        assert _pick_clickhouse_codec("LZ4") is None

    def test_none_default(self):
        assert _pick_clickhouse_codec("NONE") is None

    def test_lz4_with_other(self):
        result = _pick_clickhouse_codec("LZ4, ZSTD")
        assert result == "ZSTD"

    def test_lz4_case_insensitive(self):
        result = _pick_clickhouse_codec("lz4, ZSTD(3)")
        assert result == "ZSTD(3)"

    def test_multiple_non_default(self):
        # The full codec chain is preserved in order.
        result = _pick_clickhouse_codec("ZSTD, Delta")
        assert result == "ZSTD, Delta"

    def test_multiple_with_parentheses(self):
        result = _pick_clickhouse_codec("CODEC(Delta(8), ZSTD(1))")
        assert result == "Delta(8), ZSTD(1)"

    def test_lz4_with_leading_trailing_spaces(self):
        result = _pick_clickhouse_codec("  LZ4 , ZSTD(3)  ")
        assert result == "ZSTD(3)"


class TestCheckChEngineRecreateAllowed:
    def test_no_reasons_ok(self):
        _check_ch_engine_recreate_allowed({}, {}, "t")  # should not raise

    def test_materialized_view_current(self):
        with pytest.raises(ValueError, match="materialized view") as exc:
            _check_ch_engine_recreate_allowed(
                {"ch_object_type": "materialized_view", "ch_to_table": "target"},
                {},
                "mv_table",
            )
        assert "current" in str(exc.value)

    def test_materialized_view_new(self):
        with pytest.raises(ValueError, match="materialized view") as exc:
            _check_ch_engine_recreate_allowed(
                {},
                {"ch_object_type": "materialized_view", "ch_to_table": "target"},
                "mv_table",
            )
        assert "new" in str(exc.value)

    def test_to_table_on_both(self):
        with pytest.raises(ValueError, match="materialized view") as exc:
            _check_ch_engine_recreate_allowed(
                {"ch_object_type": "materialized_view", "ch_to_table": "t1"},
                {"ch_object_type": "materialized_view", "ch_to_table": "t2"},
                "mv_table",
            )
        assert "current" in str(exc.value)
        assert "new" in str(exc.value)

    def test_select_and_to(self):
        with pytest.raises(ValueError, match="SELECT statement"):
            _check_ch_engine_recreate_allowed(
                {"ch_select_statement": "SELECT * FROM t", "ch_to_table": "t"},
                {},
                "mv_table",
            )

    def test_select_and_to_on_new(self):
        with pytest.raises(ValueError, match="SELECT statement"):
            _check_ch_engine_recreate_allowed(
                {},
                {"ch_select_statement": "SELECT * FROM t", "ch_to_table": "t"},
                "mv_table",
            )


class TestClickhouseEngineFamily:
    def test_none_engine(self):
        assert _clickhouse_engine_family(None) is None

    def test_empty_tuple(self):
        assert _clickhouse_engine_family(()) is None

    def test_tuple_with_name(self):
        result = _clickhouse_engine_family({"name": "MergeTree"})
        assert result == "mergetree"

    def test_tuple_with_replicated_name(self):
        result = _clickhouse_engine_family({"name": "ReplicatedMergeTree", "args": ["/zk", "r1"]})
        assert result == "replicatedmergetree"

    def test_whitespace_name(self):
        assert _clickhouse_engine_family("   ") is None

    def test_string_no_args(self):
        result = _clickhouse_engine_family("MergeTree")
        assert result == "mergetree"

    def test_string_with_parenthesis(self):
        result = _clickhouse_engine_family("ReplicatedMergeTree('/zk/path', 'r1')")
        assert result == "replicatedmergetree"

    def test_mixed_case(self):
        result = _clickhouse_engine_family("MERGE TREE")
        assert result == "merge tree"


class TestDiffChColumnExtras:
    def test_no_diff(self):
        snap = {"ch_codec": "LZ4"}
        model = {"ch_codec": "LZ4"}
        up, rb = [], []
        _diff_ch_column_extras(snap, model, "t", "c", up, rb)
        assert up == []
        assert rb == []

    def test_diff_detected(self):
        snap = {"ch_codec": "LZ4"}
        model = {"ch_codec": "ZSTD"}
        up, rb = [], []
        _diff_ch_column_extras(snap, model, "t", "c", up, rb)
        assert up == [{
            "type": "alter_ch_column",
            "table": "t",
            "column": "c",
            "from_ch_column": snap,
            "to_ch_column": model,
        }]
        assert rb == [{
            "type": "alter_ch_column",
            "table": "t",
            "column": "c",
            "from_ch_column": model,
            "to_ch_column": snap,
        }]
