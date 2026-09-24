import json

import pytest

from dbwarden.engine.safety.plans import file_severity
from dbwarden.engine.safety.static import classify_file


@pytest.mark.parametrize("sql,level", [
    ("CREATE TABLE t (id INT);", "INFO"),
    ("ALTER TABLE t ALTER COLUMN id SET NOT NULL;", "WARN"),
    ("ALTER TABLE t DROP COLUMN id;", "CRITICAL"),
    ("CREATE INDEX ix ON t(id);", "WARN"),
    ("CREATE INDEX CONCURRENTLY ix ON t(id);", "INFO"),
    ("UPDATE t SET x=2 WHERE id BETWEEN 1 AND 8;", "INFO"),
    ("UPDATE t SET x=2;", "WARN"),
    ("DO $$ BEGIN DELETE FROM t; END $$;", "UNKNOWN"),
    ("CREATE TABLE t (id INT); EXECUTE 'DROP TABLE t';", "UNKNOWN"),
    ("CREATE TABLE t (id INT", "UNKNOWN"),
    ("ALTER TABLE t ALTER COLUMN id TYPE SMALLINT;", "UNKNOWN"),
    ("WITH gone AS (DELETE FROM t RETURNING *) SELECT * FROM gone;", "UNKNOWN"),
    ("SELECT * INTO new_table FROM t;", "UNKNOWN"),
    ("CREATE SEQUENCE ids;", "INFO"),
    ("CREATE TYPE mood AS ENUM ('happy', 'sad');", "INFO"),
    ("CREATE TABLE t (id INT); INVALID SQL;", "UNKNOWN"),
    ("SELECT 1; /*! DROP TABLE t; */", "UNKNOWN"),
    ("CREATE SCHEMA app CREATE VIEW v AS SELECT unsafe_function();", "UNKNOWN"),
    ("ALTER TYPE mood RENAME VALUE 'happy' TO 'sad';", "UNKNOWN"),
])
def test_static_file_fails_closed(tmp_path, sql, level):
    path = tmp_path / "primary__0001_manual.sql"
    path.write_text(f"-- upgrade\n{sql}\n-- rollback\nDROP TABLE t;\n", encoding="utf-8")
    assert classify_file(path, "postgresql")[0] == level
    assert file_severity(path)[0] == level
    if level == "UNKNOWN":
        assert not path.with_suffix(".plan.json").exists()
    else:
        before = path.with_suffix(".plan.json").read_bytes()
        assert classify_file(path, "postgresql")[1] == "unchanged"
        assert path.with_suffix(".plan.json").read_bytes() == before


def test_generated_never_overwritten(tmp_path):
    path = tmp_path / "primary__0001_generated.sql"
    path.write_text("-- upgrade\nCREATE TABLE t (id INT);", encoding="utf-8")
    plan = {"severity": {"provenance": "generated"}}
    plan_path = path.with_suffix(".plan.json")
    plan_path.write_text(json.dumps(plan), encoding="utf-8")
    before = plan_path.read_bytes()
    assert classify_file(path, "sqlite")[0] == "UNKNOWN"
    assert plan_path.read_bytes() == before
