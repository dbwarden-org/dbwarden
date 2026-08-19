from __future__ import annotations

from dataclasses import dataclass
from typing import Any, TypedDict
from typing_extensions import Unpack


class KafkaSettings(TypedDict, total=False):
    kafka_broker_list: str
    kafka_topic_list: str
    kafka_group_name: str
    kafka_format: str
    kafka_row_delimiter: str
    kafka_schema: str
    kafka_num_consumers: int
    kafka_max_block_size: int
    kafka_skip_broken_messages: int
    kafka_commit_every_batch: bool
    kafka_client_id: str
    kafka_poll_timeout_ms: int
    kafka_flush_interval_ms: int
    kafka_thread_per_consumer: bool
    kafka_handle_error_mode: str


class S3Settings(TypedDict, total=False):
    s3_access_key_id: str
    s3_secret_access_key: str
    s3_format: str
    s3_compression: str
    s3_compression_method: str
    s3_compression_level: int


class S3QueueSettings(TypedDict, total=False):
    s3queue_buckets: int
    s3queue_processing_threads: int
    s3queue_tracking_poll_timeout_ms: int
    s3queue_tracking_window_ms: int
    s3queue_last_processed_node: str


class RabbitMQSettings(TypedDict, total=False):
    rabbitmq_host: str
    rabbitmq_port: int
    rabbitmq_virtual_host: str
    rabbitmq_username: str
    rabbitmq_password: str
    rabbitmq_routing_key_list: str
    rabbitmq_exchange_name: str
    rabbitmq_format: str
    rabbitmq_exchange_type: str
    rabbitmq_routing_key: str
    rabbitmq_num_consumers: int
    rabbitmq_max_block_size: int
    rabbitmq_flush_interval_ms: int
    rabbitmq_skip_broken_messages: int
    rabbitmq_commit_every_batch: bool
    rabbitmq_queue_base: str
    rabbitmq_persistent: bool


class NATSSettings(TypedDict, total=False):
    nats_url: str
    nats_subjects: str
    nats_format: str
    nats_num_consumers: int
    nats_max_block_size: int
    nats_skip_broken_messages: int
    nats_flush_interval_ms: int
    nats_commit_every_batch: bool
    nats_username: str
    nats_password: str


class MySQLSettings(TypedDict, total=False):
    mysql_host: str
    mysql_port: int
    mysql_database: str
    mysql_user: str
    mysql_password: str


class PostgreSQLSettings(TypedDict, total=False):
    postgresql_host: str
    postgresql_port: int
    postgresql_database: str
    postgresql_user: str
    postgresql_password: str
    postgresql_schema: str


class MongoDBSettings(TypedDict, total=False):
    mongodb_host: str
    mongodb_port: int
    mongodb_user: str
    mongodb_password: str
    mongodb_database: str
    mongodb_collection: str
    mongodb_options: str


class RedisSettings(TypedDict, total=False):
    redis_host: str
    redis_port: int
    redis_password: str
    redis_storage: str


class URLSettings(TypedDict, total=False):
    url: str
    url_format: str


class MergeTreeSettings(TypedDict, total=False):
    """Typed setting names for ClickHouse MergeTree-family tables.

    All values are rendered as strings before reaching the server;
    boolean values are automatically converted to ``"0"`` / ``"1"``.
    """
    index_granularity: int
    index_granularity_bytes: int
    min_index_granularity_bytes: int
    enable_mixed_granularity_parts: bool
    ttl_only_drop_parts: bool
    merge_with_ttl_timeout: int
    merge_with_recompression_ttl_timeout: int
    write_final_mark: bool
    merge_max_block_size: int
    max_part_loading_threads: int
    max_parts_in_total: int
    replicated_deduplication_window: int
    replicated_deduplication_window_seconds: int
    replicated_can_become_leader: bool
    min_bytes_for_wide_part: int
    min_rows_for_wide_part: int
    max_parts_in_memory: int
    max_bytes_to_merge_at_max_space_in_pool: int
    min_bytes_to_use_direct_io: int
    merge_tree_clear_old_broken_detached: bool
    parts_to_throw_insert: int
    parts_to_delay_insert: int
    inactive_parts_to_throw_in_insert: int
    inactive_parts_to_delay_in_insert: int
    max_delay_to_insert: int
    max_suspicious_broken_parts: int


