from __future__ import annotations

from dbwarden.engine.safety import issues_to_json, load_issues
from dbwarden.exceptions import DBDisconnectedError
from dbwarden.output import data_table, plain, render, success, warning


def check_cmd(
    output_format: str = "txt",
    database: str | None = None,
    force: bool = False,
) -> None:
    try:
        issues = load_issues(database=database)
    except DBDisconnectedError:
        warning(
            "Database disconnected - cannot compare against live schema. "
            "Run with models only (no live compatibility checks)."
        )
        return

    if output_format == "json":
        plain(issues_to_json(issues))
    elif output_format == "txt":
        _print_issues_table(issues, database=database)
    else:
        raise ValueError(f"Unknown output format: {output_format}")

    errors = [issue for issue in issues if issue.severity.upper() == "ERROR"]
    warnings = [issue for issue in issues if issue.severity.upper() == "WARNING"]
    if errors:
        raise RuntimeError("Safety check failed: blocking changes detected.")
    if warnings and not force:
        raise RuntimeError("Safety check failed: warning-level changes require --force.")


def _print_issues_table(issues, database: str | None = None) -> None:
    db_label = database or "default"
    render(
        data_table(
            f"Safety Check - {db_label}",
            ("Severity", "Change", "Table", "Column", "Message", "Required Flag"),
            (
                (
                    issue.severity,
                    issue.change_type,
                    issue.table_name,
                    issue.column_name or "",
                    issue.message,
                    issue.required_flag or "",
                )
                for issue in issues
            ),
        )
    )
    if not issues:
        success("No schema changes detected.")
