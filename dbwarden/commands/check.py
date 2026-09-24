from __future__ import annotations

from dbwarden.engine.safety import issues_to_json, load_issues
from dbwarden.exceptions import DBDisconnectedError
from dbwarden.output import data_table, plain, render, success, warning


def check_cmd(
    output_format: str = "txt",
    database: str | None = None,
    force: bool = False,
    write_plan: bool = False,
    all_files: bool = False,
    version: str | None = None,
    data: bool = False,
) -> None:
    if write_plan:
        return _write_plans(database, all_files, version, output_format)
    if all_files or version:
        raise ValueError("--all and version require --write-plan. hint: use check --write-plan --all")
    try:
        issues = load_issues(database=database)
    except DBDisconnectedError:
        raise
    if data:
        from dbwarden.data.convergence import project_data_findings
        from dbwarden.models import SafetyIssue
        data_findings = project_data_findings(database)
        issues.extend(SafetyIssue("ERROR", "data_drift", item["declaration_id"], item["message"]) for item in data_findings)

    locations = _plan_locations(database)
    if output_format == "json":
        import json
        payload = json.loads(issues_to_json(issues))
        for item in payload:
            item["migration_files"] = _issue_files(item["change_type"], item["table_name"], item["column_name"], locations)
        plain(json.dumps(payload, indent=2))
    elif output_format == "txt":
        _print_issues_table(issues, database=database, locations=locations)
    else:
        raise ValueError(f"Unknown output format: {output_format}")

    errors = [issue for issue in issues if issue.severity.upper() == "ERROR"]
    if data and data_findings:
        raise RuntimeError("Data convergence failed; --force cannot bypass data drift")
    warnings = [issue for issue in issues if issue.severity.upper() == "WARNING"]
    if any(issue.severity == "UNKNOWN" for issue in issues):
        raise RuntimeError("Safety check contains unclassified changes. hint: review unsupported operations")
    if errors and not force:
        raise RuntimeError("Safety check failed: blocking changes detected.")
    if warnings and not force:
        raise RuntimeError("Safety check failed: warning-level changes require --force.")


def _write_plans(database, all_files, version, output_format):
    import json
    from pathlib import Path

    import typer

    from dbwarden.config import get_database, get_multi_db_config
    from dbwarden.engine.safety.static import classify_file
    from dbwarden.engine.version import (
        get_migration_filepaths_by_version,
        get_migrations_directory,
    )
    from dbwarden.merge.marker import is_superseded

    if all_files and version:
        raise ValueError("Choose --all or a version. hint: check --write-plan 0001")
    if not all_files and version is None:
        raise ValueError("Specify a version or --all. hint: check --write-plan --all")
    databases = [database] if database else list(get_multi_db_config().databases) if all_files else [get_multi_db_config().default]
    reports = []
    unresolved = False
    for name in databases:
        config = get_database(name)
        directory = Path(get_migrations_directory(name))
        if all_files:
            paths = sorted(directory.glob("*.sql"))
        else:
            files = get_migration_filepaths_by_version(str(directory))
            key = version.zfill(4)
            if key not in files:
                raise ValueError(f"Migration {version} not found. hint: check the database and version")
            paths = [Path(files[key])]
        report = {"database": name, "classified": [], "unchanged": [], "unresolved": [], "severity_counts": {level: 0 for level in ("SAFE", "INFO", "WARN", "CRITICAL")}}
        for path in paths:
            if is_superseded(path):
                continue
            level, reason = classify_file(path, config.database_type)
            entry = {"filename": path.name, "severity": level}
            if level == "UNKNOWN":
                report["unresolved"].append({**entry, "reason": reason})
                unresolved = True
            else:
                report[reason].append(entry)
                report["severity_counts"][level] += 1
        reports.append(report)
    if output_format == "json":
        plain(json.dumps(reports, indent=2))
    else:
        for report in reports:
            plain(f"Static classification — {report['database']}")
            plain(f"  {len(report['classified'])} files classified; {len(report['unchanged'])} unchanged")
            plain("  " + " · ".join(f"{level} {count}" for level, count in report["severity_counts"].items()))
            plain(f"  {len(report['unresolved'])} files unresolved:")
            for entry in report["unresolved"]:
                plain(f"    {entry['filename']} ({entry['reason']})")
            for entry in report["classified"] + report["unchanged"]:
                if entry["severity"] == "CRITICAL":
                    plain(f"    Review CRITICAL: {entry['filename']}")
    if unresolved:
        raise typer.Exit(code=4)


def _plan_locations(database):
    from pathlib import Path

    from dbwarden.engine.safety.plans import read_trusted_plan
    from dbwarden.engine.version import get_migrations_directory
    from dbwarden.exceptions import ConfigurationError, DirectoryNotFoundError
    from dbwarden.merge.marker import is_superseded

    result = {}
    try:
        directory = Path(get_migrations_directory(database))
    except (ConfigurationError, DirectoryNotFoundError):
        return result
    for path in directory.glob("*.sql"):
        if is_superseded(path):
            continue
        plan, _ = read_trusted_plan(path)
        if plan:
            for op in plan["severity"]["ops"]:
                result.setdefault((op["kind"], op.get("table"), op.get("column")), []).append(path.name)
    return result


def _issue_files(kind, table, column, locations):
    kind = kind.replace("change_", "alter_", 1)
    return locations.get((kind, table, column), [])


def _print_issues_table(issues, database: str | None = None, locations=None) -> None:
    db_label = database or "default"
    render(
        data_table(
            f"Safety Check - {db_label}",
            ("Severity", "Change", "Table", "Column", "Message", "Required Flag", "Migration Files"),
            (
                (
                    issue.severity,
                    issue.change_type,
                    issue.table_name,
                    issue.column_name or "",
                    issue.message,
                    issue.required_flag or "",
                    ", ".join(_issue_files(issue.change_type, issue.table_name, issue.column_name, locations or {})),
                )
                for issue in issues
            ),
        )
    )
    if not issues:
        success("No schema changes detected.")
