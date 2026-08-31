from __future__ import annotations

from pathlib import Path

from dbwarden.commands.generate_models.imports import (
    _render_imports,
    _resolve_imports,
    _resolve_mysql_imports,
    _resolve_postgresql_imports,
)
from dbwarden.commands.generate_models.renderers import _generate_table_code
from dbwarden.files import atomic_write_text
import re


def _write_models(
    output_dir: str, tables: list[dict], single_file: bool,
    base_import_path: str | None = None,
    base_class_name: str = "Base",
) -> None:
    out_path = Path(output_dir)
    out_path.mkdir(parents=True, exist_ok=True)

    has_relationships = False

    if single_file:
        all_imports: set[str] = set()
        pg_dialect_imports: set[str] = set()
        my_dialect_imports: set[str] = set()
        ch_meta_imports: set[str] = set()
        all_classes: list[str] = []
        for table in tables:
            for col in table["columns"]:
                col["dialect"] = table.get("dialect")
            all_imports |= _resolve_imports(table["columns"], has_relationships)
            if table.get("dialect") == "postgresql":
                pg_dialect_imports |= _resolve_postgresql_imports(table["columns"])
            if table.get("dialect") in ("mysql", "mariadb"):
                my_dialect_imports |= _resolve_mysql_imports(table["columns"])
            if table.get("ch_options"):
                if table.get("object_type") == "materialized_view":
                    ch_meta_imports.update({
                        "MaterializedView", "CHViewMeta", "materialized_view",
                        "CHColumnMeta", "ChEngineSpec",
                    })
                else:
                    ch_meta_imports.update({
                        "CHColumnMeta", "CHTableMeta", "ChEngineSpec", "ProjectionSpec",
                    })
            all_classes.append(
                _generate_table_code(
                    table["name"],
                    table["columns"],
                    table.get("ch_options"),
                    table.get("object_type", "table"),
                    table.get("pg_meta"),
                    table.get("my_meta"),
                    base_class_name=base_class_name,
                    sq_meta=table.get("sq_meta"),
                )
            )

        imports = _render_imports(all_imports)
        pg_meta_imports: set[str] = set()
        my_meta_imports: set[str] = set()
        sq_meta_imports: set[str] = set()
        needs_pg_spec = False
        needs_my_spec = False
        needs_sq_spec = False
        needs_ch_spec = False
        for table in tables:
            if any(col.get("pg_meta") for col in table["columns"]):
                pg_meta_imports.add("PGColumnMeta")
                pg_meta_imports.add("PGTableMeta")
                needs_pg_spec = True
            if any(col.get("my_meta") for col in table["columns"]):
                my_meta_imports.add("MyColumnMeta")
                my_meta_imports.add("MyTableMeta")
                needs_my_spec = True
            if any(col.get("sq_meta") for col in table["columns"]):
                sq_meta_imports.add("SqColumnMeta")
                sq_meta_imports.add("SqTableMeta")
                needs_sq_spec = True
            if any(col.get("ch_meta") for col in table["columns"]):
                needs_ch_spec = True
        if any(table.get("pg_meta") for table in tables):
            pg_meta_imports.add("PGTableMeta")
        if any(table.get("my_meta") for table in tables):
            my_meta_imports.add("MyTableMeta")
        if any(table.get("sq_meta") for table in tables):
            sq_meta_imports.add("SqTableMeta")

        content = (
            "from sqlalchemy import " + ", ".join(sorted(imports)) + "\n"
            if imports
            else ""
        )
        if pg_dialect_imports:
            content += "from sqlalchemy.dialects.postgresql import " + ", ".join(sorted(pg_dialect_imports)) + "\n"
        if base_import_path:
            content += f"from {base_import_path} import {base_class_name}\n\n\n"
        else:
            content += (
                "from sqlalchemy.ext.declarative import declarative_base\n\n\n"
                "Base = declarative_base()\n\n\n"
            )
        if pg_meta_imports or needs_pg_spec:
            imports = ", ".join(sorted(pg_meta_imports))
            if needs_pg_spec:
                imports = ("pg, " + imports) if pg_meta_imports else "pg"
            content += "from dbwarden.databases.pgsql import " + imports + "\n"
            content += "\n"
        if my_meta_imports or needs_my_spec:
            imports = ", ".join(sorted(my_meta_imports))
            if needs_my_spec:
                imports = ("my, " + imports) if my_meta_imports else "my"
            content += "from dbwarden.databases.mysql import " + imports + "\n"
            content += "\n"
        if sq_meta_imports or needs_sq_spec:
            imports = ", ".join(sorted(sq_meta_imports))
            if needs_sq_spec:
                imports = ("sq, " + imports) if sq_meta_imports else "sq"
            content += "from dbwarden.databases.sqlite import " + imports + "\n"
            content += "\n"
        if ch_meta_imports or needs_ch_spec:
            imports = ", ".join(sorted(ch_meta_imports))
            if needs_ch_spec:
                imports = ("ch, " + imports) if ch_meta_imports else "ch"
            content += "from dbwarden.databases.clickhouse import " + imports + "\n"
            content += "\n"
        content += "\n\n".join(all_classes)
        atomic_write_text(out_path / "models.py", content)
        return

    for table in tables:
        for col in table["columns"]:
            col["dialect"] = table.get("dialect")
        imports = _resolve_imports(table["columns"], has_relationships)
        pg_dialect_imports = _resolve_postgresql_imports(table["columns"]) if table.get("dialect") == "postgresql" else set()
        my_dialect_imports = _resolve_mysql_imports(table["columns"]) if table.get("dialect") in ("mysql", "mariadb") else set()
        content_lines: list[str] = []
        content_lines.append("from sqlalchemy import " + ", ".join(sorted(imports)) + "\n")
        if pg_dialect_imports:
            content_lines.append("from sqlalchemy.dialects.postgresql import " + ", ".join(sorted(pg_dialect_imports)) + "\n")
        if base_import_path:
            content_lines.append(f"from {base_import_path} import {base_class_name}\n\n\n")
        else:
            content_lines.append("from sqlalchemy.ext.declarative import declarative_base\n\n\n")
            content_lines.append("Base = declarative_base()\n\n\n")
        needs_pg_base: set[str] = set()
        needs_my_base: set[str] = set()
        needs_sq_base: set[str] = set()
        needs_pg_spec = False
        needs_my_spec = False
        needs_sq_spec = False
        needs_ch_spec = False
        if any(col.get("pg_meta") for col in table["columns"]):
            needs_pg_base.add("PGColumnMeta")
            needs_pg_base.add("PGTableMeta")
            needs_pg_spec = True
        if table.get("pg_meta"):
            needs_pg_base.add("PGTableMeta")
        if any(col.get("my_meta") for col in table["columns"]):
            needs_my_base.add("MyColumnMeta")
            needs_my_base.add("MyTableMeta")
            needs_my_spec = True
        if table.get("my_meta"):
            needs_my_base.add("MyTableMeta")
        if any(col.get("sq_meta") for col in table["columns"]):
            needs_sq_base.add("SqColumnMeta")
            needs_sq_spec = True
        if table.get("sq_meta"):
            needs_sq_base.add("SqTableMeta")
        if needs_pg_base or needs_pg_spec:
            imports = ", ".join(sorted(needs_pg_base))
            if needs_pg_spec:
                imports = ("pg, " + imports) if needs_pg_base else "pg"
            content_lines.append("from dbwarden.databases.pgsql import " + imports + "\n")
            content_lines.append("\n")
        if needs_my_base or needs_my_spec:
            imports = ", ".join(sorted(needs_my_base))
            if needs_my_spec:
                imports = ("my, " + imports) if needs_my_base else "my"
            content_lines.append("from dbwarden.databases.mysql import " + imports + "\n")
            content_lines.append("\n")
        if needs_sq_base or needs_sq_spec:
            imports = ", ".join(sorted(needs_sq_base))
            if needs_sq_spec:
                imports = ("sq, " + imports) if needs_sq_base else "sq"
            content_lines.append("from dbwarden.databases.sqlite import " + imports + "\n")
            content_lines.append("\n")
        if table.get("ch_options"):
            if table.get("object_type") == "materialized_view":
                ch_imports_set: set[str] = {
                    "MaterializedView", "CHViewMeta", "materialized_view",
                    "CHColumnMeta", "ChEngineSpec",
                }
            else:
                ch_imports_set = {"CHColumnMeta", "CHTableMeta", "ChEngineSpec", "ProjectionSpec"}
            needs_ch_spec = any(col.get("ch_meta") for col in table["columns"])
            if needs_ch_spec:
                ch_imports_set.add("ch")
            content_lines.append("from dbwarden.databases.clickhouse import " + ", ".join(sorted(ch_imports_set)) + "\n")
            content_lines.append("\n")
        content_lines.append(
            _generate_table_code(
                table["name"],
                table["columns"],
                table.get("ch_options"),
                table.get("object_type", "table"),
                table.get("pg_meta"),
                table.get("my_meta"),
                base_class_name=base_class_name,
                sq_meta=table.get("sq_meta"),
            )
        )
        safe_name = re.sub(r"[^a-zA-Z0-9_]", "_", table["name"].lower()).strip("_") or "model"
        atomic_write_text(out_path / f"{safe_name}.py", "".join(content_lines))
