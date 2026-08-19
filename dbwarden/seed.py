from __future__ import annotations

import hashlib
import inspect
from typing import Any, ClassVar

from dbwarden.exceptions import SeedError


_seed_registry: list[type[Seed]] = []


class SeedRow:
    _data: dict

    def __init__(self, **kwargs: Any) -> None:
        self._data = kwargs

    def to_dict(self) -> dict:
        return dict(self._data)


class SeedMeta:
    database: str
    description: str
    on_conflict: str
    conflict_columns: list[str]
    source_hash: str

    def __init__(
        self,
        database: str,
        description: str,
        on_conflict: str = "ignore",
        conflict_columns: list[str] | None = None,
        source_hash: str = "",
        version: str = "",
        **kwargs: Any,
    ) -> None:
        self.database = database
        self.description = description
        self.on_conflict = on_conflict
        self.conflict_columns = conflict_columns or []
        self.source_hash = source_hash


class Seed:
    """Base class for in-code seed definitions.

    Subclass this to define seed data that is applied alongside your migrations.

    Attributes:
        model: The SQLAlchemy model class to seed data into.
        __seed_version__: Immutable version in the ``C0001`` form.
        rows: List of model instances or ``SeedRow`` objects for declarative seeds.
        forward: Optional ``(connection, session)`` callable for procedural seeds.
        reverse: Optional ``(connection, session)`` callable that reverses ``forward``.
        __seed_database__: Target database name (default ``"default"``).
        __seed_description__: Human-readable label shown in seed list.
        __seed_on_conflict__: ``"ignore"`` (default), ``"update"``, or ``"error"``.
        __seed_conflict_columns__: Column names for conflict detection
            when ``__seed_on_conflict__`` is ``"update"``.
    """

    model: ClassVar[type]
    rows: ClassVar[list | None] = None

    __seed_version__: ClassVar[str] = ""
    """Immutable code-seed version, for example ``C0001``."""

    __seed_database__: ClassVar[str] = "default"
    """Target database name. Routes the seed to the correct database handle."""

    __seed_description__: ClassVar[str] = ""
    """Human-readable description shown in ``dbwarden seed list`` output."""

    __seed_on_conflict__: ClassVar[str] = "ignore"
    """Conflict resolution strategy: ``"ignore"`` (skip existing), ``"update"``, or ``"error"``."""

    __seed_conflict_columns__: ClassVar[list[str] | None] = None
    """Column names for conflict detection when ``__seed_on_conflict__`` is ``"update"``."""

    __seed_meta__: ClassVar[SeedMeta | None] = None

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        source = ""
        try:
            source = inspect.getsource(cls)
        except (OSError, TypeError):
            pass
        source_hash = hashlib.sha256(source.encode()).hexdigest()[:16] if source else ""
        cls.__seed_meta__ = SeedMeta(
            database=cls.__seed_database__,
            description=cls.__seed_description__,
            on_conflict=cls.__seed_on_conflict__,
            conflict_columns=cls.__seed_conflict_columns__ or [],
            source_hash=source_hash,
        )
        _seed_registry.append(cls)


def seed_data(
    database: str,
    version: str = "",
    description: str = "",
    on_conflict: str = "ignore",
    conflict_columns: list[str] | None = None,
):
    """Decorator that marks a class as an in-code seed definition.

    Deprecated: Use ``Seed`` base class instead.
    """
    if on_conflict not in ("ignore", "update", "error"):
        raise ValueError(
            f"on_conflict must be 'ignore', 'update', or 'error', got {on_conflict!r}"
        )

    def decorator(cls):
        source = ""
        try:
            source = inspect.getsource(cls)
        except (OSError, TypeError):
            pass
        source_hash = hashlib.sha256(source.encode()).hexdigest()[:16] if source else ""

        cls.__seed_meta__ = SeedMeta(
            database=database,
            description=description,
            on_conflict=on_conflict,
            conflict_columns=conflict_columns or [],
            source_hash=source_hash,
        )
        cls.__dbwarden_seed__ = cls.__seed_meta__
        _seed_registry.append(cls)
        return cls

    return decorator


def _row_to_dict(row: Any, model_cls: type | None = None) -> dict:
    if model_cls is not None and isinstance(row, model_cls):
        return {col.name: getattr(row, col.name) for col in model_cls.__table__.columns}
    if isinstance(row, SeedRow):
        return row.to_dict()
    if isinstance(row, dict):
        return row
    raise SeedError(f"Invalid seed row type: {type(row)}")
