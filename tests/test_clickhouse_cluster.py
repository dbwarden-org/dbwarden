"""Tests for ClickHouse cluster configuration and ON CLUSTER support."""
from __future__ import annotations

import pytest
from unittest.mock import MagicMock, patch

from dbwarden.databases.clickhouse.cluster import ClusterContext, ClusterMode
from dbwarden.engine.backends.clickhouse.cluster import ClusterableStatement


class TestClusterContext:
    """Tests for ClusterContext configuration."""

    def test_none_mode(self):
        ctx = ClusterContext(ClusterMode.NONE)
        assert ctx.mode == ClusterMode.NONE
        assert ctx.cluster_name is None

    def test_on_cluster_mode(self):
        ctx = ClusterContext(ClusterMode.ON_CLUSTER, cluster_name="prod")
        assert ctx.mode == ClusterMode.ON_CLUSTER
        assert ctx.cluster_name == "prod"

    def test_replicated_mode(self):
        ctx = ClusterContext(ClusterMode.REPLICATED)
        assert ctx.mode == ClusterMode.REPLICATED
        assert ctx.cluster_name is None

    def test_on_cluster_requires_name(self):
        with pytest.raises(ValueError, match="requires a cluster_name"):
            ClusterContext(ClusterMode.ON_CLUSTER)

    def test_cluster_name_only_valid_for_on_cluster(self):
        with pytest.raises(ValueError, match="only valid in ON_CLUSTER mode"):
            ClusterContext(ClusterMode.NONE, cluster_name="prod")

    def test_from_config_none(self):
        cfg = MagicMock()
        cfg.ch_cluster = None
        cfg.ch_replicated_database = False
        ctx = ClusterContext.from_config(cfg)
        assert ctx.mode == ClusterMode.NONE

    def test_from_config_on_cluster(self):
        cfg = MagicMock()
        cfg.ch_cluster = "production_cluster"
        cfg.ch_replicated_database = False
        ctx = ClusterContext.from_config(cfg)
        assert ctx.mode == ClusterMode.ON_CLUSTER
        assert ctx.cluster_name == "production_cluster"

    def test_from_config_replicated(self):
        cfg = MagicMock()
        cfg.ch_cluster = None
        cfg.ch_replicated_database = True
        ctx = ClusterContext.from_config(cfg)
        assert ctx.mode == ClusterMode.REPLICATED

    def test_from_config_mutual_exclusion(self):
        from dbwarden.exceptions import ConfigurationError
        cfg = MagicMock()
        cfg.ch_cluster = "cluster"
        cfg.ch_replicated_database = True
        with pytest.raises(ConfigurationError, match="mutually exclusive"):
            ClusterContext.from_config(cfg)


class TestClusterableStatement:
    """Tests for ClusterableStatement ON CLUSTER rendering."""

    def test_create_table_on_cluster(self):
        sql = "CREATE TABLE IF NOT EXISTS events (id UInt64) ENGINE = MergeTree() ORDER BY id"
        cs = ClusterableStatement.from_sql(sql)
        ctx = ClusterContext(ClusterMode.ON_CLUSTER, "prod")
        out = cs.render(ctx)
        assert "ON CLUSTER 'prod'" in out
        assert "CREATE TABLE IF NOT EXISTS events ON CLUSTER 'prod'" in out

    def test_create_view_on_cluster(self):
        sql = "CREATE MATERIALIZED VIEW IF NOT EXISTS mv TO target AS SELECT id FROM events"
        cs = ClusterableStatement.from_sql(sql)
        ctx = ClusterContext(ClusterMode.ON_CLUSTER, "prod")
        out = cs.render(ctx)
        assert "ON CLUSTER 'prod'" in out

    def test_create_dictionary_on_cluster(self):
        sql = "CREATE DICTIONARY IF NOT EXISTS dict (id UInt64, name String) PRIMARY KEY id LAYOUT(HASH()) SOURCE(HTTP(...)) LIFETIME(MIN 300 MAX 3600)"
        cs = ClusterableStatement.from_sql(sql)
        ctx = ClusterContext(ClusterMode.ON_CLUSTER, "prod")
        out = cs.render(ctx)
        assert "ON CLUSTER 'prod'" in out

    def test_alter_table_on_cluster(self):
        sql = "ALTER TABLE events ADD COLUMN IF NOT EXISTS new_col String"
        cs = ClusterableStatement.from_sql(sql)
        ctx = ClusterContext(ClusterMode.ON_CLUSTER, "prod")
        out = cs.render(ctx)
        assert "ON CLUSTER 'prod'" in out

    def test_drop_table_on_cluster(self):
        sql = "DROP TABLE IF EXISTS events"
        cs = ClusterableStatement.from_sql(sql)
        ctx = ClusterContext(ClusterMode.ON_CLUSTER, "prod")
        out = cs.render(ctx)
        assert "ON CLUSTER 'prod'" in out

    def test_rename_table_on_cluster(self):
        sql = "RENAME TABLE events TO events_v2"
        cs = ClusterableStatement.from_sql(sql)
        ctx = ClusterContext(ClusterMode.ON_CLUSTER, "prod")
        out = cs.render(ctx)
        assert "ON CLUSTER 'prod'" in out

    def test_detach_table_on_cluster(self):
        sql = "DETACH TABLE events"
        cs = ClusterableStatement.from_sql(sql)
        ctx = ClusterContext(ClusterMode.ON_CLUSTER, "prod")
        out = cs.render(ctx)
        assert "ON CLUSTER 'prod'" in out

    def test_attach_table_on_cluster(self):
        sql = "ATTACH TABLE events"
        cs = ClusterableStatement.from_sql(sql)
        ctx = ClusterContext(ClusterMode.ON_CLUSTER, "prod")
        out = cs.render(ctx)
        assert "ON CLUSTER 'prod'" in out

    def test_no_cluster_mode(self):
        sql = "CREATE TABLE IF NOT EXISTS events (id UInt64) ENGINE = MergeTree() ORDER BY id"
        cs = ClusterableStatement.from_sql(sql)
        ctx = ClusterContext(ClusterMode.NONE)
        out = cs.render(ctx)
        assert "ON CLUSTER" not in out
        assert out == sql

    def test_replicated_mode_no_cluster(self):
        sql = "CREATE TABLE IF NOT EXISTS events (id UInt64) ENGINE = MergeTree() ORDER BY id"
        cs = ClusterableStatement.from_sql(sql)
        ctx = ClusterContext(ClusterMode.REPLICATED)
        out = cs.render(ctx)
        assert "ON CLUSTER" not in out

    def test_no_double_spaces(self):
        sql = "CREATE TABLE IF NOT EXISTS events (id UInt64) ENGINE = MergeTree() ORDER BY id"
        cs = ClusterableStatement.from_sql(sql)
        ctx = ClusterContext(ClusterMode.ON_CLUSTER, "prod")
        out = cs.render(ctx)
        assert "  " not in out

    def test_backtick_quoted_names(self):
        sql = "CREATE TABLE IF NOT EXISTS `events` (id UInt64) ENGINE = MergeTree() ORDER BY id"
        cs = ClusterableStatement.from_sql(sql)
        ctx = ClusterContext(ClusterMode.ON_CLUSTER, "prod")
        out = cs.render(ctx)
        assert "ON CLUSTER 'prod'" in out


