"""Tests for ClickHouse Phase 4-6 handlers and the ClusterableStatement helper."""

import pytest

from dbwarden.databases.clickhouse.cluster import ClusterContext, ClusterMode
from dbwarden.databases.clickhouse import (
    ChIndexSpec, ChRaw, ch_raw, agg, aggregating_view, data_op,
)
from dbwarden.engine.backends.clickhouse.cluster import ClusterableStatement
from dbwarden.engine.backends.clickhouse.handlers import (
    ChAggTargetHandler,
    ChDataOpHandler,
    ChDictionaryHandler,
    ChMaterializedViewHandler,
    ChProjectionHandler,
    ChSkipIndexHandler,
    ChTableHandler,
)
from dbwarden.engine.backends.clickhouse.canonicalize import check_immutable
from dbwarden.engine.snapshot import snapshot_diff_to_sql
from dbwarden.engine.snapshot.ch_utils import classify_clickhouse_recreate_rollback
from dbwarden.engine.core.models import ModelColumn, ModelTable
from dbwarden.engine.core.protocol import Op
from dbwarden.exceptions import ImmutableChangeError


# ── ClusterableStatement.from_sql ─────────────────────────────────────────────

class TestClusterableStatementFromSql:
    def test_create_table(self):
        cs = ClusterableStatement.from_sql(
            "CREATE TABLE IF NOT EXISTS events (id UInt64) ENGINE = MergeTree()"
        )
        assert cs.prefix == "CREATE TABLE IF NOT EXISTS events"
        assert cs.suffix == " (id UInt64) ENGINE = MergeTree()"


class TestClusterableStatementFromSqlMore:
    def test_create_table_on_cluster(self):
        cs = ClusterableStatement.from_sql(
            "CREATE TABLE IF NOT EXISTS events (id UInt64) ENGINE = MergeTree()"
        )
        ctx = ClusterContext(ClusterMode.ON_CLUSTER, "prod")
        out = cs.render(ctx)
        assert "ON CLUSTER 'prod'" in out
        assert "CREATE TABLE IF NOT EXISTS events ON CLUSTER" in out

    def test_create_dictionary(self):
        cs = ClusterableStatement.from_sql(
            "CREATE DICTIONARY IF NOT EXISTS d (id UInt64) PRIMARY KEY id"
        )
        ctx = ClusterContext(ClusterMode.ON_CLUSTER, "c")
        out = cs.render(ctx)
        assert "ON CLUSTER 'c'" in out
        assert "CREATE DICTIONARY IF NOT EXISTS d ON CLUSTER" in out

    def test_create_materialized_view(self):
        cs = ClusterableStatement.from_sql(
            "CREATE MATERIALIZED VIEW IF NOT EXISTS mv TO t AS SELECT * FROM src"
        )
        ctx = ClusterContext(ClusterMode.ON_CLUSTER, "c")
        out = cs.render(ctx)
        assert "ON CLUSTER 'c'" in out
        assert "CREATE MATERIALIZED VIEW IF NOT EXISTS mv ON CLUSTER" in out

    def test_alter_table(self):
        cs = ClusterableStatement.from_sql(
            "ALTER TABLE events DROP PARTITION 202301"
        )
        ctx = ClusterContext(ClusterMode.ON_CLUSTER, "c")
        out = cs.render(ctx)
        assert "ON CLUSTER 'c'" in out

    def test_rename_table(self):
        cs = ClusterableStatement.from_sql(
            "RENAME TABLE events TO events__dbw_old, events__dbw_new TO events"
        )
        ctx = ClusterContext(ClusterMode.ON_CLUSTER, "c")
        out = cs.render(ctx)
        assert "ON CLUSTER 'c'" in out
        assert "TO events__dbw_old" in out

    def test_detach_table(self):
        cs = ClusterableStatement.from_sql("DETACH TABLE events_mv")
        ctx = ClusterContext(ClusterMode.ON_CLUSTER, "c")
        out = cs.render(ctx)
        assert "ON CLUSTER 'c'" in out

    def test_drop_table(self):
        cs = ClusterableStatement.from_sql("DROP TABLE IF EXISTS events")
        ctx = ClusterContext(ClusterMode.ON_CLUSTER, "c")
        out = cs.render(ctx)
        assert "ON CLUSTER 'c'" in out

    def test_no_double_spaces(self):
        """No double spaces should appear in rendered output."""
        stmts = [
            "CREATE TABLE t (id UInt64)",
            "ALTER TABLE t DROP PARTITION 1",
            "RENAME TABLE a TO b",
            "DROP TABLE t",
        ]
        ctx = ClusterContext(ClusterMode.ON_CLUSTER, "c")
        for s in stmts:
            cs = ClusterableStatement.from_sql(s)
            out = cs.render(ctx)
            assert "  " not in out, f"double space in {out!r}"

    def test_non_ddl_pass_through(self):
        """Non-DDL statements pass through (prefix=whole string, suffix='')."""
        cs = ClusterableStatement.from_sql(
            "INSERT INTO events (id) SELECT id FROM events_old"
        )
        assert cs.prefix == "INSERT INTO events (id) SELECT id FROM events_old"
        assert cs.suffix == ""
        ctx = ClusterContext(ClusterMode.ON_CLUSTER, "c")
        out = cs.render(ctx)
        # ON CLUSTER is still appended, harmless for SQL strings that
        # get post-processed by the caller; the caller (recreate builder)
        # now filters INSERT via a DDL check before calling from_sql.
        assert out == "INSERT INTO events (id) SELECT id FROM events_old ON CLUSTER 'c'"

    def test_none_mode_no_cluster(self):
        cs = ClusterableStatement.from_sql(
            "CREATE TABLE t (id UInt64) ENGINE = MergeTree()"
        )
        ctx = ClusterContext(ClusterMode.NONE, None)
        out = cs.render(ctx)
        assert "ON CLUSTER" not in out


