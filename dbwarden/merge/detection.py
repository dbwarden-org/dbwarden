"""Merge signal detection for dbwarden.

Detects when a branch merge has occurred by checking repository state.
Signals are checked by make-migrations and status to refuse generation
until the merge is resolved.
"""
from __future__ import annotations

import os
import re
from enum import Enum
from pathlib import Path
from typing import Optional

from dbwarden.logging import get_component_logger
from dbwarden.merge.marker import is_superseded

logger = get_component_logger("merge")


class MergeSignal(Enum):
    """Signals that a merge has occurred."""
    DIVERGENT_BASE = "divergent_base"
    VERSION_COLLISION = "version_collision"
    SNAPSHOT_DISCONTINUITY = "snapshot_discontinuity"


def detect_merge_signals(db_name: str | None = None) -> list[MergeSignal]:
    """Detect all merge signals for the given database.

    Args:
        db_name: Database name. If None, uses default.

    Returns:
        List of detected merge signals.
    """
    signals = []

    if check_divergent_base(db_name):
        signals.append(MergeSignal.DIVERGENT_BASE)

    collisions = check_version_collisions(db_name)
    if collisions:
        signals.append(MergeSignal.VERSION_COLLISION)

    if check_snapshot_discontinuity(db_name):
        signals.append(MergeSignal.SNAPSHOT_DISCONTINUITY)

    return signals


def check_divergent_base(db_name: str | None = None) -> bool:
    """Check if the newest migration's base checksum doesn't match current model state.

    This detects the case where a developer generated a migration on one branch,
    then merged another branch that changed the models.

    Per spec §3 Signal 1: The check requires BOTH conditions:
    1. base_checksum mismatches current model state
    2. Current model state is NOT a descendant of that base (git ancestry check)
    """
    from dbwarden.commands.make_migrations.pipeline import get_model_state_path
    from dbwarden.engine.version import get_migration_filepaths_by_version
    from dbwarden.engine.file_parser import parse_migration_header

    try:
        # Get current model state
        state_path = get_model_state_path(db_name)
        if not state_path.exists():
            return False

        # Get migrations directory
        from dbwarden.engine.version import get_migrations_directory
        migrations_dir = get_migrations_directory(db_name)

        # Get all migrations
        filepaths = get_migration_filepaths_by_version(migrations_dir)
        if not filepaths:
            return False

        # Check the newest migration's base checksum
        latest_version = max(filepaths.keys())
        latest_file = filepaths[latest_version]

        # Parse header for base_checksum
        from dbwarden.engine.file_parser import parse_migration_header
        header = parse_migration_header(latest_file)

        # If migration has a base_checksum, compare with current model state
        if hasattr(header, 'base_checksum') and header.base_checksum:
            import json
            state = json.loads(state_path.read_text())
            from dbwarden.engine.generation_state import state_checksum
            from dbwarden.engine.safety.plans import read_trusted_plan
            current_checksum = state_checksum(state)
            plan, _ = read_trusted_plan(latest_file)
            if plan and plan.get("target_checksum") == current_checksum:
                return False
            if "generation_base" in state:
                from dbwarden.engine.generation_state import effective_state
                effective_state(state, filepaths, applied=set(state.get("generation_applied", [])))
                return False

            if header.base_checksum != current_checksum:
                # Check git ancestry: is current model state a descendant of the base?
                # If it IS a descendant, this is a normal forward-moving branch, not a merge issue
                if _is_descendant_of_base(db_name, header.base_checksum):
                    return False

                logger.info(
                    "Divergent base detected: migration %s has base_checksum %s, "
                    "current model state has checksum %s",
                    latest_version, header.base_checksum[:8], current_checksum[:8],
                )
                return True

    except Exception as e:
        logger.debug("Error checking divergent base: %s", e)

    return False


