from __future__ import annotations

import re
from collections.abc import Iterable


def normalize_sql(sql: str) -> str:
    """Normalize SQL whitespace without changing quoted content semantics."""
    return re.sub(r"\s+", " ", sql).strip()


def assert_sql_contains(sql: str, *fragments: str) -> None:
    normalized = normalize_sql(sql).lower()
    for fragment in fragments:
        assert normalize_sql(fragment).lower() in normalized


def assert_sql_statements(sql: str, expected: Iterable[str]) -> None:
    normalized = normalize_sql(sql)
    for statement in expected:
        assert normalize_sql(statement) in normalized


def assert_real_rollback(rollback_sql: str) -> None:
    stripped = rollback_sql.strip()
    assert stripped
    assert not stripped.startswith("--")


def assert_noop_sql(upgrade_sql: str, rollback_sql: str) -> None:
    assert normalize_sql(upgrade_sql) == ""
    assert normalize_sql(rollback_sql) == ""
