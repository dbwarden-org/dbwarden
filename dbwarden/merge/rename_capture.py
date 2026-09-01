"""Rename capture and inheritance for merge handling.

Implements R9.1.4: Branch-time rename capture.
When make-migrations --rename-column/--rename-table is used on a branch,
the mapping is recorded in that migration's header as structured metadata.
dbwarden merge harvests these declarations automatically.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Optional

from dbwarden.logging import get_component_logger

logger = get_component_logger("merge")

# Rename metadata pattern in migration headers
_RENAME_METADATA_PATTERN = re.compile(r"^-- renames: (\[.+\])$", re.MULTILINE)


def capture_rename_intent(
    file_path: str | Path,
    renames: list[dict],
) -> None:
    """Record rename intents in a migration file's header.

    Args:
        file_path: Path to the migration file.
        renames: List of rename mappings, e.g., [{"from": "users.username", "to": "users.handle"}].
    """
    if not renames:
        return

    path = Path(file_path)
    content = path.read_text()

    # Build rename metadata line
    rename_json = json.dumps(renames, sort_keys=True)
    rename_line = f"-- renames: {rename_json}"

    # Insert after the first comment line (before -- upgrade)
    lines = content.split("\n")
    insert_idx = 0
    for i, line in enumerate(lines):
        if line.strip().startswith("--"):
            insert_idx = i + 1
        else:
            break

    lines.insert(insert_idx, rename_line)
    path.write_text("\n".join(lines))


def parse_rename_intents(file_path: str | Path) -> list[dict]:
    """Read rename intents from a migration file's header.

    Args:
        file_path: Path to the migration file.

    Returns:
        List of rename mappings.
    """
    try:
        content = Path(file_path).read_text()
    except Exception:
        return []

    match = _RENAME_METADATA_PATTERN.search(content)
    if not match:
        return []

    try:
        return json.loads(match.group(1))
    except json.JSONDecodeError:
        logger.warning("Failed to parse rename metadata in %s", file_path)
        return []


def harvest_rename_intents(migration_files: list[str | Path]) -> list[dict]:
    """Collect rename intents from multiple migration files.

    Args:
        migration_files: List of migration file paths.

    Returns:
        Combined list of rename mappings from all files.
    """
    all_renames = []
    for file_path in migration_files:
        renames = parse_rename_intents(file_path)
        all_renames.extend(renames)
    return all_renames


def format_rename_for_header(renames: list[dict]) -> str:
    """Format rename intents for inclusion in a migration header.

    Args:
        renames: List of rename mappings.

    Returns:
        Formatted string for the header.
    """
    if not renames:
        return ""
    return json.dumps(renames, sort_keys=True)