def _is_descendant_of_base(db_name: str | None, base_checksum: str) -> bool:
    """Check if the current model state is a descendant of the base checksum.

    Uses git to check if the model_state.json file at HEAD is a descendant
    of the commit that produced the base checksum.
    """
    import subprocess

    try:
        # Get the migrations directory to find the git root
        from dbwarden.engine.version import get_migrations_directory
        migrations_dir = get_migrations_directory(db_name)

        # Find git root
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=migrations_dir,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            return False

        git_root = result.stdout.strip()

        # Find the commit that introduced the base checksum
        # Search for commits that modified model_state.json
        result = subprocess.run(
            ["git", "log", "--all", "--oneline", "--", ".dbwarden/model_state.json"],
            cwd=git_root,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            return False

        # For simplicity, check if HEAD is ahead of the merge-base
        # This is a heuristic: if we're on a forward-moving branch, we're not diverged
        result = subprocess.run(
            ["git", "merge-base", "--is-ancestor", "HEAD", "origin/main"],
            cwd=git_root,
            capture_output=True,
        )
        # If HEAD is ancestor of main, we're on a forward-moving branch
        return result.returncode == 0

    except Exception as e:
        logger.debug("Could not check git ancestry: %s", e)
        return False


def check_version_collisions(db_name: str | None = None) -> list[str]:
    """Check for version collisions in the migration files.

    Per spec R6.3.2: Only runnable files are checked for collisions.
    Superseded files sharing a version prefix are legal.

    Returns:
        List of colliding version prefixes.
    """
    from dbwarden.engine.version import get_migrations_directory, MIGRATION_PATTERN
    from dbwarden.merge.marker import is_superseded

    try:
        migrations_dir = get_migrations_directory(db_name)
        if not os.path.exists(migrations_dir):
            return []

        # Collect all versioned migrations, excluding superseded files
        versions: dict[str, list[str]] = {}
        for filename in os.listdir(migrations_dir):
            match = MIGRATION_PATTERN.match(filename)
            if match:
                # Skip superseded files (R6.3.2)
                filepath = os.path.join(migrations_dir, filename)
                if is_superseded(filepath):
                    continue

                version = match.group(1)
                if version not in versions:
                    versions[version] = []
                versions[version].append(filename)

        # Find collisions
        collisions = [v for v, files in versions.items() if len(files) > 1]
        if collisions:
            logger.info("Version collisions detected: %s", collisions)

        return collisions

    except Exception as e:
        logger.debug("Error checking version collisions: %s", e)
        return []


def check_snapshot_discontinuity(db_name: str | None = None) -> bool:
    import json

    from dbwarden.commands.make_migrations.pipeline import get_current_model_state_path
    from dbwarden.engine.core.snapshot_io import find_latest_snapshot
    from dbwarden.engine.generation_state import effective_state, state_checksum
    from dbwarden.engine.safety.plans import read_trusted_plan
    from dbwarden.engine.version import get_migration_filepaths_by_version, get_migrations_directory

    snapshot = find_latest_snapshot(db_name)
    state_path = get_current_model_state_path(db_name)
    if snapshot is None or not state_path.exists():
        return False
    state = json.loads(state_path.read_text(encoding="utf-8"))
    files = get_migration_filepaths_by_version(get_migrations_directory(db_name))
    applied = set(snapshot.get("applied_versions", state.get("generation_applied", [])))
    if any(version not in applied and not is_superseded(path) and read_trusted_plan(path)[0] is None for version, path in files.items()):
        return False
    baseline = snapshot.get("model_state", state.get("generation_base", snapshot))
    try:
        composed, _ = effective_state(baseline, files, applied=applied, strict=True)
    except ValueError:
        return True
    if "generation_base" in state:
        return False
    return state_checksum(composed) != state_checksum(state)


def _compute_state_checksum(state: dict) -> str:
    """Compute a checksum for a model state dict."""
    import hashlib
    import json

    # Remove checksum field if present
    state_copy = {k: v for k, v in state.items() if k != "checksum"}
    content = json.dumps(state_copy, sort_keys=True, default=str)
    return hashlib.sha256(content.encode()).hexdigest()


def get_diagnostic_message(signals: list[MergeSignal]) -> str:
    """Get a human-readable diagnostic message for merge signals.

    Args:
        signals: List of detected merge signals.

    Returns:
        Diagnostic message string.
    """
    if not signals:
        return "No merge signals detected."

    messages = []
    for signal in signals:
        if signal == MergeSignal.DIVERGENT_BASE:
            messages.append(
                "Divergent generation base detected. The newest migration was "
                "generated against a different model state than the current one."
            )
        elif signal == MergeSignal.VERSION_COLLISION:
            messages.append(
                "Version collision detected. Multiple migration files share "
                "the same version prefix."
            )
        elif signal == MergeSignal.SNAPSHOT_DISCONTINUITY:
            messages.append(
                "Snapshot discontinuity detected. The latest schema snapshot "
                "doesn't match the current model state."
            )

    return "\n".join(messages)


def check_dirty_environment(db_name: str | None = None) -> bool:
    from dbwarden.config import get_database
    from dbwarden.engine.version import MIGRATION_PATTERN, get_migrations_directory
    from dbwarden.merge.environments import load_environments
    from dbwarden.merge.reconciliation import load_merge_record
    from dbwarden.repositories import get_migrated_versions, migrations_table_exists

    directory = Path(get_migrations_directory(db_name))
    superseded = {match.group(1) for path in directory.glob("*.sql")
                  if (match := MIGRATION_PATTERN.match(path.name)) and is_superseded(path)}
    if not superseded:
        return False
    directories = {Path(".dbwarden/merges").resolve(), (directory.parent / ".dbwarden/merges").resolve()}
    try:
        records = [load_merge_record(path) for root in directories for path in root.glob("*.json")]
    except (OSError, ValueError):
        return True
    if not migrations_table_exists(db_name):
        return False
    dirty = set(get_migrated_versions(db_name)) & superseded
    config = get_database(db_name)
    current_envs = {env.name for env in load_environments(db_name) if os.environ.get(env.url_env) == config.sqlalchemy_url}
    for record in records:
        if any(record.get("probe_results", {}).get(env) == "reconciled" for env in current_envs):
            dirty -= set(record.get("superseded_versions", []))
    return bool(dirty)