class TestClickHouseLockStrategy:
    """Tests for ClickHouse lock strategy."""

    def test_check_statement_idempotent_create(self):
        from dbwarden.lock.clickhouse import check_statement_idempotency
        is_idempotent, reason = check_statement_idempotency(
            "CREATE TABLE IF NOT EXISTS test (id Int32) ENGINE = MergeTree() ORDER BY id"
        )
        assert is_idempotent is True

    def test_check_statement_idempotent_drop(self):
        from dbwarden.lock.clickhouse import check_statement_idempotency
        is_idempotent, reason = check_statement_idempotency("DROP TABLE IF EXISTS test")
        assert is_idempotent is True

    def test_check_statement_idempotent_add_column(self):
        from dbwarden.lock.clickhouse import check_statement_idempotency
        is_idempotent, reason = check_statement_idempotency(
            "ALTER TABLE test ADD COLUMN IF NOT EXISTS name String"
        )
        assert is_idempotent is True

    def test_check_statement_non_idempotent_rename(self):
        from dbwarden.lock.clickhouse import check_statement_idempotency
        is_idempotent, reason = check_statement_idempotency(
            "ALTER TABLE test RENAME TO new_test"
        )
        assert is_idempotent is False
        assert "Non-idempotent" in reason

    def test_check_statement_non_idempotent_modify(self):
        from dbwarden.lock.clickhouse import check_statement_idempotency
        is_idempotent, reason = check_statement_idempotency(
            "ALTER TABLE test MODIFY COLUMN name String DEFAULT 'unknown'"
        )
        assert is_idempotent is False

    def test_check_statement_non_idempotent_update(self):
        from dbwarden.lock.clickhouse import check_statement_idempotency
        is_idempotent, reason = check_statement_idempotency(
            "UPDATE test SET name = 'test' WHERE id = 1"
        )
        assert is_idempotent is False

    def test_check_statement_non_idempotent_delete(self):
        from dbwarden.lock.clickhouse import check_statement_idempotency
        is_idempotent, reason = check_statement_idempotency("DELETE FROM test WHERE id = 1")
        assert is_idempotent is False

    def test_check_statement_comment_idempotent(self):
        from dbwarden.lock.clickhouse import check_statement_idempotency
        is_idempotent, reason = check_statement_idempotency("-- this is a comment")
        assert is_idempotent is True

    def test_check_statement_empty_idempotent(self):
        from dbwarden.lock.clickhouse import check_statement_idempotency
        is_idempotent, reason = check_statement_idempotency("")
        assert is_idempotent is True

    def test_check_statement_unknown_non_idempotent(self):
        from dbwarden.lock.clickhouse import check_statement_idempotency
        is_idempotent, reason = check_statement_idempotency("SOME UNKNOWN STATEMENT")
        assert is_idempotent is False
        assert "Unclassified" in reason


class TestClickHouseConfigSchema:
    """Tests for ClickHouse config schema options."""

    def test_ch_cluster_config(self):
        from dbwarden.config_schema import DatabaseEntry
        entry = DatabaseEntry(
            database_name="analytics",
            database_type="clickhouse",
            database_url_sync="clickhouse://localhost:8123/analytics",
            ch_cluster="production_cluster",
        )
        assert entry.ch_cluster == "production_cluster"
        assert entry.ch_replicated_database is False

    def test_ch_replicated_database_config(self):
        from dbwarden.config_schema import DatabaseEntry
        entry = DatabaseEntry(
            database_name="analytics",
            database_type="clickhouse",
            database_url_sync="clickhouse://localhost:8123/analytics",
            ch_replicated_database=True,
        )
        assert entry.ch_replicated_database is True
        assert entry.ch_cluster is None

    def test_clickhouse_config_defaults(self):
        from dbwarden.config_schema import DatabaseEntry
        entry = DatabaseEntry(
            database_name="analytics",
            database_type="clickhouse",
            database_url_sync="clickhouse://localhost:8123/analytics",
        )
        assert entry.ch_cluster is None
        assert entry.ch_replicated_database is False