def _render_settings(s: dict[str, str] | MergeTreeSettings | None) -> dict[str, str] | None:
    """Normalize settings dict: convert bools to ``"0"``/``"1"``, ints to strings."""
    if s is None:
        return None
    rendered: dict[str, str] = {}
    for k, v in s.items():
        if isinstance(v, bool):
            rendered[k] = "1" if v else "0"
        else:
            rendered[k] = str(v)
    return rendered


@dataclass
class ChEngineSpec:
    name: str
    args: tuple[str, ...] = ()
    zookeeper_path: str | None = None
    replica_name: str | None = None
    settings: dict[str, str] | None = None

    def __post_init__(self) -> None:
        if isinstance(self.args, str):
            self.args = (self.args,)
        elif not isinstance(self.args, tuple):
            self.args = tuple(self.args)

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"name": self.name}
        if self.args:
            d["args"] = self.args
        if self.zookeeper_path is not None:
            d["zookeeper_path"] = self.zookeeper_path
        if self.replica_name is not None:
            d["replica_name"] = self.replica_name
        if self.settings is not None:
            d["settings"] = dict(self.settings)
        return d

    @classmethod
    def from_dict(cls, d: dict) -> ChEngineSpec:
        return cls(
            name=d["name"],
            args=tuple(d.get("args", [])),
            zookeeper_path=d.get("zookeeper_path"),
            replica_name=d.get("replica_name"),
            settings=d.get("settings"),
        )

    @classmethod
    def from_engine_string(cls, engine_str: str) -> ChEngineSpec:
        name, *rest = engine_str.split("(", 1)
        name = name.strip()
        inner = rest[0].rstrip(")") if rest else ""
        if not inner:
            return cls(name)
        parts = _split_engine_args(inner)
        zk_path = None
        replica = None
        tail_args = list(parts)
        if name.lower().startswith("replicated") or "zookeeper" in name.lower():
            if tail_args:
                zk_path = tail_args.pop(0).strip("'\" ")
            if tail_args:
                replica = tail_args.pop(0).strip("'\" ")
        return cls(name, args=tuple(tail_args), zookeeper_path=zk_path, replica_name=replica)


def _split_engine_args(inner: str) -> list[str]:
    args: list[str] = []
    depth = 0
    current: list[str] = []
    in_quote: str | None = None
    for ch in inner:
        if in_quote:
            current.append(ch)
            if ch == in_quote:
                in_quote = None
        elif ch in ("'", '"'):
            current.append(ch)
            in_quote = ch
        elif ch in ("(", "["):
            depth += 1
            current.append(ch)
        elif ch in (")", "]"):
            depth -= 1
            current.append(ch)
        elif ch == "," and depth == 0:
            args.append("".join(current).strip())
            current = []
        else:
            current.append(ch)
    rest = "".join(current).strip()
    if rest:
        args.append(rest)
    return args


def merge_tree(
    *,
    settings: MergeTreeSettings | None = None,
) -> ChEngineSpec:
    return ChEngineSpec("MergeTree", settings=_render_settings(settings))


def replacing_merge_tree(
    version_col: str | None = None,
    *,
    settings: MergeTreeSettings | None = None,
) -> ChEngineSpec:
    args = (version_col,) if version_col else ()
    return ChEngineSpec("ReplacingMergeTree", args=args, settings=_render_settings(settings))


def replicated_merge_tree(
    zookeeper_path: str,
    replica_name: str,
    *args: str,
    settings: MergeTreeSettings | None = None,
) -> ChEngineSpec:
    return ChEngineSpec(
        "ReplicatedMergeTree",
        args=args,
        zookeeper_path=zookeeper_path,
        replica_name=replica_name,
        settings=_render_settings(settings),
    )


