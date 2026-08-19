import pytest

from dbwarden.databases.clickhouse.engine import (
    ChEngineSpec,
    MergeTreeSettings,
    _split_engine_args,
    _render_settings,
    aggregating_merge_tree,
    buffer,
    collapsing_merge_tree,
    dictionary_engine,
    distributed,
    file_engine,
    graphite_merge_tree,
    hdfs,
    join_engine,
    kafka,
    log,
    memory,
    merge,
    merge_tree,
    mongodb,
    mysql_engine,
    nats,
    null,
    postgresql_engine,
    rabbitmq,
    redis,
    replicated_merge_tree,
    replacing_merge_tree,
    s3,
    s3_queue,
    set_engine,
    stripe_log,
    summing_merge_tree,
    tiny_log,
    url_engine,
    versioned_collapsing_merge_tree,
)
from dbwarden.databases.clickhouse.compiler import column_name_from_expr


class TestSplitEngineArgs:
    def test_empty_string(self):
        assert _split_engine_args("") == []

    def test_single_arg(self):
        assert _split_engine_args("foo") == ["foo"]

    def test_multiple_args(self):
        assert _split_engine_args("a, b, c") == ["a", "b", "c"]

    def test_args_with_parens(self):
        assert _split_engine_args("(a, b), c") == ["(a, b)", "c"]

    def test_args_with_quotes(self):
        assert _split_engine_args("'/zk/path', 'replica'") == ["'/zk/path'", "'replica'"]


class TestExpressionColumnNames:
    def test_lowercase_as_alias(self):
        assert column_name_from_expr("toDate(event_time) as day") == "day"


class TestChEngineSpec:
    def test_basic_engine(self):
        spec = ChEngineSpec("MergeTree")
        assert spec.name == "MergeTree"
        assert spec.args == ()
        assert spec.zookeeper_path is None

    def test_with_args(self):
        spec = ChEngineSpec("SummingMergeTree", args=("col1", "col2"))
        assert spec.args == ("col1", "col2")

    def test_args_normalized_in_post_init(self):
        spec = ChEngineSpec("SummingMergeTree", args=["col1", "col2"])
        assert spec.args == ("col1", "col2")

    def test_args_string_in_post_init(self):
        spec = ChEngineSpec("Test", args="single")
        assert spec.args == ("single",)

    def test_to_dict_basic(self):
        spec = ChEngineSpec("MergeTree")
        assert spec.to_dict() == {"name": "MergeTree"}

    def test_to_dict_with_args(self):
        spec = ChEngineSpec("SummingMergeTree", args=("col1",))
        assert spec.to_dict() == {"name": "SummingMergeTree", "args": ("col1",)}

    def test_to_dict_with_zookeeper(self):
        spec = ChEngineSpec("ReplicatedMergeTree", zookeeper_path="/zk/path", replica_name="r1")
        d = spec.to_dict()
        assert d["zookeeper_path"] == "/zk/path"
        assert d["replica_name"] == "r1"

    def test_to_dict_includes_settings(self):
        spec = ChEngineSpec("MergeTree", settings={"index_granularity": "8192"})
        d = spec.to_dict()
        assert d["settings"] == {"index_granularity": "8192"}

    def test_from_dict(self):
        d = {"name": "MergeTree", "args": ("col1",)}
        spec = ChEngineSpec.from_dict(d)
        assert spec.name == "MergeTree"
        assert spec.args == ("col1",)

    def test_from_dict_preserves_settings_key(self):
        spec = ChEngineSpec.from_dict({"name": "MergeTree", "settings": {"index_granularity": "8192"}})
        assert spec.name == "MergeTree"
        assert spec.settings == {"index_granularity": "8192"}

    def test_from_engine_string_basic(self):
        spec = ChEngineSpec.from_engine_string("MergeTree()")
        assert spec.name == "MergeTree"

    def test_from_engine_string_with_args(self):
        spec = ChEngineSpec.from_engine_string("SummingMergeTree(col1, col2)")
        assert spec.name == "SummingMergeTree"
        assert spec.args == ("col1", "col2")

    def test_from_engine_string_replicated(self):
        spec = ChEngineSpec.from_engine_string("ReplicatedMergeTree('/zk/path', 'r1', ver)")
        assert spec.name == "ReplicatedMergeTree"
        assert spec.zookeeper_path == "/zk/path"
        assert spec.replica_name == "r1"
        assert spec.args == ("ver",)


