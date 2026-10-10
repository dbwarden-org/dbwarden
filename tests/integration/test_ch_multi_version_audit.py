"""Multi-version ClickHouse drift audit, ported from /tmp/ch_multi_version_audit.py.

Spins up one container per version, creates 25+ tables/MVs/extras, extracts
via ``_extract_clickhouse_schema_snapshot``, builds a model-side dict,
canonicalizes both, and deep-diffs.  Every ``.to_dict()`` boundary on
``ChEngineSpec``, ``ChIndexSpec``, ``ProjectionSpec`` is exercised.

Usage::

    pytest tests/integration/ --ch-integration --tb=short -v

Parametrized over CH versions (see ``CH_VERSIONS`` below).
"""

from __future__ import annotations

import json
import re
import time
from typing import Any

import pytest
from sqlalchemy import create_engine

pytest.importorskip("testcontainers.clickhouse")
pytest.importorskip("clickhouse_connect")

from dbwarden.engine.snapshot.extract_ch import _extract_clickhouse_schema_snapshot
from dbwarden.databases.clickhouse.engine import (
    ChEngineSpec, merge_tree, aggregating_merge_tree, replicated_merge_tree,
    memory, merge, null,
)
from dbwarden.engine.snapshot.ch_utils import _serialize_clickhouse_engine
from dbwarden.engine.backends.clickhouse.canonicalize import canonicalize

CH_VERSIONS = [
    pytest.param(("clickhouse/clickhouse-server:24.3", "24.3"), id="24.3"),
    pytest.param(("clickhouse/clickhouse-server:latest", "26.6"), id="26.6"),
]


def j(obj: Any) -> str:
    return json.dumps(obj, indent=2, sort_keys=True, default=str)


def _extract_engine_expr(create_query: str) -> str | None:
    m = re.search(r'ENGINE\s*=\s*(\w+(?:\s*\([^)]*\))?)', create_query, re.IGNORECASE)
    if m:
        return m.group(1)
    m = re.search(r'ENGINE\s+(\w+(?:\s*\([^)]*\))?)', create_query, re.IGNORECASE)
    return m.group(1) if m else None


