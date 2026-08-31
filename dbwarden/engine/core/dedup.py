from __future__ import annotations

import re
from typing import Any

from dbwarden.logging import get_component_logger
from dbwarden.sql import split_sql_statements

_snapshot_logger = get_component_logger("snapshot")


def _normalize_sql(s: str) -> str:
    s = re.sub(r"\s+", " ", s).strip().lower()
    s = s.rstrip(";").strip()
    return s


def _sql_into_statements(sql: str) -> list[str]:
    parts = split_sql_statements(sql)
    return [p + ";" for p in parts]


def _filter_duplicates_from_snapshot_diff(
    upgrade_sql: str,
    rollback_sql: str,
    changes: list[Any],
    existing_statements: set[str],
) -> tuple[str, str, list[Any]]:
    normalized_existing: set[str] = set()
    for s in existing_statements:
        s = _normalize_sql(s)
        if s:
            normalized_existing.add(s)

    upgrade_parts = [s.strip() for s in upgrade_sql.split("\n\n") if s.strip()]
    rollback_parts = [s.strip() for s in rollback_sql.split("\n\n") if s.strip()]

    if len(upgrade_parts) != len(changes) or len(rollback_parts) != len(changes):
        _snapshot_logger.warning(
            "Snapshot diff part count mismatch: %d upgrade, %d rollback, %d changes. "
            "Skipping duplicate filter to avoid misalignment.",
            len(upgrade_parts), len(rollback_parts), len(changes),
        )
        return upgrade_sql, rollback_sql, changes

    filtered_upgrade = []
    filtered_rollback = []
    filtered_changes = []

    for i, (up_sql, rb_sql) in enumerate(zip(upgrade_parts, rollback_parts)):
        normalized = _normalize_sql(up_sql)
        if normalized in normalized_existing:
            continue
        statements = _sql_into_statements(up_sql)
        if statements and all(s in normalized_existing for s in statements):
            continue
        filtered_upgrade.append(up_sql)
        filtered_rollback.append(rb_sql)
        filtered_changes.append(changes[i])

    return "\n\n".join(filtered_upgrade), "\n\n".join(filtered_rollback), filtered_changes
