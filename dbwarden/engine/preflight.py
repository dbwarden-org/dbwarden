"""Preflight checks for pending migrations (Phase 8.1).

Runs structured safety and impact evaluations on the exact pending batch
before any SQL executes. Scoped to migration plans, not CLI output.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any


@dataclass
class PreflightResult:
    """Outcome of preflight checks."""

    abort: bool = False
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    impact: list[dict[str, Any]] = field(default_factory=list)


def _find_plan_file(filepath: str) -> str | None:
    """Locate the plan file for a migration file.

    Plans are saved alongside migration files with .plan.json suffix.
    """
    from pathlib import Path

    plan_path = str(Path(filepath).with_suffix(".plan.json"))
    if os.path.isfile(plan_path):
        return plan_path

    return None


def _parse_plan_safety(plan: dict) -> list[dict]:
    """Extract safety-relevant operations from a plan."""
    return [
        op
        for op in (plan.get("operations") or [])
        if op.get("severity", "INFO") in ("WARNING", "ERROR", "CRITICAL")
    ]


def run_preflight(
    filepaths: dict[str, str],
    missing_plan: str = "warn",
    impact_paths: list[str] | None = None,
    migrations_dir: str | None = None,
    applied_versions: set[str] | None = None,
    force: bool = False,
) -> PreflightResult:
    """Run preflight checks on exact pending migrations.

    Args:
        filepaths: version -> filepath mapping for pending migrations.
        missing_plan: "off" | "warn" | "block" for missing plan files.
        impact_paths: Directories to scan for impact analysis.
        migrations_dir: Path to migrations directory for dependency validation.
        applied_versions: Set of applied versions for dependency validation.

    Returns:
        PreflightResult with abort=True if blocking issues found.
    """
    result = PreflightResult()

    # Section 7.1, Step 1: Validate migration files, checksums, headers, parseability
    for version, filepath in filepaths.items():
        if not os.path.isfile(filepath):
            result.abort = True
            result.errors.append(f"{version}: migration file not found: {filepath}")
            continue

        try:
            with open(filepath, encoding="utf-8") as f:
                content = f.read()
        except (OSError, UnicodeDecodeError) as exc:
            result.abort = True
            result.errors.append(f"{version}: cannot read migration file: {exc}")
            continue

        # Check for upgrade section
        try:
            from dbwarden.data.integration import load_data_plan
            load_data_plan(filepath)
        except (ValueError, OSError, TypeError) as exc:
            result.abort = True
            result.errors.append(f"{version}: invalid frozen data bundle: {exc}")
            continue
        if "-- upgrade" not in content.lower() and not content.strip():
            result.abort = True
            result.errors.append(f"{version}: migration file has no upgrade section")
            continue

    for version, filepath in filepaths.items():
        plan_path = _find_plan_file(filepath)

        if plan_path is None:
            if missing_plan == "block":
                result.abort = True
                result.errors.append(
                    f"{version}: missing plan (blocking per policy)"
                )
            elif missing_plan == "warn":
                result.warnings.append(f"{version}: no plan file found")
            continue

        try:
            with open(plan_path) as f:
                plan = json.load(f)
        except (json.JSONDecodeError, OSError) as exc:
            if missing_plan == "block":
                result.abort = True
                result.errors.append(f"{version}: malformed plan ({exc})")
            elif missing_plan == "warn":
                result.warnings.append(f"{version}: malformed plan ({exc})")
            continue

        from dbwarden.engine.safety.plans import read_trusted_plan

        trusted, _ = read_trusted_plan(filepath)
        if trusted:
            if "--force" in trusted["required_flags"] and not force:
                result.abort = True
                result.errors.append(f"{version}: acknowledgement required. hint: review the plan and pass --force")
            continue
        safety_ops = _parse_plan_safety(plan)
        for op in safety_ops:
            severity = op.get("severity", "INFO")
            op_type = op.get("type", "unknown")
            table = op.get("table", "")
            msg = f"{version}: {op_type} on {table} [{severity}]"
            if severity in ("ERROR", "CRITICAL"):
                result.abort = True
                result.errors.append(msg)
            else:
                result.warnings.append(msg)

    # Validate migration dependencies (always runs — correctness, not policy)
    if migrations_dir and not result.abort:
        from dbwarden.engine.version import validate_dependencies

        dep_errors = validate_dependencies(
            migrations_dir, applied_versions or set()
        )
        for err in dep_errors:
            if err.error_type in ("missing_target", "circular", "unmet_applied_dep"):
                result.abort = True
                result.errors.append(f"{err.version}: {err.message}")
            elif err.error_type == "superseded_dep":
                result.warnings.append(f"{err.version}: {err.message}")

    if impact_paths and not result.abort:
        result.impact = _aggregate_impact(filepaths, impact_paths)

    return result


def _aggregate_impact(
    filepaths: dict[str, str], scan_paths: list[str]
) -> list[dict[str, Any]]:
    """Aggregate impact across all pending migration plans."""
    from dbwarden.engine.impact import (
        _affected_operations,
        _extract_targets,
        parse_plan,
        scan_file_ast,
        scan_file_grep,
        _get_py_files,
    )

    all_targets: set[str] = set()
    all_ops: list[dict] = []

    for filepath in filepaths.values():
        plan_path = _find_plan_file(filepath)
        if plan_path is None:
            continue
        try:
            plan = parse_plan(plan_path)
            ops = _affected_operations(plan, verbose=False)
            all_ops.extend(ops)
            all_targets.update(_extract_targets(ops))
        except (json.JSONDecodeError, OSError):
            continue

    if not all_targets or not all_ops:
        return []

    py_files: list[str] = []
    for sp in scan_paths:
        if os.path.isdir(sp):
            py_files.extend(_get_py_files(sp))

    refs: list[dict] = []
    for fpath in py_files:
        refs.extend(scan_file_ast(fpath, sorted(all_targets)))
        refs.extend(scan_file_grep(fpath, sorted(all_targets)))

    refs_by_target: dict[str, list[dict]] = {}
    for r in refs:
        snippet = r.get("snippet", "").lower()
        for t in all_targets:
            if t.lower() in snippet:
                refs_by_target.setdefault(t, []).append(r)

    impact: list[dict] = []
    seen_tables: set[str] = set()
    for op in all_ops:
        table = op.get("table") or op.get("old_table") or ""
        if table and table not in seen_tables:
            seen_tables.add(table)
            op_refs = refs_by_target.get(table, [])
            if op_refs:
                impact.append(
                    {
                        "operation_type": op.get("type"),
                        "table": table,
                        "references": op_refs[:5],
                    }
                )

    return impact
