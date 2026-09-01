"""Cascading aggregating view container test: asserts VALUES, not SQL shape.

The critical combinator distinction: ``sumState(x)`` vs ``sumMergeState(x)``.
Both parse. A wrong combinator at a cascade level produces silently wrong
numbers, not an error. The only thing that can disagree is real data through
a real cascade; mocked SQL assertions pass tautologically.

This file has TWO test classes:

1. ``TestCascadeCorrectSQL``: uses hand-written DDL with the *correct*
   combinator (sumMergeState at cascade levels). This establishes that the
   cascade mechanism works in ClickHouse and the test infrastructure is sound.

2. ``TestCascadeViaDbWardenAPI``: uses ``aggregating_view()`` / ``to_dict()``
   to generate DDL. **Before the fix**: generates wrong combinators, so the
   test fails. **After the fix**: generates correct combinators, test passes.
   This is the gate.

Usage::

    pytest tests/integration/test_agg_cascade.py --ch-integration --tb=short -v

All tables use the ``default`` database so that bare table names in generated
SELECT statements (from ``aggregating_view().to_dict()``) resolve correctly.
"""

from __future__ import annotations

import os
import time

import pytest

pytest.importorskip("testcontainers.clickhouse")
pytest.importorskip("clickhouse_connect")


def _get_ch_client():
    """Return a clickhouse-connect client (container or env-provided)."""
    import clickhouse_connect

    host = os.environ.get("CLICKHOUSE_HOST")
    native_port = os.environ.get("CLICKHOUSE_PORT")
    if host and native_port:
        return clickhouse_connect.get_client(
            host=host,
            port=int(native_port),
            username=os.environ.get("CLICKHOUSE_USERNAME", "default"),
            password=os.environ.get("CLICKHOUSE_PASSWORD", ""),
        )

    from testcontainers.clickhouse import ClickHouseContainer

    ch = ClickHouseContainer(image="clickhouse/clickhouse-server:24.3")
    ch.__enter__()
    client = clickhouse_connect.get_client(
        host=ch.get_container_host_ip(),
        port=ch.get_exposed_port(8123),
        username=ch.username,
        password=ch.password,
    )
    client._dbw_container = ch
    return client


@pytest.fixture(scope="session")
def ch_client():
    client = _get_ch_client()
    yield client
    container = getattr(client, "_dbw_container", None)
    if container is not None:
        try:
            client.command("DROP TABLE IF EXISTS events")
            client.command("DROP TABLE IF EXISTS event_hourly")
            client.command("DROP TABLE IF EXISTS event_daily")
            client.command("DROP VIEW IF EXISTS event_hourly_mv")
            client.command("DROP VIEW IF EXISTS event_daily_mv")
        except Exception:
            pass
        container.__exit__(None, None, None)


def _clean(client):
    for t in ("events", "event_hourly", "event_hourly_mv", "event_daily", "event_daily_mv"):
        client.command(f"DROP TABLE IF EXISTS {t}")
    for t in ("event_hourly_mv", "event_daily_mv"):
        client.command(f"DROP VIEW IF EXISTS {t}")


def _reset(client):
    _clean(client)
    client.command("""
        CREATE TABLE events (
            id UInt64,
            user_id Int64,
            amount Float64
        ) ENGINE = MergeTree()
        ORDER BY (user_id, id)
    """)


def _read_cascade(client) -> list[tuple[int, float]]:
    rows = client.query("""
        SELECT user_id, sumMerge(amount_sum) AS total
        FROM event_daily
        GROUP BY user_id
        ORDER BY user_id
    """)
    return [(r[0], float(r[1])) for r in rows.result_rows]


# ── Test 1: Hand-written correct DDL (proves cascade CAN work) ────────────────