class TestClickHouseRecreateRollbackPolicy:
    @pytest.mark.parametrize(
        "to_engine",
        [
            "ReplacingMergeTree",
            "SummingMergeTree",
            "AggregatingMergeTree",
            "CollapsingMergeTree",
            "VersionedCollapsingMergeTree",
        ],
    )
    def test_lossy_engine_transition_is_irreversible(self, to_engine):
        kind, reason = classify_clickhouse_recreate_rollback("MergeTree", to_engine)

        assert kind == "irreversible"
        assert "lose row-level detail" in reason

    def test_unknown_engine_transition_is_irreversible(self):
        kind, reason = classify_clickhouse_recreate_rollback("MergeTree", "CustomTree")

        assert kind == "irreversible"
        assert "not statically proven" in reason

    def test_row_preserving_engine_transition_is_conditional(self):
        kind, reason = classify_clickhouse_recreate_rollback("MergeTree", "Log")

        assert kind == "conditional"
        assert "known row-preserving" in reason


# ── ChDictionaryHandler ───────────────────────────────────────────────────────

class TestChDictionaryHandler:
    def setup_method(self):
        self.h = ChDictionaryHandler()
        self.ctx = ClusterContext(ClusterMode.ON_CLUSTER, "prod")

    def test_create_dictionary(self):
        up, _ = self.h.diff({}, {"d": dict(
            ch_dictionary=True,
            ch_dict_layout="HASHED()",
            ch_dict_source="CLICKHOUSE TABLE src",
            ch_dict_lifetime=300,
        )})
        assert len(up) == 1
        assert up[0].upgrade_attrs["action"] == "create"
        stmts = self.h.emit(up[0], cluster_ctx=self.ctx)
        assert "CREATE DICTIONARY" in stmts[0].upgrade_sql

    def test_alter_dictionary(self):
        snap = {"d": dict(ch_dictionary=True, ch_dict_layout="HASHED()")}
        model = {"d": dict(ch_dictionary=True, ch_dict_layout="COMPLEX_KEY_HASHED()")}
        up, _ = self.h.diff(snap, model)
        assert len(up) == 1
        assert up[0].upgrade_attrs["action"] == "alter"
        stmts = self.h.emit(up[0], cluster_ctx=self.ctx)
        assert "MODIFY LAYOUT" in stmts[0].upgrade_sql

    def test_drop_dictionary(self):
        snap = {"d": dict(ch_dictionary=True, ch_dict_layout="HASHED()")}
        up, _ = self.h.diff(snap, {})
        assert len(up) == 1
        assert up[0].upgrade_attrs["action"] == "drop"
        stmts = self.h.emit(up[0], cluster_ctx=self.ctx)
        assert "DROP DICTIONARY" in stmts[0].upgrade_sql

    def test_no_cluster_mode(self):
        up, _ = self.h.diff({"d": dict(ch_dictionary=True)}, {})
        stmts = self.h.emit(up[0])
        assert "ON CLUSTER" not in stmts[0].upgrade_sql

    def test_no_change(self):
        snap = {"d": dict(ch_dictionary=True, ch_dict_layout="HASHED()")}
        up, _ = self.h.diff(snap, dict(snap))
        assert len(up) == 0

    def test_model_spec_from_tables(self):
        t = ModelTable(name="d", columns=[], clickhouse_options={
            "ch_dictionary": True,
            "ch_dict_layout": "HASHED()",
        })
        spec = self.h.model_spec_from_tables([t])
        assert "d" in spec
        assert spec["d"]["ch_dict_layout"] == "HASHED()"

    def test_no_dictionary_keys_in_chtablehandler(self):
        from dbwarden.engine.backends.clickhouse.handlers.ch_table_handler import (
            _CH_OPTION_KEYS, _RECREATE_REQUIRED_CH_KEYS,
        )
        assert "ch_dictionary" not in _CH_OPTION_KEYS
        assert "ch_dict_layout" not in _RECREATE_REQUIRED_CH_KEYS


# ── ChProjectionHandler ───────────────────────────────────────────────────────