def make_cases():
    cases = []

    def add(name, sql, model_kw=None, model_columns=None, model_indexes=None,
            extra_sql=None, skip_no_zk=False):
        cases.append(dict(
            name=name, create_sql=sql, model_kw=model_kw or {},
            model_columns=model_columns or [], model_indexes=model_indexes or [],
            extra_sql=extra_sql or [], skip_no_zk=skip_no_zk,
        ))

    ns = "SETTINGS allow_nullable_key = 1"

    add("version_probe", "SELECT 1", model_kw={"order_by": None})

    add("engine_no_parens",
        "CREATE TABLE engine_no_parens (a UInt64) ENGINE = MergeTree ORDER BY a",
        {"engine": merge_tree(), "order_by": "a"},
        [{"name": "a", "type": "UInt64"}])

    add("engine_empty_parens",
        "CREATE TABLE engine_empty_parens (a UInt64) ENGINE = MergeTree() ORDER BY a",
        {"engine": merge_tree(), "order_by": "a"},
        [{"name": "a", "type": "UInt64"}])

    add("replicated_mt",
        "CREATE TABLE replicated_mt (a UInt64) ENGINE = ReplicatedMergeTree('/zk/table', 'r1') ORDER BY a",
        {"engine": replicated_merge_tree('/zk/table', 'r1'), "order_by": "a"},
        [{"name": "a", "type": "UInt64"}],
        skip_no_zk=True)

    add("aggregating_mt",
        "CREATE TABLE aggregating_mt (k UInt64, v AggregateFunction(sum, Float64), c AggregateFunction(count)) ENGINE = AggregatingMergeTree ORDER BY k",
        {"engine": aggregating_merge_tree(), "order_by": "k"},
        [{"name": "k", "type": "UInt64"},
         {"name": "v", "type": "AggregateFunction(sum, Float64)"},
         {"name": "c", "type": "AggregateFunction(count)"}])

    add("order_by_multi",
        "CREATE TABLE order_by_multi (a UInt64, b UInt64) ENGINE = MergeTree ORDER BY (a, b)",
        {"engine": merge_tree(), "order_by": ["a", "b"]},
        [{"name": "a", "type": "UInt64"}, {"name": "b", "type": "UInt64"}])

    add("order_by_single",
        "CREATE TABLE order_by_single (a UInt64) ENGINE = MergeTree ORDER BY a",
        {"engine": merge_tree(), "order_by": "a"},
        [{"name": "a", "type": "UInt64"}])

    add("nullable_string",
        f"CREATE TABLE nullable_string (a Nullable(String)) ENGINE = MergeTree ORDER BY a {ns}",
        {"engine": merge_tree(), "order_by": "a",
         "settings": {"allow_nullable_key": "1"}},
        [{"name": "a", "type": "Nullable(String)"}])

    add("lowcard_nullable",
        f"CREATE TABLE lowcard_nullable (a LowCardinality(Nullable(String))) ENGINE = MergeTree ORDER BY a {ns}",
        {"engine": merge_tree(), "order_by": "a",
         "settings": {"allow_nullable_key": "1"}},
        [{"name": "a", "type": "LowCardinality(Nullable(String))"}])

    add("enum8",
        "CREATE TABLE enum8 (a Enum8('hello'=1, 'world'=2)) ENGINE = MergeTree ORDER BY a",
        {"engine": merge_tree(), "order_by": "a"},
        [{"name": "a", "type": "Enum8('hello'=1, 'world'=2)"}])

    add("decimal_10_2",
        "CREATE TABLE decimal_10_2 (a Decimal(10,2)) ENGINE = MergeTree ORDER BY a",
        {"engine": merge_tree(), "order_by": "a"},
        [{"name": "a", "type": "Decimal(10,2)"}])

    add("decimal64_2",
        "CREATE TABLE decimal64_2 (a Decimal64(2)) ENGINE = MergeTree ORDER BY a",
        {"engine": merge_tree(), "order_by": "a"},
        [{"name": "a", "type": "Decimal64(2)"}])

    add("datetime64_utc",
        "CREATE TABLE datetime64_utc (a DateTime64(3, 'UTC')) ENGINE = MergeTree ORDER BY a",
        {"engine": merge_tree(), "order_by": "a"},
        [{"name": "a", "type": "DateTime64(3, 'UTC')"}])

    add("map_string_uint64",
        "CREATE TABLE map_string_uint64 (a Map(String, UInt64)) ENGINE = MergeTree ORDER BY a",
        {"engine": merge_tree(), "order_by": "a"},
        [{"name": "a", "type": "Map(String, UInt64)"}])

    add("array_nullable",
        f"CREATE TABLE array_nullable (a Array(Nullable(Int32))) ENGINE = MergeTree ORDER BY a {ns}",
        {"engine": merge_tree(), "order_by": "a",
         "settings": {"allow_nullable_key": "1"}},
        [{"name": "a", "type": "Array(Nullable(Int32))"}])

    add("tuple_col",
        "CREATE TABLE tuple_col (a Tuple(a UInt8, b String)) ENGINE = MergeTree ORDER BY a",
        {"engine": merge_tree(), "order_by": "a"},
        [{"name": "a", "type": "Tuple(a UInt8, b String)"}])

    add("nested_col",
        "CREATE TABLE nested_col (id UInt64, n Nested(x UInt8, y String)) ENGINE = MergeTree ORDER BY id",
        {"engine": merge_tree(), "order_by": "id"},
        [{"name": "id", "type": "UInt64"},
         {"name": "n", "type": "Nested(x UInt8, y String)"}])

    add("fixedstring_16",
        "CREATE TABLE fixedstring_16 (a FixedString(16)) ENGINE = MergeTree ORDER BY a",
        {"engine": merge_tree(), "order_by": "a"},
        [{"name": "a", "type": "FixedString(16)"}])

    add("codec_chain",
        "CREATE TABLE codec_chain (a UInt64 CODEC(Delta, ZSTD(3))) ENGINE = MergeTree ORDER BY a",
        {"engine": merge_tree(), "order_by": "a"},
        [{"name": "a", "type": "UInt64", "codec": "Delta, ZSTD(3)"}])

    add("ttl_column",
        "CREATE TABLE ttl_column (a UInt64, b DateTime) ENGINE = MergeTree ORDER BY a TTL b + INTERVAL 1 MONTH",
        {"engine": merge_tree(), "order_by": "a", "ttl": ["b + INTERVAL 1 MONTH"]},
        [{"name": "a", "type": "UInt64"}, {"name": "b", "type": "DateTime"}])

    add("ttl_table",
        "CREATE TABLE ttl_table (a UInt64, b DateTime) ENGINE = MergeTree ORDER BY a TTL b + INTERVAL 1 MONTH RECOMPRESS CODEC(ZSTD(3)), b + INTERVAL 3 MONTH DELETE",
        {"engine": merge_tree(), "order_by": "a",
         "ttl": ["b + INTERVAL 1 MONTH RECOMPRESS CODEC(ZSTD(3))", "b + INTERVAL 3 MONTH"]},
        [{"name": "a", "type": "UInt64"}, {"name": "b", "type": "DateTime"}])

    add("settings_default",
        "CREATE TABLE settings_default (a UInt64) ENGINE = MergeTree ORDER BY a SETTINGS index_granularity = 8192",
        {"engine": merge_tree(), "order_by": "a",
         "settings": {"index_granularity": "8192"}},
        [{"name": "a", "type": "UInt64"}])

    add("skip_index",
        "CREATE TABLE skip_index (a UInt64, b String, INDEX ix_a a TYPE bloom_filter GRANULARITY 1) ENGINE = MergeTree ORDER BY a",
        {"engine": merge_tree(), "order_by": "a"},
        [{"name": "a", "type": "UInt64"}, {"name": "b", "type": "String"}],
        model_indexes=[{"name": "ix_a", "type": "bloom_filter", "expr": "a", "granularity": 1}])

    add("projection",
        "CREATE TABLE projection (a UInt64, b UInt64) ENGINE = MergeTree ORDER BY a",
        {"engine": merge_tree(), "order_by": "a",
         "projections": [{"name": "proj_a", "query": "SELECT a, b ORDER BY a"}]},
        [{"name": "a", "type": "UInt64"}, {"name": "b", "type": "UInt64"}],
        extra_sql=["ALTER TABLE projection ADD PROJECTION proj_a (SELECT a, b ORDER BY a)"])

    add("mv_with_to",
        "CREATE TABLE mv_target (a UInt64) ENGINE = MergeTree ORDER BY a",
        {"engine": None, "order_by": None, "object_type": "materialized_view",
         "select_statement": "SELECT a FROM mv_target", "to_table": "mv_target"},
        [{"name": "a", "type": "UInt64"}],
        extra_sql=[
            "CREATE MATERIALIZED VIEW mv_with_to TO mv_target AS SELECT a FROM mv_target"
        ])

    add("mv_inner",
        "CREATE TABLE mv_inner_source (a UInt64) ENGINE = MergeTree ORDER BY a",
        {"engine": merge_tree(), "order_by": "a", "object_type": "materialized_view",
         "select_statement": "SELECT a FROM mv_inner_source", "to_table": None},
        [{"name": "a", "type": "UInt64"}],
        extra_sql=[
            "CREATE MATERIALIZED VIEW mv_inner ENGINE = MergeTree ORDER BY a AS SELECT a FROM mv_inner_source"
        ])

    # ── Cases 25–36: new features ──

    # Case 32: special engines: Null
    add("null_table",
        "CREATE TABLE null_table (a UInt64) ENGINE = Null",
        {"engine": null(), "order_by": None},
        [{"name": "a", "type": "UInt64"}])

    # Case 32b: special engines: Memory
    add("memory_table",
        "CREATE TABLE memory_table (a UInt64) ENGINE = Memory",
        {"engine": memory(), "order_by": None},
        [{"name": "a", "type": "UInt64"}])

    # Case 32c: special engines: Merge
    add("merge_table",
        "CREATE TABLE merge_source (a UInt64) ENGINE = MergeTree ORDER BY a",
        {"engine": merge('audit', 'merge_source'), "order_by": None},
        [{"name": "a", "type": "UInt64"}],
        extra_sql=["CREATE TABLE merge_table (a UInt64) ENGINE = Merge('audit', 'merge_source')"])

    # Case 33: table + column comment
    add("comment_table",
        "CREATE TABLE comment_table (a UInt64 COMMENT 'col desc') ENGINE = MergeTree ORDER BY a COMMENT 'table desc'",
        {"engine": merge_tree(), "order_by": "a"},
        [{"name": "a", "type": "UInt64", "comment": "col desc"}])

    # Case 34: comment removal: empty comment canonicalizes to None
    add("comment_empty",
        "CREATE TABLE comment_empty (a UInt64) ENGINE = MergeTree ORDER BY a",
        {"engine": merge_tree(), "order_by": "a"},
        [{"name": "a", "type": "UInt64"}])

    # Case 35: column codec + REMOVE (create with codec, test that extracted codec matches)
    add("codec_remove",
        "CREATE TABLE codec_remove (a UInt64 CODEC(ZSTD(3))) ENGINE = MergeTree ORDER BY a",
        {"engine": merge_tree(), "order_by": "a"},
        [{"name": "a", "type": "UInt64", "codec": "ZSTD(3)"}])

    # Case 36: column TTL + REMOVE
    add("ttl_remove",
        "CREATE TABLE ttl_remove (a UInt64, b DateTime) ENGINE = MergeTree ORDER BY a TTL b + INTERVAL 1 DAY",
        {"engine": merge_tree(), "order_by": "a", "ttl": ["b + INTERVAL 1 DAY"]},
        [{"name": "a", "type": "UInt64"}, {"name": "b", "type": "DateTime"}])

    # ── Cases 37–39: compiled expression verification ──
    #
    # These test that render_expr() output matches what the server reports.
    # If compiled expressions diverge from server form, the whole widening is
    # wrong, which is why these MUST be audit cases against a live server,
    # not unit tests.

    # Case 37: partition_by=func.toYYYYMM(...), compiled expression vs server form
    add("compiled_partition",
        "CREATE TABLE compiled_partition (event_date Date, amount Float64) "
        "ENGINE = MergeTree ORDER BY event_date PARTITION BY toYYYYMM(event_date)",
        {"engine": merge_tree(), "order_by": "event_date",
         "partition_by": "toYYYYMM(event_date)"},
        [{"name": "event_date", "type": "Date"},
         {"name": "amount", "type": "Float64"}])

    # Case 38: compiled expression + alias in an MV body
    add("compiled_mv_group_by",
        "CREATE TABLE compiled_mv_target (day Date, cnt UInt64) ENGINE = MergeTree ORDER BY day",
        {"engine": None, "order_by": None, "object_type": "materialized_view",
         "select_statement": "SELECT toDate(event_time) AS day, count() AS cnt FROM compiled_mv_source GROUP BY day",
         "to_table": "compiled_mv_target"},
        [{"name": "day", "type": "Date"}, {"name": "cnt", "type": "UInt64"}],
        extra_sql=[
            "CREATE TABLE compiled_mv_source (event_time DateTime) ENGINE = MergeTree ORDER BY event_time",
            "CREATE MATERIALIZED VIEW compiled_mv_group_by TO compiled_mv_target AS "
            "SELECT toDate(event_time) AS day, count() AS cnt FROM compiled_mv_source GROUP BY day",
        ])

    # Case 39: compiled aggregate in a projection (select item)
    add("compiled_mv_select",
        "CREATE TABLE compiled_mv_select_target (total Float64) ENGINE = SummingMergeTree ORDER BY total",
        {"engine": None, "order_by": None, "object_type": "materialized_view",
         "select_statement": "SELECT sum(amount) AS total FROM compiled_mv_select_source",
         "to_table": "compiled_mv_select_target"},
        [{"name": "total", "type": "Float64"}],
        extra_sql=[
            "CREATE TABLE compiled_mv_select_source (amount Float64) ENGINE = MergeTree ORDER BY amount",
            "CREATE MATERIALIZED VIEW compiled_mv_select TO compiled_mv_select_target AS "
            "SELECT sum(amount) AS total FROM compiled_mv_select_source",
        ])

    return cases