@pytest.mark.integration
class TestCascadeCorrectSQL:

    def test_three_level_cascade_produces_correct_values(self, ch_client):
        _clean(ch_client)
        _reset(ch_client)

        ch_client.command("""
            CREATE TABLE event_hourly (
                user_id Int64,
                amount_sum AggregateFunction(sum, Float64)
            ) ENGINE = AggregatingMergeTree()
            ORDER BY user_id
        """)
        ch_client.command("""
            CREATE MATERIALIZED VIEW event_hourly_mv
            TO event_hourly
            AS SELECT
                user_id,
                sumState(amount) AS amount_sum
            FROM events
            GROUP BY user_id
        """)

        ch_client.command("""
            CREATE TABLE event_daily (
                user_id Int64,
                amount_sum AggregateFunction(sum, Float64)
            ) ENGINE = AggregatingMergeTree()
            ORDER BY user_id
        """)
        ch_client.command("""
            CREATE MATERIALIZED VIEW event_daily_mv
            TO event_daily
            AS SELECT
                user_id,
                sumMergeState(amount_sum) AS amount_sum
            FROM event_hourly
            GROUP BY user_id
        """)

        ch_client.command("""
            INSERT INTO events (id, user_id, amount) VALUES
            (0, 1, 10.0), (1, 1, 20.0), (2, 1, 30.0),
            (3, 2, 100.0), (4, 2, 200.0),
            (5, 3, 5.0), (6, 3, 5.0), (7, 3, 5.0)
        """)
        time.sleep(2)

        result = _read_cascade(ch_client)
        assert result == [
            (1, 60.0),
            (2, 300.0),
            (3, 15.0),
        ], f"Expected correct rollup sum, got {result}"


# ── Test 2: DbWarden-generated DDL (proves the combinator fix) ────────────────

@pytest.mark.integration
class TestCascadeViaDbWardenAPI:

    def test_dbwarden_generated_cascade_uses_correct_combinator(self, ch_client):
        from dbwarden.databases.clickhouse import (
            agg, aggregating_view, AggregatingViewSpec,
        )
        from dbwarden.databases.clickhouse.views import AggregatingView
        from dbwarden.schema.table_meta import CHViewMeta

        _clean(ch_client)
        _reset(ch_client)

        class EventHourly(AggregatingView):
            __tablename__ = "event_hourly"

            class Meta(CHViewMeta):
                ch = aggregating_view(
                    source="events",
                    group_by=["user_id"],
                    aggregates=[agg.sum("amount", "Float64").as_("amount_sum")],
                    order_by=["user_id"],
                )

        class EventDaily(AggregatingView):
            __tablename__ = "event_daily"

            class Meta(CHViewMeta):
                ch = aggregating_view(
                    source=EventHourly,
                    group_by=["user_id"],
                    aggregates=[
                        agg.sum("amount_sum", "Float64").as_("amount_sum"),
                    ],
                    order_by=["user_id"],
                )

        hourly_spec = EventHourly.Meta.ch
        daily_spec = EventDaily.Meta.ch
        hourly_select = hourly_spec.to_dict()["ch_agg_mv"]["ch_select_statement"]
        daily_select = daily_spec.to_dict()["ch_agg_mv"]["ch_select_statement"]

        assert "sumMergeState(amount_sum)" in daily_select, (
            f"Expected sumMergeState for cascade level, got:\n{daily_select}"
        )

        ch_client.command("""
            CREATE TABLE event_hourly (
                user_id Int64,
                amount_sum AggregateFunction(sum, Float64)
            ) ENGINE = AggregatingMergeTree()
            ORDER BY user_id
        """)
        ch_client.command(f"""
            CREATE MATERIALIZED VIEW event_hourly_mv
            TO event_hourly
            AS {hourly_select}
        """)
        ch_client.command("""
            CREATE TABLE event_daily (
                user_id Int64,
                amount_sum AggregateFunction(sum, Float64)
            ) ENGINE = AggregatingMergeTree()
            ORDER BY user_id
        """)
        ch_client.command(f"""
            CREATE MATERIALIZED VIEW event_daily_mv
            TO event_daily
            AS {daily_select}
        """)

        ch_client.command("""
            INSERT INTO events (id, user_id, amount) VALUES
            (0, 1, 10.0), (1, 1, 20.0), (2, 1, 30.0),
            (3, 2, 100.0), (4, 2, 200.0),
            (5, 3, 5.0), (6, 3, 5.0), (7, 3, 5.0)
        """)
        time.sleep(2)

        result = _read_cascade(ch_client)
        assert result == [
            (1, 60.0),
            (2, 300.0),
            (3, 15.0),
        ], (
            f"Cascade produced wrong values: {result}.\n"
            f"The MV SELECT was:\n{daily_select}\n"
            f"Expected sumMergeState for cascade level, but likely got "
            f"sumState (state-of-a-state; invalid or silently wrong)."
        )