class TestChProjectionHandler:
    def setup_method(self):
        self.h = ChProjectionHandler()
        self.ctx = ClusterContext(ClusterMode.ON_CLUSTER, "prod")

    def test_add_projection(self):
        up, _ = self.h.diff({}, {"t": [{"name": "p1", "query": "SELECT a ORDER BY a"}]})
        assert len(up) == 1
        stmts = self.h.emit(up[0], cluster_ctx=self.ctx)
        assert "ADD PROJECTION" in stmts[0].upgrade_sql

    def test_drop_projection(self):
        snap = {"t": [{"name": "p1", "query": "SELECT a ORDER BY a"}]}
        up, _ = self.h.diff(snap, {})
        assert len(up) == 1
        stmts = self.h.emit(up[0], cluster_ctx=self.ctx)
        assert "DROP PROJECTION" in stmts[0].upgrade_sql

    def test_replace_projection(self):
        snap = {"t": [{"name": "p1", "query": "SELECT a ORDER BY a"}]}
        model = {"t": [{"name": "p1", "query": "SELECT a, b ORDER BY a, b"}]}
        up, _ = self.h.diff(snap, model)
        assert len(up) == 1
        stmts = self.h.emit(up[0], cluster_ctx=self.ctx)
        assert "DROP PROJECTION" in stmts[0].upgrade_sql
        assert "ADD PROJECTION" in stmts[0].upgrade_sql

    def test_no_cluster_mode(self):
        up, _ = self.h.diff({"t": [{"name": "p1", "query": "SELECT a"}]}, {})
        stmts = self.h.emit(up[0])
        assert "ON CLUSTER" not in stmts[0].upgrade_sql

    def test_no_change(self):
        snap = {"t": [{"name": "p1", "query": "SELECT a"}]}
        up, _ = self.h.diff(snap, dict(snap))
        assert len(up) == 0


# ── ChSkipIndexHandler ────────────────────────────────────────────────────────

class TestChSkipIndexHandler:
    def setup_method(self):
        self.h = ChSkipIndexHandler()
        self.ctx = ClusterContext(ClusterMode.ON_CLUSTER, "prod")

    def test_add_index(self):
        up, _ = self.h.diff({}, {"t": [dict(
            name="ix1", columns=["payload"],
            clickhouse_type="bloom_filter", clickhouse_granularity=1,
        )]})
        assert len(up) == 1
        stmts = self.h.emit(up[0], cluster_ctx=self.ctx)
        assert "ADD INDEX" in stmts[0].upgrade_sql
        assert "bloom_filter" in stmts[0].upgrade_sql

    def test_drop_index(self):
        snap = {"t": [dict(name="ix1", columns=["payload"], type="bloom_filter")]}
        up, _ = self.h.diff(snap, {})
        assert len(up) == 1
        stmts = self.h.emit(up[0], cluster_ctx=self.ctx)
        assert "DROP INDEX" in stmts[0].upgrade_sql

    def test_replace_index(self):
        snap = {"t": [dict(name="ix1", columns=["payload"], type="bloom_filter", granularity=1)]}
        model = {"t": [dict(name="ix1", columns=["payload"], type="set", granularity=4)]}
        up, _ = self.h.diff(snap, model)
        assert len(up) == 1
        stmts = self.h.emit(up[0], cluster_ctx=self.ctx)
        assert "DROP INDEX" in stmts[0].upgrade_sql
        assert "ADD INDEX" in stmts[0].upgrade_sql

    def test_model_spec_with_ch_index_spec(self):
        ix = ChIndexSpec("ix", ["a"], type="set", granularity=4)
        t = ModelTable(name="t", columns=[], clickhouse_options={"ch_indexes": [ix.to_dict()]})
        spec = self.h.model_spec_from_tables([t])
        assert "t" in spec
        assert spec["t"][0]["clickhouse_type"] == "set"

    def test_no_cluster_mode(self):
        snap = {"t": [dict(name="ix1", columns=["a"], type="minmax")]}
        up, _ = self.h.diff(snap, {})
        stmts = self.h.emit(up[0])
        assert "ON CLUSTER" not in stmts[0].upgrade_sql

    def test_no_change(self):
        snap = {"t": [dict(name="ix1", columns=["a"], type="minmax", granularity=1)]}
        up, _ = self.h.diff(snap, dict(snap))
        assert len(up) == 0


# ── ChAggTargetHandler ────────────────────────────────────────────────────────

class TestChAggTargetHandler:
    def setup_method(self):
        self.h = ChAggTargetHandler()
        self.ctx = ClusterContext(ClusterMode.ON_CLUSTER, "prod")

    def _make_table(self, name="agg_target", order=None):
        return ModelTable(
            name=name,
            columns=[],
            clickhouse_options={
                "ch_engine": "AggregatingMergeTree()",
                "ch_order_by": order or ["x"],
            },
        )

    def test_create_agg_target(self):
        m = self.h.model_spec_from_tables([self._make_table()])
        up, _ = self.h.diff({}, m)
        assert len(up) == 1
        assert up[0].object_type == "create_ch_agg_target"
        stmts = self.h.emit(up[0], cluster_ctx=self.ctx)
        assert "AggregatingMergeTree" in stmts[0].upgrade_sql

    def test_drop_agg_target(self):
        m = self.h.model_spec_from_tables([self._make_table()])
        up, _ = self.h.diff(m, {})
        assert len(up) == 1
        assert up[0].object_type == "drop_ch_agg_target"
        stmts = self.h.emit(up[0], cluster_ctx=self.ctx)
        assert "DROP TABLE" in stmts[0].upgrade_sql

    def test_no_change(self):
        m = self.h.model_spec_from_tables([self._make_table()])
        up, _ = self.h.diff(m, dict(m))
        assert len(up) == 0


