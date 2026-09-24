from __future__ import annotations

from dataclasses import dataclass
from typing import Any

_CLASS_METADATA = {
    "__annotations__",
    "__classcell__",
    "__doc__",
    "__firstlineno__",
    "__module__",
    "__qualname__",
    "__static_attributes__",
}


def _category(value) -> str | None:
    if value is not None and (
        not isinstance(value, str) or not value or "\x00" in value
    ):
        raise ValueError("category must be a nonempty string or None")
    return value


def _declarative_value(value, *, label: str):
    if callable(value):
        raise TypeError(f"{label} cannot be a Python callable")
    return value


def _normalize_transition_coverage(coverage, overlap):
    if overlap not in {"error", "fan_out", "priority"}:
        raise ValueError("overlap must be error, fan_out, or priority")
    coverage = {
        "exactly_once": "exactly_once_match",
    }.get(coverage, coverage)
    if coverage == "all" and overlap == "priority":
        coverage = "all_assigned_once"
    if coverage not in {
        "all",
        "subset",
        "exactly_once_match",
        "all_assigned_once",
    }:
        raise ValueError("Invalid transition coverage")
    if overlap == "priority" and coverage != "all_assigned_once":
        raise ValueError("priority overlap requires coverage=all_assigned_once")
    if coverage == "all_assigned_once" and overlap != "priority":
        raise ValueError("all_assigned_once coverage requires priority overlap")
    return coverage


def column_name(value) -> str:
    name = value if isinstance(value, str) else getattr(value, "name", None)
    if not isinstance(name, str) or not name or "\x00" in name:
        raise ValueError("Expected a column name or SQLAlchemy column")
    return name


def column_names(value) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str) or hasattr(value, "name"):
        value = [value]
    result = tuple(column_name(item) for item in value)
    if len(set(result)) != len(result):
        raise ValueError("Duplicate identity columns")
    return result


@dataclass(frozen=True)
class ArchiveTable:
    table: str
    schema: str | None = None


def archive_table(table: str, *, schema=None) -> ArchiveTable:
    if (
        not isinstance(table, str)
        or not table
        or "\x00" in table
        or schema is not None
        and (not isinstance(schema, str) or not schema or "\x00" in schema)
    ):
        raise ValueError("Archive tables require a table name and optional schema")
    return ArchiveTable(table, schema)


@dataclass(frozen=True)
class ManagedRows:
    key: tuple[str, ...]
    values: tuple[dict, ...] | None
    source: str | None
    owned_columns: tuple[str, ...]
    on_missing: str
    scope: Any
    acknowledge_delete: bool
    archive_to: ArchiveTable | None
    acknowledge_archive: bool
    rollback: str
    category: str | None


def rows(
    *,
    key=None,
    rows=None,
    source=None,
    owned_columns=None,
    on_missing="keep",
    scope=None,
    acknowledge_delete=False,
    archive_to=None,
    acknowledge_archive=False,
    rollback="irreversible",
    category=None,
) -> ManagedRows:
    if (rows is None) == (source is None):
        raise ValueError("rows requires exactly one of rows= or source=")
    if rows is not None and (
        not isinstance(rows, (list, tuple))
        or any(not isinstance(row, dict) for row in rows)
    ):
        raise ValueError("rows must contain literal dictionaries")
    if source is not None and (not isinstance(source, str) or not source):
        raise ValueError("source must name a static CSV or JSON file")
    if on_missing not in {"keep", "delete", "archive"}:
        raise ValueError("on_missing must be keep, delete, or archive")
    if type(acknowledge_delete) is not bool:
        raise ValueError("acknowledge_delete must be boolean")
    if on_missing == "delete" and (scope is None or acknowledge_delete is not True):
        raise ValueError(
            "Managed-row deletion requires scope= and acknowledge_delete=True"
        )
    if type(acknowledge_archive) is not bool:
        raise ValueError("acknowledge_archive must be boolean")
    if on_missing == "archive" and (
        scope is None
        or not isinstance(archive_to, ArchiveTable)
        or acknowledge_archive is not True
    ):
        raise ValueError(
            "Managed-row archive requires scope=, archive_to=archive_table(...), and acknowledge_archive=True"
        )
    if on_missing != "archive" and (
        archive_to is not None or acknowledge_archive is not False
    ):
        raise ValueError(
            "archive_to and acknowledge_archive require on_missing=archive"
        )
    if rollback not in {"irreversible", "restore_previous"}:
        raise ValueError(
            "Managed rows support irreversible or restore_previous rollback"
        )
    return ManagedRows(
        column_names(key),
        None if rows is None else tuple(dict(row) for row in rows),
        source,
        column_names(owned_columns),
        on_missing,
        _declarative_value(scope, label="scope"),
        acknowledge_delete,
        archive_to,
        acknowledge_archive,
        rollback,
        _category(category),
    )


@dataclass(frozen=True)
class Derivation:
    target: str
    expression: Any
    when: Any
    on_unmatched: str
    rollback: str
    max_rows: int | None
    category: str | None
    settings: dict | None
    execution: Any