def model_spec_for_table(table_name, **kw):
    columns = kw.pop("columns", [])
    indexes = kw.pop("indexes", [])
    engine = kw.get("engine")
    ch_engine_serialized = _serialize_clickhouse_engine(engine) if engine else None
    ch_engine_raw = engine.to_dict() if engine else None

    settings = kw.get("settings")

    ch_options = {
        "ch_engine_raw": ch_engine_raw,
        "ch_engine": ch_engine_serialized,
        "ch_order_by": kw.get("order_by"),
        "ch_primary_key": kw.get("primary_key"),
        "ch_partition_by": kw.get("partition_by"),
        "ch_sample_by": kw.get("sample_by"),
        "ch_ttl": kw.get("ttl"),
        "ch_settings": settings,
        "ch_object_type": kw.get("object_type", "table"),
        "ch_select_statement": kw.get("select_statement"),
        "ch_to_table": kw.get("to_table"),
        "ch_dictionary": kw.get("dictionary", False),
        "ch_dict_layout": kw.get("dict_layout"),
        "ch_dict_source": kw.get("dict_source"),
        "ch_dict_lifetime": kw.get("dict_lifetime"),
        "ch_dict_primary_key": kw.get("dict_primary_key"),
        "ch_projections": kw.get("projections") or [],
        "ch_zookeeper_path": kw.get("zookeeper_path"),
        "ch_replica_name": kw.get("replica_name"),
    }

    cols_dict = {}
    for col in columns:
        raw_type = col.get("type", "String")
        ch_nullable = "Nullable(" in str(raw_type)
        ch_low_cardinality = "LowCardinality(" in str(raw_type)
        codec = col.get("codec")
        ch_column = {
            "ch_codec": codec,
            "ch_default_expression": None,
            "ch_materialized": None,
            "ch_alias": None,
            "ch_ttl": None,
            "ch_low_cardinality": ch_low_cardinality,
            "ch_nullable": ch_nullable,
            "ch_type": raw_type,
        }
        cols_dict[col["name"]] = {
            "type": raw_type,
            "nullable": ch_nullable,
            "primary_key": col.get("primary_key", False),
            "default": None,
            "comment": col.get("comment"),
            "ch_column": ch_column,
        }

    pk = kw.get("order_by")
    if isinstance(pk, list):
        pk_list = list(pk)
    elif isinstance(pk, str) and pk:
        pk_list = [pk]
    else:
        pk_list = []

    return {
        "format_version": 1, "migration_id": "", "database_name": "audit",
        "database_type": "clickhouse", "applied_at": "",
        "tables": {
            table_name: {
                "object_type": ch_options["ch_object_type"],
                "columns": cols_dict,
                "primary_key": pk_list,
                "comment": None,
                "indexes": indexes,
                "ch_options": ch_options,
            }
        },
        "enums": {}, "indexes": {}, "constraints": {},
    }


