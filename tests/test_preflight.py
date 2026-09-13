import json
import os
import tempfile

from dbwarden.engine.preflight import (
    PreflightResult,
    _find_plan_file,
    _parse_plan_safety,
    run_preflight,
)


def _make_migration_file(tmp_path, version="0001"):
    sql = f"-- Migration: {version}\nSELECT 1;\n"
    path = tmp_path / f"{version}__test.sql"
    path.write_text(sql)
    return str(path)


def _make_plan_file(tmp_path, version="0001", ops=None):
    plan = {
        "migration_id": f"{version}__test",
        "operations": ops or [],
        "required_flags": [],
        "checksum": "",
    }
    path = tmp_path / f"{version}__test.plan.json"
    with open(path, "w") as f:
        json.dump(plan, f)
    return str(path)


def test_find_plan_file_found(tmp_path):
    filepath = _make_migration_file(tmp_path)
    _make_plan_file(tmp_path)
    result = _find_plan_file(filepath)
    assert result is not None
    assert result.endswith(".plan.json")


def test_find_plan_file_missing(tmp_path):
    filepath = _make_migration_file(tmp_path)
    result = _find_plan_file(filepath)
    assert result is None


def test_parse_plan_safety_empty():
    assert _parse_plan_safety({"operations": []}) == []


def test_parse_plan_safety_filters_info():
    ops = [
        {"type": "create_table", "table": "x", "severity": "INFO"},
        {"type": "drop_column", "table": "x", "severity": "WARNING"},
    ]
    result = _parse_plan_safety({"operations": ops})
    assert len(result) == 1
    assert result[0]["severity"] == "WARNING"


def test_parse_plan_safety_includes_error():
    ops = [{"type": "drop_table", "table": "x", "severity": "ERROR"}]
    result = _parse_plan_safety({"operations": ops})
    assert len(result) == 1
    assert result[0]["severity"] == "ERROR"


def test_run_preflight_no_plans_warn(tmp_path):
    filepath = _make_migration_file(tmp_path)
    result = run_preflight({"0001": filepath}, missing_plan="warn")
    assert not result.abort
    assert len(result.warnings) == 1
    assert "no plan file found" in result.warnings[0]


def test_run_preflight_no_plans_block(tmp_path):
    filepath = _make_migration_file(tmp_path)
    result = run_preflight({"0001": filepath}, missing_plan="block")
    assert result.abort
    assert len(result.errors) == 1
    assert "missing plan" in result.errors[0]


def test_run_preflight_no_plans_off(tmp_path):
    filepath = _make_migration_file(tmp_path)
    result = run_preflight({"0001": filepath}, missing_plan="off")
    assert not result.abort
    assert not result.warnings
    assert not result.errors


def test_run_preflight_safe_plan(tmp_path):
    filepath = _make_migration_file(tmp_path)
    _make_plan_file(tmp_path, ops=[{"type": "add_column", "table": "x", "severity": "INFO"}])
    result = run_preflight({"0001": filepath})
    assert not result.abort
    assert not result.errors


def test_run_preflight_warning_plan(tmp_path):
    filepath = _make_migration_file(tmp_path)
    _make_plan_file(tmp_path, ops=[{"type": "drop_column", "table": "x", "severity": "WARNING"}])
    result = run_preflight({"0001": filepath})
    assert not result.abort
    assert len(result.warnings) == 1


def test_run_preflight_error_plan(tmp_path):
    filepath = _make_migration_file(tmp_path)
    _make_plan_file(tmp_path, ops=[{"type": "drop_table", "table": "x", "severity": "ERROR"}])
    result = run_preflight({"0001": filepath})
    assert result.abort
    assert len(result.errors) == 1


def test_run_preflight_critical_plan(tmp_path):
    filepath = _make_migration_file(tmp_path)
    _make_plan_file(tmp_path, ops=[{"type": "drop_table", "table": "x", "severity": "CRITICAL"}])
    result = run_preflight({"0001": filepath})
    assert result.abort
    assert len(result.errors) == 1


def test_run_preflight_malformed_plan_warn(tmp_path):
    filepath = _make_migration_file(tmp_path)
    (tmp_path / "0001__test.plan.json").write_text("not json")
    result = run_preflight({"0001": filepath}, missing_plan="warn")
    assert not result.abort
    assert len(result.warnings) == 1
    assert "malformed plan" in result.warnings[0]


def test_run_preflight_malformed_plan_block(tmp_path):
    filepath = _make_migration_file(tmp_path)
    (tmp_path / "0001__test.plan.json").write_text("not json")
    result = run_preflight({"0001": filepath}, missing_plan="block")
    assert result.abort
    assert len(result.errors) == 1
    assert "malformed plan" in result.errors[0]


def test_run_preflight_empty_batch():
    result = run_preflight({})
    assert not result.abort
    assert not result.warnings
    assert not result.errors