def summing_merge_tree(
    *columns: str,
    settings: MergeTreeSettings | None = None,
) -> ChEngineSpec:
    return ChEngineSpec("SummingMergeTree", args=columns, settings=_render_settings(settings))


def aggregating_merge_tree(
    *,
    settings: MergeTreeSettings | None = None,
) -> ChEngineSpec:
    return ChEngineSpec("AggregatingMergeTree", settings=_render_settings(settings))


def collapsing_merge_tree(
    sign_col: str,
    *,
    settings: MergeTreeSettings | None = None,
) -> ChEngineSpec:
    """``CollapsingMergeTree(sign_col)``: rows with opposite sign cancel on merge."""
    return ChEngineSpec("CollapsingMergeTree", args=(sign_col,), settings=_render_settings(settings))


def versioned_collapsing_merge_tree(
    sign_col: str,
    version_col: str,
    *,
    settings: MergeTreeSettings | None = None,
) -> ChEngineSpec:
    """``VersionedCollapsingMergeTree(sign_col, version_col)``."""
    return ChEngineSpec(
        "VersionedCollapsingMergeTree", args=(sign_col, version_col), settings=_render_settings(settings),
    )


def graphite_merge_tree(
    config_section: str = "default",
    *,
    settings: MergeTreeSettings | None = None,
) -> ChEngineSpec:
    """``GraphiteMergeTree(config_section)``: for graphite rollup data."""
    return ChEngineSpec("GraphiteMergeTree", args=(config_section,), settings=_render_settings(settings))


def distributed(
    cluster: str,
    database: str,
    table: str,
    sharding_key: str | None = None,
    *,
    policy_name: str | None = None,
    settings: MergeTreeSettings | None = None,
) -> ChEngineSpec:
    """``Distributed(cluster, database, table[, sharding_key[, policy_name]])``.

    .. note::
       ``cluster`` here is the *engine's destination cluster*, not the DDL
       propagation cluster managed by ``ClusterContext``.  They are distinct
       concepts and should not be conflated.
    """
    args: list[str] = [cluster, database, table]
    if sharding_key is not None:
        args.append(sharding_key)
    if policy_name is not None:
        args.append(policy_name)
    return ChEngineSpec("Distributed", args=tuple(args), settings=_render_settings(settings))


def kafka(
    *,
    named_collection: str | None = None,
    broker_list: str | None = None,
    topic_list: str | None = None,
    group_name: str | None = None,
    format: str | None = None,
    num_consumers: int | None = None,
) -> ChEngineSpec:
    """ENGINE = Kafka.

    PREFER named_collection for credentials. Inline creds are declare-only.
    """
    if named_collection is None and broker_list is None:
        raise ValueError("kafka() requires named_collection or broker_list")
    return ChEngineSpec("Kafka")


def s3(
    *,
    named_collection: str | None = None,
    path: str | None = None,
    format: str | None = None,
    compression: str | None = None,
    access_key_id: str | None = None,
    secret_access_key: str | None = None,
) -> ChEngineSpec:
    """ENGINE = S3.  PREFER named_collection for credentials."""
    return ChEngineSpec("S3")


def s3_queue(
    *,
    named_collection: str | None = None,
    path: str | None = None,
    format: str | None = None,
    compression: str | None = None,
) -> ChEngineSpec:
    """ENGINE = S3Queue.  PREFER named_collection for credentials."""
    return ChEngineSpec("S3Queue")


def rabbitmq(
    *,
    named_collection: str | None = None,
    host: str | None = None,
    format: str | None = None,
    exchange_name: str | None = None,
    routing_key: str | None = None,
) -> ChEngineSpec:
    """ENGINE = RabbitMQ.  PREFER named_collection for credentials."""
    return ChEngineSpec("RabbitMQ")


def nats(
    *,
    named_collection: str | None = None,
    url: str | None = None,
    subjects: str | None = None,
    format: str | None = None,
) -> ChEngineSpec:
    """ENGINE = NATS.  PREFER named_collection for credentials."""
    return ChEngineSpec("NATS")