def deep_diff(a: dict, b: dict, prefix: str = "") -> list[tuple[str, Any, Any]]:
    diffs = []
    all_keys = set(a.keys()) | set(b.keys())
    for k in sorted(all_keys):
        path = f"{prefix}.{k}" if prefix else k
        va = a.get(k)
        vb = b.get(k)
        if isinstance(va, dict) and isinstance(vb, dict):
            diffs.extend(deep_diff(va, vb, prefix=path))
        elif isinstance(va, list) and isinstance(vb, list):
            ja = json.dumps(va, sort_keys=True, default=str)
            jb = json.dumps(vb, sort_keys=True, default=str)
            if ja != jb:
                diffs.append((path, va, vb))
        else:
            if va != vb:
                diffs.append((path, va, vb))
    return diffs


@pytest.mark.integration
class TestChMultiVersionAudit:

    @pytest.fixture(scope="class")
    def container(self, request):
        image, label = request.param
        import clickhouse_connect
        from testcontainers.clickhouse import ClickHouseContainer

        ch = ClickHouseContainer(image, username="test", password="test")
        ch.start()
        host = ch.get_container_host_ip()
        port = ch.get_exposed_port(8123)
        client = clickhouse_connect.get_client(
            host=host, port=port, username="test", password="test"
        )
        ch._host = host
        ch._port = port
        ch._client = client
        ch._version_label = label
        ch._image = image
        yield ch
        try:
            ch.stop()
        except Exception:
            pass

    def _run_version(self, container):
        from dbwarden.engine.snapshot.extract_ch import _extract_clickhouse_schema_snapshot
        import clickhouse_connect

        client = container._client
        host = container._host
        port = container._port
        label = container._version_label

        version_row = client.query("SELECT version()").first_row
        actual_version = version_row[0] if version_row else "unknown"

        client.command("CREATE DATABASE IF NOT EXISTS audit")
        client.command("USE audit")

        sa_url = f"clickhousedb://test:test@{host}:{port}/audit"
        sa_engine = create_engine(sa_url)

        cases = make_cases()
        all_table_names = set()

        # ── create tables ──
        for c in cases:
            c["_errors"] = []
            c["_table_name"] = None
            stmts = []
            if c.get("create_sql") and "SELECT 1" not in c["create_sql"]:
                stmts.append(c["create_sql"])
            stmts.extend(c.get("extra_sql", []))
            for stmt in stmts:
                m = re.search(r'CREATE\s+(TABLE|MATERIALIZED\s+VIEW)\s+(\w+)', stmt, re.IGNORECASE)
                if m:
                    c["_table_name"] = m.group(2)
                    all_table_names.add(m.group(2))
                try:
                    client.command(stmt)
                except Exception as e:
                    if c["skip_no_zk"] and "NO_ZOOKEEPER" in str(e):
                        c["_errors"].append("SKIPPED (no ZooKeeper)")
                        continue
                    c["_errors"].append(str(e))
            time.sleep(1)

        # ── extract side ──
        with sa_engine.connect() as conn:
            extract_snap = _extract_clickhouse_schema_snapshot(conn, "audit")

        # ── model side ──
        model_snaps = {}
        for c in cases:
            tn = c.get("_table_name")
            if tn:
                model_snaps[tn] = model_spec_for_table(
                    tn, **c["model_kw"],
                    columns=c.get("model_columns", []),
                    indexes=c.get("model_indexes", []),
                )

        defaults = extract_snap.get("ch_setting_defaults", {})

        # ── compare ──
        results = []
        for c in cases:
            tn = c.get("_table_name")
            if not tn or (c.get("skip_no_zk") and c.get("_errors")):
                results.append({
                    "name": c["name"], "table": tn,
                    "status": "SKIPPED",
                    "detail": c["_errors"][0] if c.get("_errors") else "no table",
                })
                continue

            extract_opts = None
            model_opts = None
            if tn in extract_snap.get("tables", {}):
                extract_opts = extract_snap["tables"][tn].get("ch_options", {})
            if tn in model_snaps.get(tn, {}).get("tables", {}):
                model_opts = model_snaps[tn]["tables"][tn].get("ch_options", {})

            if extract_opts is None and tn not in extract_snap.get("tables", {}):
                results.append({
                    "name": c["name"], "table": tn,
                    "status": "MISSING",
                    "detail": "not found in extract snapshot",
                    "creation_error": bool(c.get("_errors")),
                })
                continue

            extract_c = canonicalize(
                dict(extract_opts), defaults=defaults, database="audit"
            ) if extract_opts else {}
            model_c = canonicalize(
                dict(model_opts), defaults=defaults, database="audit"
            ) if model_opts else {}

            diffs = deep_diff(extract_c, model_c, "diff")
            if not diffs:
                results.append({
                    "name": c["name"], "table": tn,
                    "status": "MATCH",
                    "creation_error": bool(c.get("_errors")),
                })
            else:
                drift_fields = {}
                for path, ev, mv in diffs:
                    field = path.split(".", 1)[-1]
                    drift_fields[field] = {"extract": ev, "model": mv}
                results.append({
                    "name": c["name"], "table": tn,
                    "status": "DRIFT",
                    "drift_fields": drift_fields,
                    "creation_error": bool(c.get("_errors")),
                })

        # ── verify named collections / RBAC extraction ──
        # Use the clickhouse_connect client directly to test system table queries
        rbac_ok = True
        rbac_detail: list[str] = []
        try:
            nc_rows = client.query("SELECT name FROM system.named_collections").result_rows
            rbac_detail.append(f"nc={len(nc_rows)}")
        except Exception as e:
            rbac_ok = False
            rbac_detail.append(f"nc_err={e}")
        try:
            role_rows = client.query("SELECT name FROM system.roles").result_rows
            rbac_detail.append(f"roles={len(role_rows)}")
        except Exception as e:
            rbac_ok = False
            rbac_detail.append(f"roles_err={e}")
        try:
            user_rows = client.query("SELECT name, storage FROM system.users").result_rows
            rbac_detail.append(f"users={len(user_rows)}")
        except Exception as e:
            rbac_ok = False
            rbac_detail.append(f"users_err={e}")
        try:
            sp_rows = client.query("SELECT name FROM system.settings_profiles").result_rows
            rbac_detail.append(f"profiles={len(sp_rows)}")
        except Exception as e:
            rbac_ok = False
            rbac_detail.append(f"profiles_err={e}")

        return {
            "version_label": label,
            "actual_version": actual_version,
            "results": results,
            "rbac_ok": rbac_ok,
            "rbac_detail": ", ".join(rbac_detail),
        }

    @pytest.mark.parametrize("container", CH_VERSIONS, indirect=True)
    def test_all_cases_converge(self, container):
        result = self._run_version(container)
        label = result["version_label"]
        actual = result["actual_version"]
        drifts = [r for r in result["results"] if r["status"] == "DRIFT"]
        missing = [r for r in result["results"] if r["status"] == "MISSING"]
        matches = [r for r in result["results"] if r["status"] == "MATCH"]

        lines = [f"CH {label} ({actual}): {len(matches)} match, {len(drifts)} drift, {len(missing)} missing"]
        for d in drifts:
            fields = ", ".join(d.get("drift_fields", {}).keys())
            lines.append(f"  DRIFT {d['name']}: {fields}")
            for f, v in d.get("drift_fields", {}).items():
                lines.append(f"    {f}: E={v['extract']!r} M={v['model']!r}")
        for m in missing:
            lines.append(f"  MISSING {m['name']}: {m.get('detail', '')}")

        rbac_ok = result.get("rbac_ok", False)
        rbac_detail = result.get("rbac_detail", "unknown")
        lines.append(f"RBAC: ok={rbac_ok} detail={rbac_detail}")

        report = "\n".join(lines)
        print(report)

        assert not drifts, f"Drifts on {label}:\n{report}"
        assert not missing, f"Missing on {label}:\n{report}"
        assert rbac_ok, f"RBAC system table queries failed on {label}: {rbac_detail}"
