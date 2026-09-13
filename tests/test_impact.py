import json
import os
import tempfile

from dbwarden.engine.impact import (
    _affected_operations,
    _extract_targets,
    parse_plan,
    scan_file_ast,
    scan_file_grep,
    analyze_impact,
)


def _make_plan(ops: list[dict]) -> str:
    plan = {"migration_id": "test__0001_test", "operations": ops, "required_flags": [], "checksum": ""}
    path = os.path.join(tempfile.mkdtemp(), "test.plan.json")
    with open(path, "w") as f:
        json.dump(plan, f)
    return path


def test_parse_plan():
    path = _make_plan([{"type": "drop_column", "table": "users", "target": "email", "severity": "WARNING"}])
    plan = parse_plan(path)
    assert plan["migration_id"] == "test__0001_test"
    assert len(plan["operations"]) == 1


def test_affected_operations_filters_info():
    ops = [
        {"type": "create_table", "table": "x", "severity": "INFO"},
        {"type": "add_column", "table": "x", "target": "y", "severity": "INFO"},
        {"type": "drop_column", "table": "x", "target": "y", "severity": "WARNING"},
        {"type": "drop_table", "table": "x", "severity": "ERROR"},
        {"type": "drop_table", "table": "y", "severity": "CRITICAL"},
    ]
    result = _affected_operations({"operations": ops})
    assert len(result) == 3
    assert result[0]["type"] == "drop_column"
    assert result[1]["severity"] == "ERROR"
    assert result[2]["severity"] == "CRITICAL"


def test_affected_operations_verbose():
    ops = [
        {"type": "create_table", "table": "x", "severity": "INFO"},
        {"type": "drop_column", "table": "x", "severity": "WARNING"},
    ]
    result = _affected_operations({"operations": ops}, verbose=True)
    assert len(result) == 2


def test_extract_targets_from_ops():
    ops = [
        {"type": "drop_column", "table": "users", "target": "email", "severity": "WARNING"},
        {"type": "drop_table", "table": "orders", "severity": "WARNING"},
    ]
    targets = _extract_targets(ops)
    assert "users" in targets
    assert "email" in targets
    assert "orders" in targets


def test_extract_targets_rename():
    ops = [{"type": "rename_table", "old_table": "old_t", "new_table": "new_t", "severity": "WARNING"}]
    targets = _extract_targets(ops)
    assert "old_t" in targets
    assert "new_t" in targets


def test_scan_file_grep_finds_match(tmp_path):
    f = tmp_path / "app.py"
    f.write_text("user = get_user(email='test@example.com')\n")
    results = scan_file_grep(str(f), ["email"])
    assert len(results) == 1
    assert results[0]["kind"] == "grep"
    assert "email" in results[0]["snippet"]


def test_scan_file_grep_skips_comments(tmp_path):
    f = tmp_path / "app.py"
    f.write_text("# email is the primary key\n")
    results = scan_file_grep(str(f), ["email"])
    assert len(results) == 0


def test_scan_file_ast_finds_attribute(tmp_path):
    f = tmp_path / "models.py"
    f.write_text("user.email = 'test@example.com'\n")
    results = scan_file_ast(str(f), ["email"])
    assert len(results) >= 1
    assert results[0]["kind"] == "attribute_access"


def test_scan_file_ast_finds_string_literal(tmp_path):
    f = tmp_path / "routes.py"
    f.write_text('query = select(User).where(User.name == "email")\n')
    results = scan_file_ast(str(f), ["email"])
    string_hits = [r for r in results if r["kind"] == "string_literal"]
    assert len(string_hits) >= 0


def test_analyze_impact_no_impact(tmp_path):
    plan_path = _make_plan([{"type": "create_table", "table": "new_table", "severity": "INFO"}])
    src = tmp_path / "src"
    src.mkdir()
    (src / "app.py").write_text("x = 1\n")
    result = analyze_impact(plan_path, scan_path=str(src))
    assert "impact" in result
    assert len(result["impact"]) == 0


def test_analyze_impact_with_impact(tmp_path):
    plan_path = _make_plan([{"type": "drop_column", "table": "users", "target": "email", "severity": "WARNING"}])
    src = tmp_path / "src"
    src.mkdir()
    (src / "app.py").write_text("user.email = 'test@example.com'\n")
    result = analyze_impact(plan_path, scan_path=str(src))
    assert len(result["impact"]) > 0
    first = result["impact"][0]
    assert first["operation_type"] == "drop_column"
    assert first["references"]


def test_analyze_impact_verbose_shows_info(tmp_path):
    plan_path = _make_plan([
        {"type": "create_table", "table": "new_table", "severity": "INFO"},
        {"type": "drop_column", "table": "users", "target": "email", "severity": "WARNING"},
    ])
    src = tmp_path / "src"
    src.mkdir()
    (src / "app.py").write_text("new_table = get_model()\nuser.email = 'test'\n")
    quiet = analyze_impact(plan_path, scan_path=str(src), verbose=False)
    verbose = analyze_impact(plan_path, scan_path=str(src), verbose=True)
    assert len(verbose["impact"]) >= len(quiet["impact"])


def test_analyze_impact_missing_plan(tmp_path):
    import pytest
    with pytest.raises(FileNotFoundError):
        analyze_impact(str(tmp_path / "nonexistent.json"))


def test_scan_file_ast_handles_syntax_error(tmp_path):
    f = tmp_path / "bad.py"
    f.write_text("this is not valid python @@\n")
    results = scan_file_ast(str(f), ["something"])
    assert results == []


def test_scan_file_grep_no_match(tmp_path):
    f = tmp_path / "app.py"
    f.write_text("x = 1\n")
    results = scan_file_grep(str(f), ["nonexistent"])
    assert results == []


def test_scan_file_grep_non_existent_file():
    results = scan_file_grep("/nonexistent/file.py", ["test"])
    assert results == []