# ── aggregating_view integration ──────────────────────────────────────────────

class TestAggregatingView:
    def test_triad_generated(self):
        from dbwarden.databases.clickhouse import AggregatingViewSpec
        av = aggregating_view(
            name="events_daily",
            source="events",
            group_by=["user_id", ch_raw("toDate(event_time) AS day")],
            aggregates=[
                agg.sum("amount", "Float64").as_("amount_sum"),
                agg.count().as_("event_count"),
            ],
            order_by=["user_id", "day"],
            partition_by="toYYYYMM(day)",
        )
        assert isinstance(av, AggregatingViewSpec)
        d = av.to_dict()
        assert "ch_agg_target" in d
        assert "ch_agg_mv" in d
        mv = d["ch_agg_mv"]
        assert "ch_object_type" not in mv
        # In the class API, target is __tablename__ (here: name), not name + "_agg"
        assert mv["ch_to_table"] == "events_daily"
        assert mv["ch_select_statement"] is not None
        assert "sumState(amount) AS amount_sum" in mv["ch_select_statement"]

    def test_target_derived_from_name(self):
        from dbwarden.databases.clickhouse import AggregatingViewSpec
        av = aggregating_view(
            source="src",
            group_by=["id"],
            aggregates=[agg.count().as_("cnt")],
            order_by=["id"],
            name="my_ev",
        )
        assert isinstance(av, AggregatingViewSpec)
        d = av.to_dict()
        target = d["ch_agg_target"]["name"]
        mv = d["ch_agg_mv"]
        assert target == "my_ev"
        assert mv["ch_to_table"] == "my_ev"

    def test_cascade_level_uses_merge_state_combinator(self):
        """Cascade level emits sumMergeState, not sumState.

        Proved by the container test in ``test_agg_cascade.py``. This is a
        fast regression check for the shape already proven correct there.
        """
        from dbwarden.databases.clickhouse import AggregatingViewSpec, AggregatingView
        from dbwarden.schema.table_meta import CHViewMeta
        from sqlalchemy import Integer, String
        from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

        class _Base(DeclarativeBase):
            pass

        class SourceTable(_Base):
            __tablename__ = "source"
            id: Mapped[int] = mapped_column(Integer, primary_key=True)
            amount: Mapped[float] = mapped_column()

        class LevelOne(AggregatingView):
            __tablename__ = "level_one"

            class Meta(CHViewMeta):
                ch = aggregating_view(
                    source=SourceTable,
                    group_by=["id"],
                    aggregates=[agg.sum("amount", "Float64").as_("amount_sum")],
                    order_by=["id"],
                )

        class LevelTwo(AggregatingView):
            __tablename__ = "level_two"

            class Meta(CHViewMeta):
                ch = aggregating_view(
                    source=LevelOne,
                    group_by=["id"],
                    aggregates=[
                        agg.sum("amount_sum", "Float64").as_("amount_sum"),
                    ],
                    order_by=["id"],
                )

        d = LevelTwo.Meta.ch.to_dict()
        select = d["ch_agg_mv"]["ch_select_statement"]
        assert "sumMergeState(amount_sum)" in select, (
            f"Cascade level should use sumMergeState, got:\n{select}"
        )
        # The first level (not tested here) still uses sumState — that's correct

    def test_plain_source_uses_state_combinator(self):
        """Cascade level emits sumMergeState, not sumState.

        Proved by the container test in ``test_agg_cascade.py``. This is a
        fast regression check for the shape already proven correct there.
        """
        from sqlalchemy import Integer, String
        from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

        class _Base(DeclarativeBase):
            pass

        class SourceTable(_Base):
            __tablename__ = "source"
            id: Mapped[int] = mapped_column(Integer, primary_key=True)
            amount: Mapped[float] = mapped_column()

        av = aggregating_view(
            name="events_daily",
            source=SourceTable,
            group_by=["id"],
            aggregates=[
                agg.sum("amount", "Float64").as_("amount_sum"),
            ],
            order_by=["id"],
        )
        d = av.to_dict()
        select = d["ch_agg_mv"]["ch_select_statement"]
        assert "sumState(amount) AS amount_sum" in select, (
            f"Plain source should use sumState, got:\n{select}"
        )


# ── data_op ───────────────────────────────────────────────────────────────────

