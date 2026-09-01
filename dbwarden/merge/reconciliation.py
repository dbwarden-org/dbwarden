"""Reconciliation migration header parser and writer.

Implements the reconciliation migration header format from the merge spec (§6.2):
- Added to migration files generated at merge time
- Contains merge metadata for audit and recovery
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from dbwarden.logging import get_component_logger

logger = get_component_logger("merge")

# Header patterns
_HEADER_START = "-- dbwarden:merge-reconciliation"
_HEADER_PATTERN = re.compile(r"^-- (\w[\w-]*): (.+)$")


@dataclass
class ReconciliationHeader:
    """Represents a reconciliation migration header.

    Attributes:
        merge_base: Version of the merge-base head.
        merge_base_checksum: State checksum at merge-base.
        supersedes: List of filenames that were superseded.
        probe_results: Dict of environment -> clean/dirty/unknown.
        generated_by: Generator string (e.g., "dbwarden merge (0.18.0)").
        renames: Optional list of confirmed rename mappings.
    """
    merge_base: str
    merge_base_checksum: str
    supersedes: list[str]
    probe_results: dict[str, str]
    generated_by: str
    renames: list[dict] | None = None


def parse_reconciliation_header(file_path: str | Path) -> Optional[ReconciliationHeader]:
    """Parse a reconciliation header from a migration file.

    Args:
        file_path: Path to the migration file.

    Returns:
        ReconciliationHeader if found, None otherwise.
    """
    try:
        lines = Path(file_path).read_text().splitlines()
    except Exception as e:
        logger.warning("Failed to read %s: %s", file_path, e)
        return None

    if not lines or not lines[0].strip().startswith(_HEADER_START):
        return None

    # Parse header fields
    fields = {}
    for line in lines[1:20]:
        line = line.strip()
        if not line.startswith("--"):
            break
        match = _HEADER_PATTERN.match(line)
        if match:
            key, value = match.group(1), match.group(2)
            fields[key] = value

    # Parse merge-base with checksum
    merge_base = fields.get("merge-base", "")
    merge_base_checksum = ""
    base_match = re.match(r"(\S+)\s+\(state checksum (\S+)\)", merge_base)
    if base_match:
        merge_base = base_match.group(1)
        merge_base_checksum = base_match.group(2)

    # Parse supersedes list
    supersedes_str = fields.get("supersedes", "")
    supersedes = [f.strip() for f in supersedes_str.split(",") if f.strip()]

    # Parse probe results
    probe_str = fields.get("probe", "")
    probe_results = {}
    if probe_str:
        for part in probe_str.split(","):
            part = part.strip()
            if "=" in part:
                env, result = part.split("=", 1)
                probe_results[env.strip()] = result.strip()

    return ReconciliationHeader(
        merge_base=merge_base,
        merge_base_checksum=merge_base_checksum,
        supersedes=supersedes,
        probe_results=probe_results,
        generated_by=fields.get("generated-by", "unknown"),
    )


def write_reconciliation_header(
    file_path: str | Path,
    header: ReconciliationHeader,
) -> None:
    """Write a reconciliation header to the beginning of a migration file.

    Args:
        file_path: Path to the migration file.
        header: The header to write.
    """
    path = Path(file_path)
    content = path.read_text()

    # Build probe string
    probe_parts = [f"{env}={result}" for env, result in header.probe_results.items()]
    probe_str = ", ".join(probe_parts) if probe_parts else "none"

    # Build supersedes string
    supersedes_str = ", ".join(header.supersedes) if header.supersedes else "none"

    # Build header block
    header_block = "\n".join([
        _HEADER_START,
        f"-- merge-base: {header.merge_base} (state checksum {header.merge_base_checksum})",
        f"-- supersedes: {supersedes_str}",
        f"-- probe: {probe_str}",
        f"-- generated-by: {header.generated_by}",
        "",
    ])

    # Insert header at the beginning
    new_content = header_block + content
    path.write_text(new_content)


def is_reconciliation(file_path: str | Path) -> bool:
    """Check if a migration file has a reconciliation header.

    Args:
        file_path: Path to the migration file.

    Returns:
        True if the file has a reconciliation header.
    """
    try:
        header = parse_reconciliation_header(file_path)
        return header is not None
    except Exception:
        return False
