from sqlalchemy import Column, Int64, String, Date, DateTime
from sqlalchemy.orm import declarative_base
from dbwarden import CHTableMeta, CHColumnMeta, ChEngineSpec, ChIndexSpec, ProjectionSpec

Base = declarative_base()


# ── CHTableMeta ────────────────────────────────────────────────
# ClickHouse-specific table metadata.  Controls the MergeTree
# engine configuration, partitioning, ordering, TTL, projections,
# and skip indexes, all of which are ClickHouse-native DDL
# concepts that dbwarden translates into CREATE TABLE statements.
#
# ChEngineSpec     : MergeTree, ReplicatedMergeTree, etc.
# ChIndexSpec      : skip indexes (bloom_filter, minmax, etc.)
# ProjectionSpec   : table projections (pre-computed aggregations)
# CHColumnMeta     : per-column codecs (ZSTD, LZ4, etc.)


class PageView(Base):
    __tablename__ = "page_views"

    id = Column(Int64, primary_key=True)
    url = Column(String, nullable=False)
    user_id = Column(Int64, nullable=True)
    viewed_at = Column(DateTime, nullable=False)
    event_date = Column(Date, nullable=False)

    class Meta(CHTableMeta):
        # Engine family and parameters
        ch_engine = ChEngineSpec("MergeTree")

        # ORDER BY is required for MergeTree tables.  Defines the
        # sort key, which determines data locality for range scans.
        ch_order_by = ["event_date", "id"]

        # PARTITION BY divides data into partitions for efficient
        # partition-level DROP and TTL operations.
        ch_partition_by = "toYYYYMM(event_date)"

        # TTL: data older than 1 year is automatically deleted.
        ch_ttl = ["event_date + toIntervalYear(1) DELETE"]

        # Projections are pre-computed aggregations stored within
        # the table at INSERT time.  The query engine automatically
        # routes qualifying queries to the projection data.
        ch_projections = [
            ProjectionSpec(
                "by_url",
                "SELECT url, count() GROUP BY url",
            ),
        ]

        # Skip indexes (bloom_filter, minmax, set, ngrambf_v1, etc.)
        # speed up queries that would otherwise scan all granules.
        ch_indexes = [
            ChIndexSpec(
                "ix_url_bloom",
                ["url"],
                type="bloom_filter",
                granularity=1,
            ),
        ]

        # Column codecs: ClickHouse lets you specify compression
        # per column.  ZSTD(3) balances speed and compression ratio.
        class url(CHColumnMeta):
            ch_codec = "ZSTD(3)"
