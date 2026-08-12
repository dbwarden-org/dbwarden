from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from dbwarden.engine.model_discovery import ModelColumn, ModelTable


@dataclass(frozen=True)
class SqlScenario:
    name: str
    model_tables: tuple[ModelTable, ...]
    expected_operations: tuple[str, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)


def column(
    name: str,
    type_name: str = "integer",
    *,
    nullable: bool = False,
    primary_key: bool = False,
    unique: bool = False,
    default: str | None = None,
) -> ModelColumn:
    return ModelColumn(
        name,
        type_name,
        nullable,
        primary_key,
        unique,
        default,
        None,
    )


def table(name: str, *columns: ModelColumn, schema: str | None = None) -> ModelTable:
    return ModelTable(
        name=name,
        columns=list(columns) or [column("id", primary_key=True)],
        schema=schema,
    )