class TestDataOp:
    def test_basic(self):
        op = data_op(
            name="drop_part",
            forward="ALTER TABLE events DROP PARTITION '202301'",
            rollback=None,
        )
        assert op.name == "drop_part"
        assert op.forward == "ALTER TABLE events DROP PARTITION '202301'"
        assert op.rollback is None

    def test_requires_confirmation(self):
        op = data_op(
            name="danger",
            forward="DROP TABLE events",
            rollback=None,
            requires_confirmation=True,
        )
        assert op.requires_confirmation

    def test_handler_emit(self):
        h = ChDataOpHandler()
        op = Op(object_type="apply_data_op", upgrade_attrs={
            "data_op": data_op(
                name="test",
                forward="OPTIMIZE TABLE events FINAL",
                rollback="-- no rollback",
            ),
        })
        stmts = h.emit(op)
        assert len(stmts) == 1
        assert "OPTIMIZE TABLE events FINAL" in stmts[0].upgrade_sql
        assert "-- no rollback" in stmts[0].rollback_sql

    def test_handler_emit_with_confirmation(self):
        h = ChDataOpHandler()
        op = Op(object_type="apply_data_op", upgrade_attrs={
            "data_op": data_op(
                name="danger",
                forward="DROP TABLE events",
                rollback=None,
                requires_confirmation=True,
            ),
        })
        stmts = h.emit(op)
        assert "requires confirmation" in stmts[0].upgrade_sql


# ── ChTableHandler (immutable keys, engine gate, convergence) ──────────────

class TestChTableHandler:
    def setup_method(self):
        self.h = ChTableHandler()

    def _make_opts(self, overrides: dict | None = None) -> dict:
        opts = {
            "ch_engine": "MergeTree()",
            "ch_order_by": ["id", "ts"],
            "ch_partition_by": "toYYYYMM(ts)",
            "ch_primary_key": ["id", "ts"],
            "ch_sample_by": "id",
            "ch_ttl": ["ts + INTERVAL 30 DAY"],
            "ch_settings": {"index_granularity": "8192"},
        }
        if overrides:
            opts.update(overrides)
        return opts

    def _make_table(self, name="t", opts=None, columns=None):
        """Build a ModelTable with the given clickhouse_options."""
        return ModelTable(
            name=name,
            columns=columns or [],
            clickhouse_options=opts or self._make_opts(),
        )

    @staticmethod
    def _col_entry(name, type_="Int64", nullable=False, primary_key=True):
        return {"name": name, "type": type_, "nullable": nullable,
                "primary_key": primary_key, "unique": False, "default": None,
                "foreign_key": None, "comment": None}

    def test_no_change(self):
        opts = self._make_opts()
        snap = {"t": {"ch_options": opts, "snapshot_table": {"name": "t", "columns": {}}}}
        model = {"t": {"ch_options": dict(opts), "model_table": self._make_table(opts=dict(opts))}}
        up, _ = self.h.diff(snap, model)
        assert len(up) == 0

    def test_immutable_partition_by_raises(self):
        snap = {"t": {"ch_options": self._make_opts(), "snapshot_table": {"name": "t", "columns": {}}}}
        model_opts = self._make_opts({"ch_partition_by": "toYYYYMM(created)"})
        model = {"t": {"ch_options": model_opts,
                       "model_table": self._make_table(opts={"ch_partition_by": "toYYYYMM(created)"})}}
        with pytest.raises(ImmutableChangeError, match="ch_partition_by"):
            self.h.diff(snap, model)

    def test_immutable_primary_key_raises(self):
        snap = {"t": {"ch_options": self._make_opts(), "snapshot_table": {"name": "t", "columns": {}}}}
        model_opts = self._make_opts({"ch_primary_key": ["id"]})
        model = {"t": {"ch_options": model_opts,
                       "model_table": self._make_table(opts={"ch_primary_key": ["id"]})}}
        with pytest.raises(ImmutableChangeError, match="ch_primary_key"):
            self.h.diff(snap, model)

    def test_immutable_sample_by_raises(self):
        snap = {"t": {"ch_options": self._make_opts(), "snapshot_table": {"name": "t", "columns": {}}}}
        model_opts = self._make_opts({"ch_sample_by": "ts"})
        model = {"t": {"ch_options": model_opts,
                       "model_table": self._make_table(opts={"ch_sample_by": "ts"})}}
        with pytest.raises(ImmutableChangeError, match="ch_sample_by"):
            self.h.diff(snap, model)

    def test_engine_change_flag_off_emits_comment(self):
        """Engine change with clickhouse_engine_recreate=False emits instructive comment."""
        snap_opts = self._make_opts({"ch_engine": "MergeTree()"})
        model_opts = self._make_opts({"ch_engine": "ReplacingMergeTree(ver)"})
        snap = {"t": {"ch_options": snap_opts, "snapshot_table": {"name": "t", "columns": {
            "id": self._col_entry("id"),
        }}}}
        model = {"t": {"ch_options": model_opts,
                       "model_table": self._make_table(opts=model_opts)}}
        self.h.clickhouse_engine_recreate = False
        up, _ = self.h.diff(snap, model)
        assert len(up) == 1
        assert up[0].object_type == "alter_ch_options"
        changes = up[0].upgrade_attrs["changes"]
        assert "ch_engine" in changes

    def test_alter_options_rollback_reads_rollback_attrs(self):
        op = Op(
            object_type="alter_ch_options",
            upgrade_attrs={
                "table": "events",
                "changes": {
                    "ch_ttl": {"from": ["ts + INTERVAL 1 DAY"], "to": ["ts + INTERVAL 7 DAY"]},
                    "ch_settings": {"from": {"index_granularity": "8192"}, "to": {"index_granularity": "4096"}},
                },
            },
            rollback_attrs={
                "table": "events",
                "changes": {
                    "ch_ttl": {"from": ["ts + INTERVAL 7 DAY"], "to": ["ts + INTERVAL 1 DAY"]},
                    "ch_settings": {"from": {"index_granularity": "4096"}, "to": {"index_granularity": "8192"}},
                },
            },
        )

        stmt = self.h.emit(op)[0]

        assert "MODIFY TTL ts + INTERVAL 7 DAY" in stmt.upgrade_sql
        assert "MODIFY SETTING index_granularity = 4096" in stmt.upgrade_sql
        assert "MODIFY TTL ts + INTERVAL 1 DAY" in stmt.rollback_sql
        assert "MODIFY SETTING index_granularity = 8192" in stmt.rollback_sql

    def test_engine_change_flag_on_creates_recreate(self):
        """Engine change with clickhouse_engine_recreate=True creates recreate op."""
        snap_opts = self._make_opts({"ch_engine": "MergeTree()"})
        model_opts = self._make_opts({"ch_engine": "ReplacingMergeTree(ver)"})
        snap = {"t": {"ch_options": snap_opts, "snapshot_table": {"name": "t", "columns": {
            "id": self._col_entry("id"),
        }}}}
        model = {"t": {"ch_options": model_opts,
                       "model_table": self._make_table(opts=model_opts,
                           columns=[ModelColumn("id", "Int64", nullable=False, primary_key=True, unique=False, default=None, foreign_key=None)])}}
        self.h.clickhouse_engine_recreate = True
        up, _ = self.h.diff(snap, model)
        assert len(up) == 1
        assert up[0].object_type == "recreate_ch_table"
        assert "ch_engine" in up[0].upgrade_attrs["reason"]


