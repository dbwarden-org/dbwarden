from __future__ import annotations

import json
import os

from dbwarden.engine.impact import analyze_impact
from dbwarden.engine.version import get_migration_filepaths_by_version, get_migrations_directory
from dbwarden.output import data_table, error, info, plain, render, success, warning


def check_impact_cmd(
    migration: str,
    out: str = "text",
    scan_path: str = ".",
    deep: bool = False,
    verbose: bool = False,
    database: str | None = None,
) -> None:
    plan_path = _resolve_plan_path(migration, database)
    if not plan_path:
        return

    result = analyze_impact(
        plan_path=plan_path,
        scan_path=scan_path,
        deep=deep,
        verbose=verbose,
    )

    if out == "json":
        plain(json.dumps(result, indent=2, default=str))
        return

    impact = result.get("impact", [])
    if not impact:
        success("No impact detected")
        info(f"Scanned: {scan_path}")
        return

    info(f"Migration: {result.get('migration_id', plan_path)}")
    warning(f"Impact detected: {len(impact)} operation(s) affect code")

    for item in impact:
        op_type = item.get("operation_type", "?")
        table = item.get("table", "")
        column = item.get("column", "")
        refs = item.get("references", [])

        label = f"{op_type}"
        if table:
            label += f" on {table}"
        if column:
            label += f".{column}"
        warning(label)
        info(f"References: {len(refs)}")

        rows = []
        for ref in refs[:10]:
            fpath = ref.get("file", "")
            line = ref.get("line", 0)
            snippet = ref.get("snippet", "")
            kind = ref.get("kind", "")
            short_path = os.path.relpath(fpath, scan_path) if os.path.isabs(fpath) else fpath
            rows.append((f"{short_path}:{line}", kind, snippet))
        if rows:
            render(data_table(None, ("Location", "Kind", "Snippet"), rows))

        if len(refs) > 10:
            info(f"... and {len(refs) - 10} more")


def _resolve_plan_path(migration: str, database: str | None = None) -> str | None:
    if migration.endswith(".plan.json") and os.path.isfile(migration):
        return migration

    if os.path.isfile(migration):
        base, _ext = os.path.splitext(migration)
        plan_path = base + ".plan.json"
        if os.path.isfile(plan_path):
            return plan_path
        error(f"Plan file not found: {plan_path}")
        return None

    try:
        migrations_dir = get_migrations_directory(database)
    except Exception as exc:
        error(str(exc))
        return None

    filepaths = get_migration_filepaths_by_version(migrations_dir)
    if migration in filepaths:
        sql_path = filepaths[migration]
        base, _ext = os.path.splitext(sql_path)
        plan_path = base + ".plan.json"
        if os.path.isfile(plan_path):
            return plan_path

    for ver, sql_path in sorted(filepaths.items()):
        if ver.startswith(migration):
            base, _ext = os.path.splitext(sql_path)
            plan_path = base + ".plan.json"
            if os.path.isfile(plan_path):
                return plan_path

    error(f"No plan file found for migration: {migration}")
    warning(f"Migrations directory: {migrations_dir}")
    return None
