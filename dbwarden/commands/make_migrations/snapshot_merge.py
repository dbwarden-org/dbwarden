import os
import re
from typing import Any


_ALTER_ADD_COLUMN_RE = re.compile(
    r"ALTER\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?(\S+)\s+ADD\s+(?:COLUMN\s+)?(?!CONSTRAINT\b|PRIMARY\b|FOREIGN\b|UNIQUE\b|CHECK\b|EXCLUDE\b)(\S+)",
    re.IGNORECASE,
)

# `ALTER TABLE t ADD <x>` is a column only when <x> is not one of these: the
# COLUMN keyword is optional, so ADD CONSTRAINT / ADD PRIMARY KEY / ADD UNIQUE
# otherwise read as a column literally named "CONSTRAINT" or "PRIMARY".
_NOT_A_COLUMN_NAME: frozenset[str] = frozenset({
    "constraint",
    "primary",
    "foreign",
    "unique",
    "check",
    "exclude",
    "index",
})

_DROP_TABLE_RE = re.compile(r"DROP\s+TABLE\s+(?:IF\s+EXISTS\s+)?(\S+)", re.IGNORECASE)

_RENAME_TABLE_RE = re.compile(
    r"ALTER\s+TABLE\s+(?:IF\s+EXISTS\s+)?(\S+)\s+RENAME\s+TO\s+(\S+)", re.IGNORECASE
)


def _identifier(raw: str) -> str:
    return raw.strip().rstrip(";").strip('"`\'')


def _merge_pending_migrations_into_snapshot(
    snapshot: dict[str, Any],
    migrations_dir: str,
) -> None:
    from dbwarden.engine.file_parser import parse_upgrade_statements
    from dbwarden.engine.discovery import _extract_create_table_columns

    if not os.path.exists(migrations_dir):
        return

    tables = snapshot.setdefault("tables", {})

    # Tables this merge invented from migration text. A statement later in the
    # same file may drop or rename one of them - a SQLite table rebuild creates
    # a staging table, copies into it, drops the original and renames the
    # staging table over it - and without following that through, the staging
    # table survives in the snapshot as a table the models do not declare. The
    # next diff then proposes to drop it, in SQL built from columns typed
    # "unknown". Only merge-added tables are followed: the live database
    # remains the authority on everything it already reported.
    added: set[str] = set()

    for filename in sorted(os.listdir(migrations_dir)):
        if not filename.endswith(".sql"):
            continue
        filepath = os.path.join(migrations_dir, filename)
        statements = parse_upgrade_statements(filepath)

        for stmt in statements:
            stmt = stmt.strip()
            if not stmt:
                continue

            dropped = _DROP_TABLE_RE.match(stmt)
            if dropped:
                name = _identifier(dropped.group(1))
                if name in added:
                    tables.pop(name, None)
                    added.discard(name)
                continue

            renamed = _RENAME_TABLE_RE.match(stmt)
            if renamed:
                old_name = _identifier(renamed.group(1))
                new_name = _identifier(renamed.group(2))
                if old_name in added:
                    entry = tables.pop(old_name)
                    added.discard(old_name)
                    # A rename onto a table the database already reported keeps
                    # the real definition; the staging entry only ever held
                    # placeholder column types.
                    if new_name not in tables:
                        tables[new_name] = entry
                        added.add(new_name)
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
                added.add(table_name)
                continue

            m = _ALTER_ADD_COLUMN_RE.match(stmt)
            if m:
                tbl_name = m.group(1).strip('"`\'')
                col_name = m.group(2).strip('"`\'')
                if col_name.lower() in _NOT_A_COLUMN_NAME:
                    continue
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
                    added.add(tbl_name)
