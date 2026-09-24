"""Declarative data migrations, frozen at generation and executed from SQL."""

from .advanced import aggregate, from_source, merge_sources, winner
from .batches import batch
from .declarations import (
    ArchiveTable,
    DataMeta,
    DataTransition,
    archive_table,
    derive,
    historical_table,
    into,
    rows,
    validate,
)
from .expressions import case, cast, col, func, literal, mapping, param

__all__ = [
    "ArchiveTable",
    "DataMeta",
    "DataTransition",
    "aggregate",
    "archive_table",
    "batch",
    "case",
    "cast",
    "col",
    "derive",
    "from_source",
    "func",
    "historical_table",
    "into",
    "literal",
    "mapping",
    "merge_sources",
    "param",
    "rows",
    "validate",
    "winner",
]
