from __future__ import annotations

from typing import Any

# Column metadata is declared through ``sq.field(...)``; flat ``sq_*`` field
# attributes are rejected by the meta reader, so generated models must use the
# spec form.
_FLAT_TO_SPEC: dict[str, str] = {
    "sq_generated": "generated",
    "sq_generated_mode": "generated_mode",
    "sq_collate": "collate",
}


def resolve_sqlite_imports(columns: list[dict]) -> set[str]:
    imports: set[str] = set()
    return imports


def extract_sqlite_meta(
    connection, table_name: str,
    indexes: list[dict] | None = None,
    checks: list[dict] | None = None,
    uniques: list[dict] | None = None,
    raw_columns: list[dict] | None = None,
) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    from dbwarden.engine.backends.sqlite.extract import (
        extract_sqlite_column_meta,
        extract_sqlite_table_meta,
    )

    table_meta = extract_sqlite_table_meta(connection, table_name)
    column_meta = extract_sqlite_column_meta(None, connection, table_name, raw_columns or [])
    return table_meta, column_meta


def _render_sqlite_meta(columns: list[dict], sq_meta: dict | None = None) -> list[str]:
    if not sq_meta and not any(
        col.get("sq_meta") or col.get("comment") for col in columns
    ):
        return []

    from dbwarden.engine.shared.format_utils import _format_meta_value

    # Every block is assembled before its class header is written. Column
    # metadata that carries nothing renderable - an extracted key with no
    # `class Meta` equivalent, say - would otherwise emit
    # `class id(SqColumnMeta):` with an empty body, and the generated module
    # fails to import with "expected an indented block after class definition".
    body: list[str] = []
    sq_meta = sq_meta or {}
    for key, value in sq_meta.items():
        if key == "comment":
            body.append(f"        comment = {value!r}")
        else:
            rendered = _format_meta_value(value)
            if rendered:
                body.append(f"        {key} = {rendered[0].strip()}")
                body.extend(rendered[1:])

    for col in columns:
        field_meta: dict[str, Any] = {}
        if col.get("comment"):
            field_meta["comment"] = col["comment"]
        field_meta.update(col.get("sq_meta") or {})
        if not field_meta:
            continue

        column_body: list[str] = []
        comment_val = field_meta.pop("comment", None)
        if comment_val is not None:
            column_body.append(f"            comment = {comment_val!r}")
        sq_kwargs = {
            spec_key: field_meta[flat_key]
            for flat_key, spec_key in _FLAT_TO_SPEC.items()
            if flat_key in field_meta
        }
        if sq_kwargs:
            kwargs_repr = ", ".join(f"{k}={v!r}" for k, v in sq_kwargs.items())
            column_body.append(f"            sq = sq.field({kwargs_repr})")
        if not column_body:
            continue

        body.append("")
        body.append(f"        class {col['name']}(SqColumnMeta):")
        body.extend(column_body)

    if not body:
        return []
    return ["    class Meta(SqTableMeta):", *body]
