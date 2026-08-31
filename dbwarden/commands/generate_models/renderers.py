from __future__ import annotations

import re
from typing import Any

from dbwarden.engine.backends.clickhouse.generate_models import (
    _render_ch_meta,
    _render_ch_mv_meta,
)
from dbwarden.engine.backends.mysql.generate_models import _render_mysql_meta
from dbwarden.engine.backends.sqlite.generate_models import _render_sqlite_meta
from dbwarden.engine.backends.postgresql.generate_models import (
    _format_pg_type,
    _render_postgresql_meta,
)
from dbwarden.engine.core.type_parsing import _format_default, _parse_type
from dbwarden.engine.shared.format_utils import _format_meta_value, _sanitize_identifier


def _format_column(col: dict) -> str:
    attr_name = _sanitize_identifier(col["name"])
    sa_type = _format_pg_type(col) or _parse_type(col["type"], col.get("dialect"))

    col_args = [f"{attr_name} = Column({col['name']!r}, {sa_type}"]
    if col.get("foreign_key"):
        fk_opts = col.get("fk_options", {})
        fk_parts: list[str] = []
        for opt_key, sa_key in (("ondelete", "ondelete"), ("onupdate", "onupdate"), ("deferrable", "deferrable"), ("initially", "initially")):
            val = fk_opts.get(opt_key)
            if opt_key == "deferrable":
                if val:
                    fk_parts.append("deferrable=True")
            elif opt_key == "initially":
                if val:
                    fk_parts.append(f"initially={val!r}")
            elif val and val != "NO ACTION":
                fk_parts.append(f"{sa_key}={val!r}")
        if fk_parts:
            col_args.append(f"ForeignKey('{col['foreign_key']}', {', '.join(fk_parts)})")
        else:
            col_args.append(f"ForeignKey('{col['foreign_key']}')")
    if col.get("primary_key"):
        col_args.append("primary_key=True")
    if not col.get("nullable", True):
        col_args.append("nullable=False")
    if col.get("unique"):
        col_args.append("unique=True")
    default = _format_default(col.get("default"))
    if default is not None:
        col_args.append(f"default={default}")
    if col.get("server_default"):
        col_args.append(f"server_default=text({col['server_default']!r})")
    if col.get("autoincrement") is True:
        col_args.append("autoincrement=True")
    elif col.get("autoincrement") is False:
        col_args.append("autoincrement=False")

    col_args.append(")")
    return ",\n        ".join(col_args)


def _generate_table_code(
    table_name: str,
    columns: list[dict],
    ch_options: dict | None = None,
    object_type: str = "table",
    pg_meta: dict | None = None,
    my_meta: dict | None = None,
    base_class_name: str = "Base",
    sq_meta: dict | None = None,
) -> str:
    class_name = "".join(part.capitalize() for part in re.split(r"[_\s]", table_name) if part)
    if not class_name:
        class_name = table_name.capitalize()

    is_ch_mv = bool(ch_options and object_type == "materialized_view")
    effective_base = "MaterializedView" if is_ch_mv else base_class_name
    meta_class = "CHViewMeta" if is_ch_mv else "CHTableMeta"

    lines: list[str] = []
    lines.append(f"class {class_name}({effective_base}):")
    lines.append(f"    __tablename__ = {table_name!r}")
    for col in columns:
        col_line = _format_column(col)
        if col_line:
            lines.append(f"    {col_line}")

    primary_key_cols = None
    if my_meta and my_meta.get("primary_key"):
        primary_key_cols = my_meta["primary_key"]
    elif pg_meta and pg_meta.get("primary_key"):
        primary_key_cols = pg_meta["primary_key"]
    if primary_key_cols:
        lines.append(f"    __mapper_args__ = {{'primary_key': {primary_key_cols!r}}}")

    if ch_options:
        lines.append("")
        if is_ch_mv:
            lines.extend(_render_ch_mv_meta(columns, ch_options))
        else:
            lines.append(f"    class Meta({meta_class}):")
            lines.extend(_render_ch_meta(columns, ch_options, object_type))
    if pg_meta or any(col.get("pg_meta") for col in columns):
        lines.append("")
        lines.extend(_render_postgresql_meta(columns, pg_meta))
    if my_meta or any(col.get("my_meta") for col in columns):
        lines.append("")
        lines.extend(_render_mysql_meta(columns, my_meta))
    if sq_meta or any(col.get("sq_meta") for col in columns):
        lines.append("")
        lines.extend(_render_sqlite_meta(columns, sq_meta))
    return "\n".join(lines) + "\n"
