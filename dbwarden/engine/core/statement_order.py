from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum
from typing import Any


class StatementOrder(IntEnum):
    ROLE_MGMT = -5
    ALTER_DEFAULT_PRIVILEGES = -4
    CREATE_EXTENSION = -3
    CREATE_SCHEMA = -2
    CREATE_DOMAIN = -1
    CREATE_SEQUENCE = 0
    RENAME_TABLE = 1
    RENAME_COLUMN = 2
    ALTER_COLUMN_TYPE = 3
    ALTER_COLUMN_NULLABLE = 4
    ALTER_COLUMN_DEFAULT = 5
    CREATE_TYPE = 6
    CREATE_FUNCTION = 6
    CREATE_TABLE = 7
    CREATE_VIEW = 8
    ADD_COLUMN = 9
    ALTER_FOREIGN_KEY = 10
    DROP_VIEW = 11
    ALTER_INDEX = 12
    DROP_COLUMN = 13
    DROP_TABLE = 14
    ALTER_TABLE_COMMENT = 15
    ALTER_COLUMN_COMMENT = 16
    ALTER_TABLE_OPTIONS = 17
    ALTER_TABLE_CONSTRAINT = 18
    ALTER_CONSTRAINT = 19
    VALIDATE_CONSTRAINT = 19
    ALTER_COLUMN_AUTOINCREMENT = 20
    ALTER_PG_RLS = 21
    ALTER_PG_POLICY = 22
    ALTER_PG_GRANT = 23
    CREATE_STATISTICS = 24
    CREATE_TRIGGER = 24
    ALTER_VIEW = 99


@dataclass
class MigrationStatement:
    order: StatementOrder
    upgrade_sql: str
    rollback_sql: str
    rollback_kind: str = "real"
    rollback_reason: str | None = None
    category: str | None = None
    safety: str | None = None


def _assemble_migration(
    statements: list[MigrationStatement],
) -> tuple[str, str]:
    upgrade_parts: list[str] = []
    rollback_parts: list[str] = []
    for stmt in sorted(statements, key=lambda s: s.order):
        upgrade_parts.append(stmt.upgrade_sql)
        rollback_parts.append(stmt.rollback_sql)
    rollback_parts.reverse()
    return "\n\n".join(upgrade_parts), "\n\n".join(rollback_parts)
