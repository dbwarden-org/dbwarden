"""DBWarden merge handling.

This package implements branch merge support for dbwarden, including:
- Superseded markers for migrations
- Reconciliation migration headers
- Merge detection signals
- Environment registry
- Git utilities
- Rename capture and inheritance
"""
from dbwarden.merge.marker import (
    SupersededMarker,
    parse_superseded_marker,
    write_superseded_marker,
    is_superseded,
    get_file_checksum,
)
from dbwarden.merge.reconciliation import (
    ReconciliationHeader,
    parse_reconciliation_header,
    write_reconciliation_header,
    is_reconciliation,
)
from dbwarden.merge.detection import (
    MergeSignal,
    detect_merge_signals,
)
from dbwarden.merge.environments import (
    EnvironmentConfig,
    load_environments,
    is_persistent,
    get_persistent_environments,
)

__all__ = [
    "SupersededMarker",
    "parse_superseded_marker",
    "write_superseded_marker",
    "is_superseded",
    "get_file_checksum",
    "ReconciliationHeader",
    "parse_reconciliation_header",
    "write_reconciliation_header",
    "is_reconciliation",
    "MergeSignal",
    "detect_merge_signals",
    "EnvironmentConfig",
    "load_environments",
    "is_persistent",
    "get_persistent_environments",
]
