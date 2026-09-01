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
            current_checksum = _compute_state_checksum(state)

            if header.base_checksum != current_checksum:
                logger.info(
                    "Divergent base detected: migration %s has base_checksum %s, "
                    "current model state has checksum %s",
                    latest_version, header.base_checksum[:8], current_checksum[:8],
                )
                return True

    except Exception as e:
        logger.debug("Error checking divergent base: %s", e)

    return False


def check_version_collisions(db_name: str | None = None) -> list[str]:
    """Check for version collisions in the migration files.

    Returns:
        List of colliding version prefixes.
    """
    from dbwarden.engine.version import get_migrations_directory, MIGRATION_PATTERN

    try:
        migrations_dir = get_migrations_directory(db_name)
        if not os.path.exists(migrations_dir):
            return []

        # Collect all versioned migrations
        versions: dict[str, list[str]] = {}
        for filename in os.listdir(migrations_dir):
            match = MIGRATION_PATTERN.match(filename)
            if match:
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
    """Check if the latest snapshot doesn't match the model state implied by the runnable chain.

    This detects the case where migrations were applied but the snapshot
    wasn't updated, or where the snapshot is stale.
    """
    from dbwarden.engine.core.snapshot_io import find_latest_snapshot, compute_checksum
    from dbwarden.commands.make_migrations.pipeline import get_model_state_path

    try:
        # Get latest snapshot
        snapshot = find_latest_snapshot(db_name)
        if snapshot is None:
            return False

        # Get current model state
        state_path = get_model_state_path(db_name)
        if not state_path.exists():
            return False

        # Compare checksums
        snapshot_checksum = compute_checksum(snapshot)

        import json
        state = json.loads(state_path.read_text())
        state_checksum = _compute_state_checksum(state)

        if snapshot_checksum != state_checksum:
            logger.info(
                "Snapshot discontinuity: snapshot checksum %s != model state checksum %s",
                snapshot_checksum[:8], state_checksum[:8],
            )
            return True

    except Exception as e:
        logger.debug("Error checking snapshot discontinuity: %s", e)

    return False


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