class TestEngineFactories:
    def test_merge_tree(self):
        spec = merge_tree()
        assert spec.name == "MergeTree"

    def test_merge_tree_with_settings(self):
        spec = merge_tree(settings={"a": "b"})
        assert spec.settings == {"a": "b"}

    def test_engine_spec_to_dict_from_dict_round_trip(self):
        spec = merge_tree()
        assert ChEngineSpec.from_dict(spec.to_dict()) == spec

    def test_replacing_merge_tree(self):
        spec = replacing_merge_tree()
        assert spec.name == "ReplacingMergeTree"
        assert spec.args == ()

    def test_replacing_merge_tree_with_version(self):
        spec = replacing_merge_tree("ver")
        assert spec.args == ("ver",)

    def test_replicated_merge_tree(self):
        spec = replicated_merge_tree("/zk/path", "r1", "ver")
        assert spec.name == "ReplicatedMergeTree"
        assert spec.zookeeper_path == "/zk/path"
        assert spec.replica_name == "r1"
        assert spec.args == ("ver",)

    def test_summing_merge_tree(self):
        spec = summing_merge_tree("col1", "col2")
        assert spec.name == "SummingMergeTree"
        assert spec.args == ("col1", "col2")

    def test_aggregating_merge_tree(self):
        spec = aggregating_merge_tree()
        assert spec.name == "AggregatingMergeTree"

    def test_collapsing_merge_tree(self):
        spec = collapsing_merge_tree("sign")
        assert spec.name == "CollapsingMergeTree"
        assert spec.args == ("sign",)

    def test_versioned_collapsing_merge_tree(self):
        spec = versioned_collapsing_merge_tree("sign", "ver")
        assert spec.name == "VersionedCollapsingMergeTree"
        assert spec.args == ("sign", "ver")

    def test_graphite_merge_tree(self):
        spec = graphite_merge_tree("rollup")
        assert spec.name == "GraphiteMergeTree"
        assert spec.args == ("rollup",)

    def test_distributed(self):
        spec = distributed("cluster_1", "db", "events")
        assert spec.name == "Distributed"
        assert spec.args == ("cluster_1", "db", "events")

    def test_distributed_with_sharding_key(self):
        spec = distributed("cluster_1", "db", "events", sharding_key="rand()")
        assert spec.args == ("cluster_1", "db", "events", "rand()")

    def test_distributed_with_policy(self):
        spec = distributed("c", "d", "t", policy_name="p1")
        assert spec.args == ("c", "d", "t", "p1")

    def test_buffer(self):
        spec = buffer("db", "target", 1, 10, 60, 100, 1000, 10000, 100000)
        assert spec.name == "Buffer"
        assert spec.args == ("db", "target", "1", "10", "60", "100", "1000", "10000", "100000")

    def test_null(self):
        spec = null()
        assert spec.name == "Null"

    def test_memory(self):
        spec = memory()
        assert spec.name == "Memory"

    def test_merge(self):
        spec = merge("db", ".*")
        assert spec.name == "Merge"
        assert spec.args == ("db", ".*")

    def test_set_engine(self):
        spec = set_engine()
        assert spec.name == "Set"

    def test_join_engine(self):
        spec = join_engine("ALL", "INNER", "k1", "k2")
        assert spec.name == "Join"
        assert spec.args == ("ALL", "INNER", "k1", "k2")

    def test_dictionary_engine(self):
        spec = dictionary_engine("my_dict")
        assert spec.name == "Dictionary"
        assert spec.args == ("my_dict",)

    def test_log_family(self):
        assert log().name == "Log"
        assert tiny_log().name == "TinyLog"
        assert stripe_log().name == "StripeLog"

    def test_kafka(self):
        spec = kafka(broker_list="b:9092", topic_list="events", group_name="g1", format="JSONEachRow")
        assert spec.name == "Kafka"

    def test_kafka_requires_broker_or_named_collection(self):
        import pytest
        with pytest.raises(ValueError, match="requires named_collection or broker_list"):
            kafka()

    def test_s3(self):
        spec = s3(path="s3://bucket/data", format="Parquet")
        assert spec.name == "S3"

    def test_s3_queue(self):
        spec = s3_queue(path="s3://bucket/queue", format="JSONEachRow")
        assert spec.name == "S3Queue"

    def test_rabbitmq(self):
        spec = rabbitmq(host="localhost", format="JSONEachRow")
        assert spec.name == "RabbitMQ"

    def test_nats(self):
        spec = nats(url="nats://localhost:4222", subjects="events", format="JSONEachRow")
        assert spec.name == "NATS"

    def test_mysql_engine(self):
        spec = mysql_engine("localhost", 3306, "db", "tbl", "user", "pass")
        assert spec.name == "MySQL"
        assert spec.args[0] == "localhost:3306"

    def test_postgresql_engine(self):
        spec = postgresql_engine("localhost", 5432, "db", "tbl", "user", "pass")
        assert spec.name == "PostgreSQL"

    def test_mongodb_engine(self):
        spec = mongodb("localhost", 27017, "db", "tbl", "user", "pass")
        assert spec.name == "MongoDB"

    def test_redis_engine(self):
        spec = redis("localhost", 6379, "pass", "string")
        assert spec.name == "Redis"

    def test_url_engine(self):
        spec = url_engine("http://example.com/data", "JSONEachRow")
        assert spec.name == "URL"
        assert spec.args == ("http://example.com/data", "JSONEachRow")

    def test_file_engine(self):
        spec = file_engine("CSV")
        assert spec.name == "File"
        assert spec.args == ("CSV",)

    def test_file_engine_with_path(self):
        spec = file_engine("CSV", "data.csv")
        assert spec.args == ("CSV", "data.csv")

    def test_hdfs(self):
        spec = hdfs("hdfs://namenode:8020/data", "Parquet")
        assert spec.name == "HDFS"
        assert spec.args == ("hdfs://namenode:8020/data", "Parquet")


class TestSettingsRendering:
    def test_none(self):
        assert _render_settings(None) is None

    def test_empty(self):
        assert _render_settings({}) == {}

    def test_str_values_passthrough(self):
        assert _render_settings({"a": "b"}) == {"a": "b"}

    def test_int_to_str(self):
        assert _render_settings({"index_granularity": 8192}) == {"index_granularity": "8192"}

    def test_bool_to_0_1(self):
        assert _render_settings({"ttl_only_drop_parts": True}) == {"ttl_only_drop_parts": "1"}
        assert _render_settings({"ttl_only_drop_parts": False}) == {"ttl_only_drop_parts": "0"}
