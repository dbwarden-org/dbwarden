from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from dbwarden.databases.clickhouse.field import ChFieldSpec, field

import sys as _sys
ch = _sys.modules[__name__]
from dbwarden.databases.clickhouse.engine import (
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
    KafkaSettings,
    MergeTreeSettings,
    NATSSettings,
    RabbitMQSettings,
    RedisSettings,
    S3QueueSettings,
    S3Settings,
    URLSettings,
)
from dbwarden.databases.clickhouse.index import skip_index
from dbwarden.databases.clickhouse.projection import projection
from dbwarden.databases.clickhouse.agg import AggExpr, ChAggStateType, agg, ch_agg_state
from dbwarden.databases.clickhouse.cluster import ClusterMode
from dbwarden.databases.clickhouse.data_op import DataOp, data_op
from dbwarden.databases.clickhouse import data_ops as _data_ops_module
data_ops = _data_ops_module
from dbwarden.databases.clickhouse.data_ops import populate as data_ops_populate
from dbwarden.databases.clickhouse.dictionary import DictSpec, dictionary
from dbwarden.databases.clickhouse.materialized_view import (
    AggregatingViewSpec,
    aggregating_view,
    materialized_view,
    MaterializedViewSpec,
)
from dbwarden.databases.clickhouse.named_collection import NamedCollectionSpec, named_collection
from dbwarden.databases.clickhouse.rbac import (
    ChGrantSpec, ChQuotaSpec, ChRoleSpec,
    ChRowPolicySpec, ChSettingsProfileSpec, ChUserSpec,
)
from dbwarden.databases.clickhouse.compiler import render_expr, render_expr_list, column_name_from_expr
from dbwarden.databases.clickhouse.raw import ChRaw, ch_raw
from dbwarden.databases.clickhouse.engine import ChEngineSpec
from dbwarden.databases.clickhouse.projection import ProjectionSpec
from dbwarden.databases.clickhouse.views import (
    ChView, MaterializedView, AggregatingView,
    _validate_view_class, derive_agg_target_columns,
    get_all_ch_views, ch_view_tables_from_models,
)
from dbwarden.schema.table_meta import CHColumnMeta, CHTableMeta, CHViewMeta


@dataclass
class ChIndexSpec:
    """Typed spec for a ClickHouse skip index in ``class Meta``.

    Example::

        from dbwarden import ChIndexSpec

        class Meta(CHTableMeta):
            ch_indexes = [
                ChIndexSpec("ix_payload", ["payload"],
                    type="bloom_filter", granularity=1),
            ]
    """
    name: str
    columns: list[str]
    type: Literal["minmax", "set", "bloom_filter", "ngrambf_v1", "tokenbf_v1", "hypothesis"] | str = "minmax"
    granularity: int = 1
    expr: str | None = None

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "name": self.name,
            "columns": list(self.columns),
            "clickhouse_type": self.type,
            "clickhouse_granularity": self.granularity,
        }
        if self.expr is not None:
            d["expr"] = self.expr
        return d

    @classmethod
    def from_dict(cls, d: dict) -> ChIndexSpec:
        return cls(
            name=d["name"],
            columns=list(d.get("columns", [])),
            type=d.get("clickhouse_type") or d.get("type", ""),
            granularity=d.get("clickhouse_granularity") or d.get("granularity", 1),
            expr=d.get("expr"),
        )


@dataclass
class ChTableSpec:
    """Typed spec for a ClickHouse table.

    Expression fields (order_by, partition_by, etc.) accept
    :class:`~sqlalchemy.sql.ColumnElement`, :class:`ChRaw`, or plain ``str``.
    When a ``ColumnElement`` is provided, it is rendered to ClickHouse SQL via
    :func:`render_expr`.
    """
    engine: ChEngineSpec | str | None = None
    order_by: list[Any] | Any | None = None
    primary_key: list[Any] | Any | None = None
    partition_by: Any | None = None
    sample_by: Any | None = None
    ttl: list[Any] | Any | None = None
    settings: MergeTreeSettings | None = None
    projections: list[ProjectionSpec] | None = None
    indexes: list[ChIndexSpec] | None = None
    zookeeper_path: str | None = None
    replica_name: str | None = None
    select_statement: Any | None = None
    to_table: str | None = None


