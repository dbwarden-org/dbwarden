import os
import re
from typing import Any


_ALTER_ADD_COLUMN_RE = re.compile(
    r"ALTER\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?(\S+)\s+ADD\s+(?:COLUMN\s+)?(?!CONSTRAINT\b|PRIMARY\b|FOREIGN\b|UNIQUE\b|CHECK\b|EXCLUDE\b)(\S+)",
    re.IGNORECASE,
)


def _merge_pending_migrations_into_snapshot(
    snapshot: dict[str, Any],
    migrations_dir: str,
) -> None:
    from dbwarden.engine.file_parser import parse_upgrade_statements
    from dbwarden.engine.discovery import _extract_create_table_columns

    if not os.path.exists(migrations_dir):
        return

    tables = snapshot.setdefault("tables", {})

    for filename in sorted(os.listdir(migrations_dir)):
        if not filename.endswith(".sql"):
            continue
        filepath = os.path.join(migrations_dir, filename)
        statements = parse_upgrade_statements(filepath)

        for stmt in statements:
            stmt = stmt.strip()
            if not stmt:
                continue
            table_name, col_names = _extract_create_table_columns(stmt)
            if table_name and col_names and table_name not in tables:
                col_dict: dict[str, dict[str, Any]] = {}
                for col_name in col_names:
                    col_dict[col_name] = {
                        "type": "unknown",
                        "nullable": True,
                        "primary_key": False,
                    }
                tables[table_name] = {
                    "columns": col_dict,
                    "primary_key": [],
                    "comment": None,
                }
                continue

            m = _ALTER_ADD_COLUMN_RE.match(stmt)
            if m:
                tbl_name = m.group(1).strip('"`\'')
                col_name = m.group(2).strip('"`\'')
                if tbl_name in tables:
                    existing_cols = tables[tbl_name].setdefault("columns", {})
                    if col_name not in existing_cols:
                        existing_cols[col_name] = {
                            "type": "unknown",
                            "nullable": True,
                            "primary_key": False,
                        }
                else:
                    tables[tbl_name] = {
                        "columns": {
                            col_name: {
                                "type": "unknown",
                                "nullable": True,
                                "primary_key": False,
                            }
                        },
                        "primary_key": [],
                        "comment": None,
                    }