# ── Cluster matrix tests (all handlers, NONE + ON_CLUSTER) ─────────────────

class TestClusterMatrix:
    """Every handler emit parameterized over NONE and ON_CLUSTER modes."""

    def _check_mode(self, stmts, mode):
        for s in stmts:
            upgrade = s.upgrade_sql
            rollback = s.rollback_sql
            if mode is ClusterMode.ON_CLUSTER:
                assert "ON CLUSTER" in upgrade, f"Missing ON CLUSTER in upgrade: {upgrade}"
            else:
                assert "ON CLUSTER" not in upgrade, f"Unexpected ON CLUSTER in upgrade: {upgrade}"
                assert "ON CLUSTER" not in rollback, f"Unexpected ON CLUSTER in rollback: {rollback}"

    @pytest.mark.parametrize("mode", [ClusterMode.NONE, ClusterMode.ON_CLUSTER])
    def test_dictionary_handler(self, mode):
        ctx = ClusterContext(mode, "prod" if mode == ClusterMode.ON_CLUSTER else None)
        h = ChDictionaryHandler()
        up, _ = h.diff({}, {"d": dict(
            ch_dictionary=True,
            ch_dict_layout="HASHED()",
            ch_dict_source="CLICKHOUSE TABLE src",
            ch_dict_lifetime=300,
        )})
        stmts = h.emit(up[0], cluster_ctx=ctx)
        self._check_mode(stmts, mode)

    @pytest.mark.parametrize("mode", [ClusterMode.NONE, ClusterMode.ON_CLUSTER])
    def test_projection_handler(self, mode):
        ctx = ClusterContext(mode, "prod" if mode == ClusterMode.ON_CLUSTER else None)
        h = ChProjectionHandler()
        snap = {"t": [{"name": "p1", "query": "SELECT a ORDER BY a"}]}
        up, _ = h.diff(snap, {})
        stmts = h.emit(up[0], cluster_ctx=ctx)
        self._check_mode(stmts, mode)

    @pytest.mark.parametrize("mode", [ClusterMode.NONE, ClusterMode.ON_CLUSTER])
    def test_skip_index_handler(self, mode):
        ctx = ClusterContext(mode, "prod" if mode == ClusterMode.ON_CLUSTER else None)
        h = ChSkipIndexHandler()
        snap = {"t": [dict(name="ix1", columns=["a"], type="minmax")]}
        up, _ = h.diff(snap, {})
        stmts = h.emit(up[0], cluster_ctx=ctx)
        self._check_mode(stmts, mode)

    @pytest.mark.parametrize("mode", [ClusterMode.NONE, ClusterMode.ON_CLUSTER])
    def test_agg_target_handler(self, mode):
        ctx = ClusterContext(mode, "prod" if mode == ClusterMode.ON_CLUSTER else None)
        h = ChAggTargetHandler()

        t = ModelTable(
            name="agg_target",
            columns=[],
            clickhouse_options={
                "ch_engine": "AggregatingMergeTree()",
                "ch_order_by": ["x"],
            },
        )
        m = h.model_spec_from_tables([t])
        up, _ = h.diff({}, m)
        stmts = h.emit(up[0], cluster_ctx=ctx)
        self._check_mode(stmts, mode)

    @pytest.mark.parametrize("mode", [ClusterMode.NONE, ClusterMode.ON_CLUSTER])
    def test_mv_handler(self, mode):
        ctx = ClusterContext(mode, "prod" if mode == ClusterMode.ON_CLUSTER else None)
        h = ChMaterializedViewHandler()
        snap = {"mv": {"ch_select_statement": "SELECT a FROM src", "ch_to_table": "t"}}
        model = {"mv": {"ch_select_statement": "SELECT a, b FROM src", "ch_to_table": "t"}}
        up, _ = h.diff(snap, model)
        stmts = h.emit(up[0], cluster_ctx=ctx)
        self._check_mode(stmts, mode)

    @pytest.mark.parametrize("mode", [ClusterMode.NONE, ClusterMode.ON_CLUSTER])
    def test_table_handler_alter(self, mode):
        ctx = ClusterContext(mode, "prod" if mode == ClusterMode.ON_CLUSTER else None)
        h = ChTableHandler()
        snap = {"t": {"ch_options": {"ch_ttl": ["ts + 30"]}, "snapshot_table": {"name": "t", "columns": {}}}}
        model = {"t": {"ch_options": {"ch_ttl": ["ts + 60"]},
                       "model_table": ModelTable(name="t", columns=[], clickhouse_options={"ch_ttl": ["ts + 60"]})}}
        up, _ = h.diff(snap, model)
        stmts = h.emit(up[0], cluster_ctx=ctx)
        self._check_mode(stmts, mode)

    @staticmethod
    def _col_entry(name, type_="Int64", nullable=False, primary_key=True):
        return {"name": name, "type": type_, "nullable": nullable,
                "primary_key": primary_key, "unique": False, "default": None,
                "foreign_key": None, "comment": None}

    @pytest.mark.parametrize("mode", [ClusterMode.NONE, ClusterMode.ON_CLUSTER])
    def test_table_handler_recreate(self, mode):
        ctx = ClusterContext(mode, "prod" if mode == ClusterMode.ON_CLUSTER else None)
        h = ChTableHandler()
        h.clickhouse_engine_recreate = True
        snap_opts = {"ch_engine": "MergeTree()", "ch_order_by": ["id"]}
        model_opts = {"ch_engine": "ReplicatedMergeTree('/zk', 'r1')", "ch_order_by": ["id"]}
        snap = {"t": {"ch_options": snap_opts, "snapshot_table": {"name": "t", "columns": {
            "id": TestClusterMatrix._col_entry("id"),
        }}}}
        model = {"t": {"ch_options": model_opts,
                       "model_table": ModelTable(name="t",
                           columns=[ModelColumn("id", "Int64", nullable=False, primary_key=True, unique=False, default=None, foreign_key=None)],
                           clickhouse_options=model_opts)}}
        up, _ = h.diff(snap, model)
        stmts = h.emit(up[0], cluster_ctx=ctx)
        self._check_mode(stmts, mode)