def ch_table(
    *,
    engine: ChEngineSpec | str | None = None,
    order_by: Any = None,
    primary_key: Any = None,
    partition_by: Any = None,
    sample_by: Any = None,
    ttl: Any = None,
    settings: MergeTreeSettings | None = None,
    projections: list[ProjectionSpec] | None = None,
    indexes: list[ChIndexSpec] | None = None,
    zookeeper_path: str | None = None,
    replica_name: str | None = None,
) -> ChTableSpec:
    """Declare a ClickHouse table.

    Expression fields (order_by, partition_by, etc.) accept
    :class:`~sqlalchemy.sql.ColumnElement`, :class:`ChRaw`, or plain ``str``.

    Returns a ``ChTableSpec``.  Use in ``class Meta``::

        class Meta(CHTableMeta):
            ch = ch_table(
                engine=merge_tree(),
                order_by=[Events.event_date],
                partition_by=func.toYYYYMM(Events.event_date),
            )
    """
    return ChTableSpec(
        engine=engine,
        order_by=order_by,
        primary_key=primary_key,
        partition_by=partition_by,
        sample_by=sample_by,
        ttl=ttl,
        settings=settings,
        projections=projections,
        indexes=indexes,
        zookeeper_path=zookeeper_path,
        replica_name=replica_name,
    )


__all__ = [
    "AggregatingView",
    "AggregatingViewSpec",
    "AggExpr",
    "ChAggStateType",
    "ChEngineSpec",
    "ChFieldSpec",
    "ChGrantSpec",
    "ChIndexSpec",
    "ChQuotaSpec",
    "ChRaw",
    "ChRoleSpec",
    "ChRowPolicySpec",
    "ChSettingsProfileSpec",
    "ChTableSpec",
    "ChUserSpec",
    "CHColumnMeta",
    "CHTableMeta",
    "CHViewMeta",
    "ChView",
    "ClusterMode",
    "DataOp",
    "DictSpec",
    "KafkaSettings",
    "MaterializedView",
    "MaterializedViewSpec",
    "MergeTreeSettings",
    "NamedCollectionSpec",
    "NATSSettings",
    "ProjectionSpec",
    "RabbitMQSettings",
    "RedisSettings",
    "S3QueueSettings",
    "S3Settings",
    "URLSettings",
    "agg",
    "aggregating_merge_tree",
    "aggregating_view",
    "buffer",
    "ch",
    "ch_agg_state",
    "ch_raw",
    "ch_table",
    "ch_view_tables_from_models",
    "collapsing_merge_tree",
    "column_name_from_expr",
    "data_op",
    "data_ops",
    "data_ops_populate",
    "derive_agg_target_columns",
    "dictionary",
    "dictionary_engine",
    "distributed",
    "field",
    "file_engine",
    "get_all_ch_views",
    "graphite_merge_tree",
    "hdfs",
    "join_engine",
    "kafka",
    "log",
    "materialized_view",
    "memory",
    "merge",
    "merge_tree",
    "mongodb",
    "mysql_engine",
    "named_collection",
    "nats",
    "null",
    "postgresql_engine",
    "projection",
    "rabbitmq",
    "redis",
    "render_expr",
    "render_expr_list",
    "replicated_merge_tree",
    "replacing_merge_tree",
    "s3",
    "s3_queue",
    "set_engine",
    "skip_index",
    "stripe_log",
    "summing_merge_tree",
    "tiny_log",
    "url_engine",
    "versioned_collapsing_merge_tree",
]