def derive(
    target,
    expression,
    *,
    rollback,
    when=None,
    on_unmatched="error",
    max_rows=None,
    category=None,
    settings=None,
    execution=None,
) -> Derivation:
    if rollback not in {"irreversible", "clear", "recompute", "capture"}:
        raise ValueError("Unsupported transformation rollback policy")
    if on_unmatched not in {"error", "keep"}:
        raise ValueError("Transformation on_unmatched must be error or keep")
    if max_rows is not None and (type(max_rows) is not int or max_rows < 0):
        raise ValueError("max_rows must be a nonnegative integer")
    if settings is not None and (
        not isinstance(settings, dict)
        or any(not isinstance(key, str) or not key for key in settings)
    ):
        raise ValueError("settings must be a dictionary with nonempty string keys")
    return Derivation(
        column_name(target),
        _declarative_value(expression, label="expression"),
        _declarative_value(when, label="when"),
        on_unmatched,
        rollback,
        max_rows,
        _category(category),
        None if settings is None else dict(settings),
        execution,
    )


@dataclass(frozen=True)
class HistoricalTable:
    table: str
    snapshot: str | None
    schema: str | None = None

    def __getattr__(self, name):
        if name.startswith("_"):
            raise AttributeError(name)
        from .expressions import col

        return col(name, table=self.table)


def historical_table(
    table: str, *, snapshot: str | None = None, schema=None
) -> HistoricalTable:
    if (
        not isinstance(table, str)
        or not table
        or "\x00" in table
        or snapshot is not None
        and (not isinstance(snapshot, str) or not snapshot or "\x00" in snapshot)
        or schema is not None
        and (not isinstance(schema, str) or not schema or "\x00" in schema)
    ):
        raise ValueError("Historical tables require a table and optional snapshot name")
    return HistoricalTable(table, snapshot, schema)


@dataclass(frozen=True)
class TransitionTarget:
    model: Any
    mappings: tuple[tuple[str, Any], ...]
    where: Any
    key: tuple[str, ...]
    on_conflict: str
    acknowledge_overwrite: bool
    priority: int | None


def into(
    model,
    *,
    map,
    key=None,
    where=None,
    on_conflict="error",
    acknowledge_overwrite=False,
    priority=None,
) -> TransitionTarget:
    if not isinstance(map, dict) or not map:
        raise ValueError("into requires a nonempty column mapping")
    if on_conflict not in {"error", "ignore_if_equivalent", "overwrite"}:
        raise ValueError(
            "Conflict policy must be error, ignore_if_equivalent, or overwrite"
        )
    if type(acknowledge_overwrite) is not bool:
        raise ValueError("acknowledge_overwrite must be boolean")
    if on_conflict == "overwrite" and acknowledge_overwrite is not True:
        raise ValueError("overwrite requires acknowledge_overwrite=True")
    if priority is not None and type(priority) is not int:
        raise ValueError("Target priority must be an integer")
    if not isinstance(model, type) or getattr(model, "__table__", None) is None:
        raise ValueError("into model must be a mapped SQLAlchemy class")
    mappings = tuple(
        (column_name(key), _declarative_value(value, label="mapping expression"))
        for key, value in map.items()
    )
    if len({name for name, _ in mappings}) != len(mappings):
        raise ValueError("Duplicate target mappings")
    return TransitionTarget(
        model,
        mappings,
        _declarative_value(where, label="where"),
        column_names(key),
        on_conflict,
        acknowledge_overwrite,
        priority,
    )


@dataclass(frozen=True)
class Validation:
    expression: Any
    message: str


def validate(expression, *, message="Data validation failed") -> Validation:
    if not isinstance(message, str) or not message:
        raise ValueError("Validation message must be a nonempty string")
    return Validation(
        _declarative_value(expression, label="validation expression"), message
    )


class DataMeta:
    managed_rows = None
    transformations = ()
    validations = ()

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        allowed = {"managed_rows", "transformations", "validations"}
        unknown = set(vars(cls)) - allowed - _CLASS_METADATA
        if unknown:
            raise ValueError(f"Unknown Data attributes: {', '.join(sorted(unknown))}")
        if cls.managed_rows is not None and not isinstance(
            cls.managed_rows, ManagedRows
        ):
            raise ValueError("Data.managed_rows must be rows(...)")
        for name, expected in (
            ("transformations", Derivation),
            ("validations", Validation),
        ):
            values = getattr(cls, name)
            if not isinstance(values, (list, tuple)) or any(
                not isinstance(item, expected) for item in values
            ):
                raise ValueError(f"Data.{name} contains an unsupported declaration")
            setattr(cls, name, tuple(values))