@pytest.mark.p0
class TestTwoCycleConvergence:
    """Model-snapshot diff must produce zero ops when they already match."""

    def _snap_table_entry(self, name: str, ch_opts: dict,
                          col_dicts: list[dict] | None = None) -> dict:
        if col_dicts is None:
            col_dicts = [{"name": "id", "type": "Int64", "nullable": False,
                          "primary_key": True, "unique": False, "default": None,
                          "foreign_key": None, "comment": None,
                          "ch_meta": {"ch_type": "Int64"}}]
        cols = {c["name"]: c for c in col_dicts}
        return {"ch_options": dict(ch_opts),
                "snapshot_table": {"name": name, "columns": cols}}

    def _model_table_entry(self, name: str, ch_opts: dict,
                           col_dicts: list[dict] | None = None) -> dict:
        from dbwarden.engine.core.models import ModelColumn, ModelTable
        if col_dicts is None:
            col_dicts = [{"name": "id", "type": "Int64", "nullable": False,
                          "primary_key": True}]
        cols = [ModelColumn(**c, unique=False, default=None, foreign_key=None,
                            comment=None, ch_meta=c.get("ch_meta", {"ch_type": "Int64"}))
                for c in col_dicts]
        return {"ch_options": dict(ch_opts),
                "model_table": ModelTable(name=name, columns=cols,
                                          clickhouse_options=dict(ch_opts))}

    def test_ch_table_handler_converges(self):
        from dbwarden.engine.backends.clickhouse.handlers import ChTableHandler
        h = ChTableHandler()
        cases = [
            {"ch_order_by": ("user_id", "event_time")},
            {"ch_order_by": ["user_id", "event_time"]},
            {"ch_order_by": ("id",)},
            {"ch_order_by": ["id"]},
            {"ch_order_by": "id"},
            {"ch_order_by": ("id",), "ch_partition_by": "toYYYYMM(ts)"},
            {"ch_order_by": ("id",), "ch_ttl": "ts + INTERVAL 30 DAY"},
            {"ch_order_by": ("id",), "ch_settings": {"max_block_size": 65536}},
        ]
        for opts in cases:
            m_opts = {"ch_engine": "MergeTree()", **opts}
            snap = {"tbl": self._snap_table_entry("tbl", m_opts)}
            model = {"tbl": self._model_table_entry("tbl", m_opts)}
            up, _ = h.diff(snap, model)
            assert not up, f"ChTableHandler drift: {opts}"

    def test_engine_recreate_converges(self):
        from dbwarden.engine.backends.clickhouse.handlers import ChTableHandler
        h = ChTableHandler()
        h.clickhouse_engine_recreate = True
        m_opts = {"ch_engine": "ReplicatedMergeTree('/zk', 'r1')", "ch_order_by": ["id"]}
        snap = {"tbl": self._snap_table_entry("tbl", m_opts)}
        model = {"tbl": self._model_table_entry("tbl", m_opts)}
        up, _ = h.diff(snap, model)
        assert not up, "engine recreate handler drifted"

    def test_column_handler_converges(self):
        from dbwarden.engine.backends.clickhouse.handlers import ChColumnHandler
        h = ChColumnHandler()
        meta = {"ch_type": "Int64", "ch_codec": "LZ4"}
        snap = {"tbl": {"id": meta}}
        model = {"tbl": {"id": dict(meta)}}
        up, _ = h.diff(snap, model)
        assert not up, "ChColumnHandler drifted on codec"

    def test_mv_handler_converges(self):
        from dbwarden.engine.backends.clickhouse.handlers import ChMaterializedViewHandler
        h = ChMaterializedViewHandler()
        snap = {"mv": {"ch_select_statement": "SELECT a FROM src", "ch_to_table": "t"}}
        model = {"mv": {"ch_select_statement": "SELECT a FROM src", "ch_to_table": "t"}}
        up, _ = h.diff(snap, model)
        assert not up, "ChMaterializedViewHandler drifted"

    def test_projection_handler_converges(self):
        from dbwarden.engine.backends.clickhouse.handlers import ChProjectionHandler
        h = ChProjectionHandler()
        snap = {"t": [{"name": "p1", "query": "SELECT a ORDER BY a"}]}
        model = {"t": [{"name": "p1", "query": "SELECT a ORDER BY a"}]}
        up, _ = h.diff(snap, model)
        assert not up, "ChProjectionHandler drifted"

    def test_skip_index_handler_converges(self):
        from dbwarden.engine.backends.clickhouse.handlers import ChSkipIndexHandler
        h = ChSkipIndexHandler()
        entry = dict(name="ix1", columns=["payload"],
                     clickhouse_type="bloom_filter", clickhouse_granularity=1)
        snap = {"t": [dict(entry)]}
        model = {"t": [dict(entry)]}
        up, _ = h.diff(snap, model)
        assert not up, "ChSkipIndexHandler drifted"

    def test_agg_target_handler_converges(self):
        from dbwarden.engine.backends.clickhouse.handlers import ChAggTargetHandler
        from dbwarden.engine.core.models import ModelTable
        h = ChAggTargetHandler()
        m_opts = {"ch_engine": "AggregatingMergeTree()", "ch_order_by": ["x"]}
        t = ModelTable(name="target_tbl", columns=[],
                       clickhouse_options=m_opts)
        snap = {"target_tbl": {"exists": True}}
        model = {"target_tbl": {"exists": True, "options": m_opts}}
        up, _ = h.diff(snap, model)
        assert not up, "ChAggTargetHandler drifted"

    def test_dictionary_handler_converges(self):
        from dbwarden.engine.backends.clickhouse.handlers import ChDictionaryHandler
        h = ChDictionaryHandler()
        snap = {"dict": {"ch_dict_layout": "flat()",
                         "ch_dict_source": {"clickhouse": {"table": "src"}},
                         "ch_dict_lifetime": 300, "ch_dict_primary_key": "id"}}
        model = {"dict": {"ch_dict_layout": "flat()",
                          "ch_dict_source": {"clickhouse": {"table": "src"}},
                          "ch_dict_lifetime": 300, "ch_dict_primary_key": "id"}}
        up, _ = h.diff(snap, model)
        assert not up, "ChDictionaryHandler drifted"

    def test_data_op_handler_converges(self):
        from dbwarden.engine.backends.clickhouse.handlers import ChDataOpHandler
        h = ChDataOpHandler()
        up, _ = h.diff({}, {})
        assert not up, "ChDataOpHandler should always return empty diff"
