from __future__ import annotations

import re
from pathlib import Path

from dbwarden.engine.snapshot import IRREVERSIBLE_ANNOTATION
from dbwarden.output import error, success, warning
from dbwarden.sql import split_sql_statements
from dbwarden.files import atomic_write_text


def _reverse_sql(sql: str) -> str:
    stripped = sql.strip()
    upper = stripped.upper()
    if upper.startswith("CREATE TABLE"):
        match = re.search(r"CREATE TABLE\s+(?:IF NOT EXISTS\s+)?(\S+)", stripped, re.IGNORECASE)
        if match:
            table_name = match.group(1).rstrip("(").strip()
            return f"DROP TABLE IF EXISTS {table_name};"
    elif upper.startswith("CREATE MATERIALIZED VIEW"):
        match = re.search(r"CREATE MATERIALIZED VIEW\s+(?:IF NOT EXISTS\s+)?(\S+)", stripped, re.IGNORECASE)
        if match:
            view_name = match.group(1).rstrip("(").strip()
            return f"DROP VIEW IF EXISTS {view_name};"
    elif upper.startswith("CREATE DICTIONARY"):
        match = re.search(r"CREATE DICTIONARY\s+(?:IF NOT EXISTS\s+)?(\S+)", stripped, re.IGNORECASE)
        if match:
            dict_name = match.group(1).rstrip("(").strip()
            return f"DROP DICTIONARY IF EXISTS {dict_name};"
    elif upper.startswith("ALTER TABLE"):
        alter_match = re.search(
            r"ALTER TABLE\s+(\S+)\s+ADD\s+COLUMN(?:\s+IF NOT EXISTS)?\s+(\S+)",
            stripped,
            re.IGNORECASE,
        )
        if alter_match:
            table_name = alter_match.group(1)
            column_name = alter_match.group(2)
            return f"ALTER TABLE {table_name} DROP COLUMN {column_name};"
    elif upper.startswith("CREATE INDEX"):
        match = re.search(
            r"CREATE INDEX\s+(?:CONCURRENTLY\s+)?(?:IF NOT EXISTS\s+)?(\S+)",
            stripped, re.IGNORECASE,
        )
        if match:
            index_name = match.group(1)
            return f"DROP INDEX IF EXISTS {index_name};"
    elif upper.startswith("CREATE UNIQUE INDEX"):
        match = re.search(
            r"CREATE UNIQUE INDEX\s+(?:CONCURRENTLY\s+)?(?:IF NOT EXISTS\s+)?(\S+)",
            stripped, re.IGNORECASE,
        )
        if match:
            index_name = match.group(1)
            return f"DROP INDEX IF EXISTS {index_name};"

    return f"-- No automatic rollback generated for:\n-- {sql}"


def make_rollback_cmd(migration_file: str) -> None:
    path = Path(migration_file)
    if not path.exists():
        error(f"Migration file not found: {migration_file}")
        return

    content = path.read_text(encoding="utf-8")

    upgrade_section = ""
    in_upgrade = False
    for line in content.splitlines(keepends=True):
        stripped = line.strip()
        if stripped == "-- upgrade":
            in_upgrade = True
            continue
        if stripped == "-- rollback":
            break
        if in_upgrade:
            upgrade_section += line

    if not upgrade_section.strip():
        warning("No upgrade SQL found in migration file.")
        return

    statements = [s for s in split_sql_statements(upgrade_section) if not s.lstrip().startswith("--")]
    rollback_statements = [_reverse_sql(s) for s in statements]
    has_placeholder = any(stmt.lstrip().startswith("-- No automatic rollback generated") for stmt in rollback_statements)
    if has_placeholder and IRREVERSIBLE_ANNOTATION not in content:
        error(
            "Automatic rollback would contain placeholder SQL. Add "
            f"'-- {IRREVERSIBLE_ANNOTATION}' only if this migration is intentionally irreversible."
        )
        return

    out_path = path.with_suffix(".rollback.sql")
    atomic_write_text(
        out_path,
        "-- rollback\n\n" + "\n\n".join(rollback_statements) + "\n",
    )

    success(f"Created rollback file: {out_path}")
