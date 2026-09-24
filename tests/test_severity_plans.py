import json

import pytest

from dbwarden.engine.safety.classifiers import Safety, classify_operation, exceeds
from dbwarden.engine.safety.plans import bind_plan, file_severity


@pytest.mark.parametrize("kind,attrs,expected", [
    ("add_column", {}, "INFO"), ("set_not_null", {}, "WARN"),
    ("drop_column", {}, "CRITICAL"), ("plugin_unknown", {}, "UNKNOWN"),
    ("create_index", {"concurrently": False}, "WARN"),
    ("update", {"bounded_key": True}, "INFO"), ("update", {}, "WARN"),
])
def test_shared_classification(kind, attrs, expected):
    assert classify_operation({"type": kind, **attrs}, "postgresql") == expected


def test_plan_trust_and_normalization(tmp_path):
    path = tmp_path / "0001_test.sql"
    content = "-- upgrade\nCREATE TABLE t (id INT);\n-- rollback\nDROP TABLE t;\n"
    path.write_text(content, encoding="utf-8")
    plan = bind_plan({}, content, [{"type": "create_table", "table": "t"}], "sqlite")
    path.with_suffix(".plan.json").write_text(json.dumps(plan), encoding="utf-8")
    assert file_severity(path) == (Safety.INFO, "")
    path.write_text(content + "-- edited\n", encoding="utf-8")
    assert file_severity(path)[0] == Safety.UNKNOWN
    assert exceeds(Safety.UNKNOWN, "WARN")
    assert not exceeds(Safety.UNKNOWN, "CRITICAL")


@pytest.mark.parametrize("mutation", [
    {"severity": {"file": "INFO", "provenance": "header", "ops": []}},
    {"severity": {"file": "INFO", "provenance": "generated", "ops": []}},
    {"severity": []}, {"required_flags": "--force"},
])
def test_malformed_metadata_is_unknown(tmp_path, mutation):
    path = tmp_path / "0001_test.sql"
    path.write_text("-- upgrade\n", encoding="utf-8")
    plan = bind_plan({}, path.read_text(), [], "sqlite")
    plan.update(mutation)
    path.with_suffix(".plan.json").write_text(json.dumps(plan), encoding="utf-8")
    assert file_severity(path)[0] == Safety.UNKNOWN
@pytest.mark.parametrize("before,after", [
    ({"type": "varchar"}, {"type": "varchar", "length": 20}),
    ({"type": "numeric", "precision": 10, "scale": 2}, {"type": "numeric", "precision": 10, "scale": 1}),
    ({"type": "numeric", "precision": 10, "scale": 2}, {"type": "numeric", "precision": 10, "scale": 3}),
])
def test_narrowing_capacity_is_critical(before, after):
    from dbwarden.engine.safety.classifiers import classify_operation
    assert classify_operation({"type": "alter_column_type", "from_type": before, "to_type": after}, "postgresql") == "CRITICAL"