class DataTransition:
    source = None
    source_snapshot = None
    source_identity = ()
    targets = ()
    coverage = "all"
    on_unmatched = "error"
    overlap = "error"
    on_complete = "preserve"
    archive_to = None
    unmatched_archive_to = None
    acknowledge_archive = False
    rollback = "restore_preserved_source"
    acknowledge_drop = False
    max_rows = None
    execution = None
    category = None
    declaration_id = None

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        allowed = {name for name in vars(DataTransition) if not name.startswith("_")}
        unknown = set(vars(cls)) - allowed - _CLASS_METADATA
        if unknown:
            raise ValueError(
                f"Unknown DataTransition attributes: {', '.join(sorted(unknown))}"
            )
        if (
            not isinstance(cls.targets, (list, tuple))
            or not cls.targets
            or any(not isinstance(target, TransitionTarget) for target in cls.targets)
        ):
            raise ValueError(
                "DataTransition requires one source and targets made with into(...)"
            )
        from .advanced import MergedSource

        merged = isinstance(cls.source, MergedSource)
        if merged and not cls.source_identity:
            cls.source_identity = cls.source.key
        if (
            not isinstance(cls.source_identity, (list, tuple))
            or not cls.source_identity
        ):
            raise ValueError("DataTransition requires source_identity columns")
        if any(callable(value) for value in cls.source_identity):
            raise ValueError("source_identity cannot contain Python callables")
        if merged:
            if tuple(column_names(cls.source_identity)) != cls.source.key:
                raise ValueError(
                    "Merged source_identity must match the merge grouping key"
                )
            if cls.source_snapshot is not None:
                raise ValueError(
                    "Merged sources carry pinned snapshots for every input"
                )
            if len(cls.targets) != 1:
                raise ValueError("A merged transition requires exactly one target")
        elif isinstance(cls.source, HistoricalTable):
            if cls.source_snapshot not in (None, cls.source.snapshot):
                raise ValueError(
                    "source_snapshot conflicts with historical_table snapshot"
                )
        else:
            if (
                not isinstance(cls.source, type)
                or getattr(cls.source, "__table__", None) is None
            ):
                raise ValueError(
                    "DataTransition source must be historical_table(...) or a mapped model"
                )
            if not isinstance(cls.source_snapshot, str) or not cls.source_snapshot:
                raise ValueError(
                    "Mapped DataTransition sources require source_snapshot"
                )
        cls.coverage = _normalize_transition_coverage(cls.coverage, cls.overlap)
        if cls.on_unmatched not in {"error", "ignore", "archive"} or (
            cls.on_unmatched in {"ignore", "archive"} and cls.coverage != "subset"
        ):
            raise ValueError("on_unmatched=ignore/archive requires coverage=subset")
        if cls.on_unmatched == "archive" and not isinstance(
            cls.unmatched_archive_to, ArchiveTable
        ):
            raise ValueError(
                "on_unmatched=archive requires unmatched_archive_to=archive_table(...)"
            )
        if cls.on_unmatched != "archive" and cls.unmatched_archive_to is not None:
            raise ValueError("unmatched_archive_to requires on_unmatched=archive")
        if cls.on_complete not in {"keep", "preserve", "drop", "archive"}:
            raise ValueError("on_complete must be keep, preserve, drop, or archive")
        if cls.on_complete == "keep" and (
            isinstance(cls.source, (HistoricalTable, MergedSource))
            or cls.on_unmatched != "error"
        ):
            raise ValueError(
                "on_complete=keep requires a mapped current-model source and on_unmatched=error"
            )
        if cls.on_complete == "archive" and not isinstance(
            cls.archive_to, ArchiveTable
        ):
            raise ValueError(
                "on_complete=archive requires archive_to=archive_table(...)"
            )
        if cls.on_complete != "archive" and cls.archive_to is not None:
            raise ValueError("archive_to requires on_complete=archive")
        if type(cls.acknowledge_archive) is not bool:
            raise ValueError("acknowledge_archive must be boolean")
        if (
            cls.on_complete == "archive" or cls.on_unmatched == "archive"
        ) and not cls.acknowledge_archive:
            raise ValueError("Transition archive requires acknowledge_archive=True")
        if (
            cls.on_complete != "archive"
            and cls.on_unmatched != "archive"
            and cls.acknowledge_archive
        ):
            raise ValueError("acknowledge_archive requires an archive policy")
        if type(cls.acknowledge_drop) is not bool:
            raise ValueError("acknowledge_drop must be boolean")
        if cls.on_complete == "drop" and (
            not cls.acknowledge_drop or cls.rollback != "irreversible"
        ):
            raise ValueError(
                "Dropping source requires acknowledge_drop=True and rollback=irreversible"
            )
        if cls.rollback not in {
            "restore_preserved_source",
            "capture",
            "irreversible",
        }:
            raise ValueError("Unsupported transition rollback policy")
        if cls.rollback == "capture" and cls.on_complete == "drop":
            raise ValueError("capture rollback requires preserve or archive completion")
        if cls.max_rows is not None and (
            type(cls.max_rows) is not int or cls.max_rows < 0
        ):
            raise ValueError("max_rows must be a nonnegative integer")
        if cls.declaration_id is not None and (
            not isinstance(cls.declaration_id, str)
            or not cls.declaration_id
            or "\x00" in cls.declaration_id
        ):
            raise ValueError("declaration_id must be a nonempty string or None")
        _category(cls.category)
        cls.source_identity = tuple(cls.source_identity)
        cls.targets = tuple(cls.targets)