def mysql_engine(
    host: str,
    port: int,
    database: str,
    table: str,
    user: str,
    password: str,
) -> ChEngineSpec:
    """ENGINE = MySQL(host:port, database, table, user, password)."""
    return ChEngineSpec("MySQL", args=(f"{host}:{port}", database, table, user, password))


def postgresql_engine(
    host: str,
    port: int,
    database: str,
    table: str,
    user: str,
    password: str,
) -> ChEngineSpec:
    """ENGINE = PostgreSQL(host:port, database, table, user, password)."""
    return ChEngineSpec("PostgreSQL", args=(f"{host}:{port}", database, table, user, password))


def mongodb(
    host: str,
    port: int,
    database: str,
    table: str,
    user: str,
    password: str,
) -> ChEngineSpec:
    """ENGINE = MongoDB(host:port, database, table, user, password)."""
    return ChEngineSpec("MongoDB", args=(f"{host}:{port}", database, table, user, password))


def redis(
    host: str,
    port: int,
    password: str,
    storage: str,
) -> ChEngineSpec:
    """ENGINE = Redis(host:port, password, storage)."""
    return ChEngineSpec("Redis", args=(f"{host}:{port}", password, storage))


def url_engine(url: str, format: str) -> ChEngineSpec:
    """ENGINE = URL(url, format)."""
    return ChEngineSpec("URL", args=(url, format))


def file_engine(format: str, path: str | None = None) -> ChEngineSpec:
    """ENGINE = File(format[, path])."""
    args = (format,) if path is None else (format, path)
    return ChEngineSpec("File", args=args)


def hdfs(uri: str, format: str) -> ChEngineSpec:
    """ENGINE = HDFS(uri, format)."""
    return ChEngineSpec("HDFS", args=(uri, format))


def null() -> ChEngineSpec:
    """ENGINE = Null: discards all writes. Common as an MV source."""
    return ChEngineSpec("Null")


def memory() -> ChEngineSpec:
    """ENGINE = Memory: non-persistent, RAM-only."""
    return ChEngineSpec("Memory")


def merge(db_regex: str, table_regex: str) -> ChEngineSpec:
    """ENGINE = Merge(db, tables_regexp): read-only union over matching tables."""
    return ChEngineSpec("Merge", args=(db_regex, table_regex))


def set_engine() -> ChEngineSpec:
    """ENGINE = Set: for use as the right side of IN."""
    return ChEngineSpec("Set")


def join_engine(strictness: str, kind: str, *key_cols: str) -> ChEngineSpec:
    """ENGINE = Join(strictness, kind, keys...)."""
    return ChEngineSpec("Join", args=(strictness, kind, *key_cols))


def dictionary_engine(dict_name: str) -> ChEngineSpec:
    """ENGINE = Dictionary(name): exposes a dictionary as a table."""
    return ChEngineSpec("Dictionary", args=(dict_name,))


def log() -> ChEngineSpec:
    """ENGINE = Log."""
    return ChEngineSpec("Log")


def tiny_log() -> ChEngineSpec:
    """ENGINE = TinyLog."""
    return ChEngineSpec("TinyLog")


def stripe_log() -> ChEngineSpec:
    """ENGINE = StripeLog."""
    return ChEngineSpec("StripeLog")


def buffer(
    database: str,
    table: str,
    num_layers: int,
    min_time: int,
    max_time: int,
    min_rows: int,
    max_rows: int,
    min_bytes: int,
    max_bytes: int,
    *,
    settings: MergeTreeSettings | None = None,
) -> ChEngineSpec:
    """``Buffer(database, table, num_layers, min_time, max_time, min_rows, max_rows, min_bytes, max_bytes)``.

    Accumulates writes and flushes to the target table based on time/rows/bytes thresholds.
    """
    return ChEngineSpec(
        "Buffer",
        args=(database, table, str(num_layers), str(min_time), str(max_time),
               str(min_rows), str(max_rows), str(min_bytes), str(max_bytes)),
        settings=_render_settings(settings),
    )
