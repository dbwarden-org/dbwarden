"""Superseded marker parser and writer for merge handling.

Implements the superseded marker format from the merge spec (§6.1):
- Machine-readable, in-file, first-line-adjacent
- Self-describing so it cannot desync from external manifests
- Strict parser; corrupt headers = fail closed
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from dbwarden.logging import get_component_logger

logger = get_component_logger("merge")

# Marker patterns
_MARKER_START = "-- dbwarden:superseded"
_MARKER_PATTERN = re.compile(r"^-- ([\w-]+): (.+)$")


@dataclass
class SupersededMarker:
    """Represents a superseded migration marker.

    Attributes:
        merged_into: Version this migration was merged into.
        merged_at: ISO timestamp of the merge.
        merge_base: Version of the merge-base head.
        branch: Source branch name.
        applied_persistent: "none", list of envs, or "unknown:<env>".
        file_checksum: SHA-256 of file content at marking time.
    """
    merged_into: str
    merged_at: str
    merge_base: str
    branch: str
    applied_persistent: str
    file_checksum: str


def get_file_checksum(file_path: str | Path) -> str:
    """Compute SHA-256 checksum of file content.

    Args:
        file_path: Path to the file.

    Returns:
        SHA-256 hex digest prefixed with "sha256:".
    """
    content = Path(file_path).read_bytes()
    digest = hashlib.sha256(content).hexdigest()
    return f"sha256:{digest[:16]}"


def parse_superseded_marker(file_path: str | Path) -> Optional[SupersededMarker]:
    """Parse a superseded marker from a migration file.

    Args:
        file_path: Path to the migration file.

    Returns:
        SupersededMarker if found and valid, None otherwise.

    Raises:
        ValueError: If marker is present but corrupt (R6.1.1: fail closed).
    """
    try:
        lines = Path(file_path).read_text().splitlines()
    except Exception as e:
        logger.warning("Failed to read %s: %s", file_path, e)
        return None

    if not lines or not lines[0].strip().startswith(_MARKER_START):
        return None

    # Parse marker fields
    fields = {}
    for line in lines[1:20]:  # Only check first 20 lines
        line = line.strip()
        if not line.startswith("--"):
            break
        match = _MARKER_PATTERN.match(line)
        if match:
            key, value = match.group(1), match.group(2)
            fields[key] = value

    # Validate required fields (R6.1.1: fail closed on corrupt headers)
    required = ["merged-into", "merged-at", "merge-base", "branch",
                "applied-persistent", "file-checksum"]
    for field in required:
        if field not in fields:
            raise ValueError(
                f"Corrupt superseded marker in {file_path}: "
                f"missing required field '{field}'"
            )

    return SupersededMarker(
        merged_into=fields["merged-into"],
        merged_at=fields["merged-at"],
        merge_base=fields["merge-base"],
        branch=fields["branch"],
        applied_persistent=fields["applied-persistent"],
        file_checksum=fields["file-checksum"],
    )


def write_superseded_marker(
    file_path: str | Path,
    marker: SupersededMarker,
) -> None:
    """Write a superseded marker to the beginning of a migration file.

    Args:
        file_path: Path to the migration file.
        marker: The marker to write.
    """
    path = Path(file_path)
    content = path.read_text()

    # Build marker block
    marker_block = "\n".join([
        _MARKER_START,
        f"-- merged-into: {marker.merged_into}",
        f"-- merged-at: {marker.merged_at}",
        f"-- merge-base: {marker.merge_base}",
        f"-- branch: {marker.branch}",
        f"-- applied-persistent: {marker.applied_persistent}",
        f"-- file-checksum: {marker.file_checksum}",
        "",
    ])

    # Insert marker at the beginning
    new_content = marker_block + content
    path.write_text(new_content)


def is_superseded(file_path: str | Path) -> bool:
    """Check if a migration file has a superseded marker.

    Args:
        file_path: Path to the migration file.

    Returns:
        True if the file has a valid superseded marker.
    """
    try:
        marker = parse_superseded_marker(file_path)
        return marker is not None
    except ValueError:
        # Corrupt marker - treat as superseded (fail closed)
        return True


def mark_file_superseded(
    file_path: str | Path,
    merged_into: str,
    merge_base: str,
    branch: str,
    applied_persistent: str = "none",
) -> SupersededMarker:
    """Mark a migration file as superseded.

    Args:
        file_path: Path to the migration file.
        merged_into: Version this migration was merged into.
        merge_base: Version of the merge-base head.
        branch: Source branch name.
        applied_persistent: Persistent environments where this was applied.

    Returns:
        The created SupersededMarker.
    """
    checksum = get_file_checksum(file_path)
    marker = SupersededMarker(
        merged_into=merged_into,
        merged_at=datetime.now(timezone.utc).isoformat(),
        merge_base=merge_base,
        branch=branch,
        applied_persistent=applied_persistent,
        file_checksum=checksum,
    )
    write_superseded_marker(file_path, marker)
    return marker
