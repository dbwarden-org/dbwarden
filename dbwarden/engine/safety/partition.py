from __future__ import annotations

from graphlib import CycleError, TopologicalSorter

from dbwarden.engine.safety.classifiers import (
    LEVELS,
    classify_operation,
    severity_level,
)


def _type_references(value) -> set[str]:
    if hasattr(value, "to_dict"):
        value = value.to_dict()
    if isinstance(value, dict):
        names = {
            item.removesuffix("[]").strip('"')
            for key, item in value.items()
            if key
            in {
                "type",
                "model_type",
                "to_type",
                "type_name",
                "enum_name",
                "pg_enum_name",
            }
            and isinstance(item, str)
        }
        return names | set().union(
            *(
                _type_references(item)
                for key, item in value.items()
                if key
                not in {"__rollback_attrs", "state_changes", "from_type", "snap_type"}
            )
        )
    if isinstance(value, (list, tuple)):
        return set().union(*(_type_references(item) for item in value))
    return set()


def partition_operations(
    ops: list[dict], threshold: str | None, backend: str
) -> tuple[list[dict], list[dict]]:
    by_id = {op["id"]: op for op in ops}
    if len(by_id) != len(ops):
        raise ValueError("Duplicate operation IDs. hint: regenerate the diff")
    dependencies = {op["id"]: set(op.get("depends_on", [])) for op in ops}
    for index, op in enumerate(ops):
        table = op.get("table", op.get("old_table"))
        references = set(op.get("referenced_tables", []))
        state_table = op.get("state_table", {})
        for foreign_key in state_table.get("foreign_keys", []):
            reference = foreign_key.get("referenced_table") or foreign_key.get(
                "referred_table"
            )
            if reference:
                references.add(reference)
        for column in state_table.get("columns", {}).values():
            if isinstance(column.get("foreign_key"), str):
                references.add(column["foreign_key"].rsplit(".", 1)[0])
        if op.get("referenced_table"):
            references.add(op["referenced_table"])
        if "view" in op["type"] or op.get("object_type") in {
            "view",
            "materialized_view",
        }:
            from sqlglot import exp, parse_one
            from sqlglot.errors import SqlglotError

            definition = (
                op.get("definition")
                or op.get("query")
                or op.get("state_table", {}).get("pg_view_definition")
            )
            if isinstance(definition, str):
                try:
                    query = parse_one(
                        definition,
                        read={"postgresql": "postgres", "mariadb": "mysql"}.get(
                            backend, backend
                        ),
                    )
                    references.update(
                        ".".join(part.name for part in item.parts)
                        for item in query.find_all(exp.Table)
                    )
                except SqlglotError as exc:
                    raise ValueError(
                        "Cannot determine view dependencies. hint: supply referenced_tables or correct the definition"
                    ) from exc
        for earlier in ops[:index]:
            same_table = table and table in (
                earlier.get("table"),
                earlier.get("new_table"),
            )
            name = op.get("name") or op.get("index_name")
            earlier_name = earlier.get("name") or earlier.get("index_name")
            same_object = name and name == earlier_name
            if same_table and (
                "index" in op["type"]
                or "constraint" in op["type"]
                or "foreign_key" in op["type"]
            ):
                same_columns = op.get("columns") and op.get("columns") == earlier.get(
                    "columns"
                )
                if same_object or (
                    same_columns
                    and op["type"].removeprefix("add_").removeprefix("drop_")
                    == earlier["type"].removeprefix("add_").removeprefix("drop_")
                ):
                    dependencies[op["id"]].add(earlier["id"])
            if (
                same_table
                and op.get("temporary")
                and op["temporary"] == earlier.get("column")
            ):
                dependencies[op["id"]].add(earlier["id"])
            if same_table and earlier["type"] in {
                "create_table",
                "rename_table",
                "recreate_sq_table",
                "recreate_ch_table",
            }:
                dependencies[op["id"]].add(earlier["id"])
            if (
                same_table
                and (
                    "index" in op["type"]
                    or "constraint" in op["type"]
                    or "foreign_key" in op["type"]
                )
                and earlier["type"]
                in {
                    "add_column",
                    "rename_column",
                    "alter_column_type",
                    "safe_type_contract",
                }
            ):
                columns = op.get("columns", [])
                if (
                    op.get("expression")
                    or earlier.get("column", earlier.get("new_name")) in columns
                ):
                    dependencies[op["id"]].add(earlier["id"])
            if (
                same_table
                and op.get("column")
                and op.get("column") == earlier.get("column", earlier.get("new_name"))
            ):
                dependencies[op["id"]].add(earlier["id"])
            if (
                op.get("referenced_table") == earlier.get("table")
                and earlier["type"] == "create_table"
            ):
                dependencies[op["id"]].add(earlier["id"])
            if earlier.get("table", earlier.get("new_table")) in references:
                dependencies[op["id"]].add(earlier["id"])
            if (
                earlier["type"] == "create_schema"
                and table
                and str(table).startswith(str(earlier.get("schema")) + ".")
            ):
                dependencies[op["id"]].add(earlier["id"])
        types = _type_references(
            {
                key: value
                for key, value in op.items()
                if key not in {"type", "enum_name", "domain_name", "type_name", "name"}
            }
        ) | set(op.get("referenced_types", []))
        for candidate in ops:
            if candidate is op:
                continue
            if candidate["type"] in {
                "create_type",
                "create_domain",
                "alter_domain",
                "alter_enum_add_value",
            }:
                name = (
                    candidate.get("enum_name")
                    or candidate.get("domain_name")
                    or candidate.get("name")
                )
                if name in types:
                    dependencies[op["id"]].add(candidate["id"])
    if any(dep not in by_id for deps in dependencies.values() for dep in deps):
        raise ValueError(
            "Unknown operation dependency. hint: correct the handler's depends_on IDs"
        )
    try:
        tuple(TopologicalSorter(dependencies).static_order())
    except CycleError as exc:
        raise ValueError(
            "Operation dependency cycle; no migration files written. hint: separate cyclic changes"
        ) from exc
    positions = {op["id"]: index for index, op in enumerate(ops)}
    if any(
        positions[dependency] >= positions[key]
        for key, deps in dependencies.items()
        for dependency in deps
    ):
        raise ValueError(
            "Operation dependencies conflict with statement ordering. hint: correct handler ordering before splitting"
        )
    for op in ops:
        op["depends_on"] = sorted(dependencies[op["id"]])
    if threshold is None:
        return ops, []
    cut = LEVELS.index(severity_level(threshold))
    deferred = {
        op["id"] for op in ops if LEVELS.index(classify_operation(op, backend)) >= cut
    }
    while True:
        closure = deferred | {
            key for key, deps in dependencies.items() if deps & deferred
        }
        if closure == deferred:
            break
        deferred = closure
    return [op for op in ops if op["id"] not in deferred], [
        op for op in ops if op["id"] in deferred
    ]


def group_operations(
    ops: list[dict], threshold: str | None, backend: str
) -> list[tuple[str, list[dict]]]:
    from dbwarden.plugin import ObjectPluginRegistry

    base, deferred = partition_operations(ops, threshold, backend)
    if not any(op.get("category") is not None for op in ops):
        return [
            (name, group)
            for name, group in (
                [("base", base), ("deferred", deferred)]
                if deferred
                else [("unsplit", base)]
            )
            if group
        ]
    categories = ObjectPluginRegistry.categories()
    deferred_ids = {op["id"] for op in deferred}
    assigned: dict[str, int] = {}
    for op in ops:
        category = op.get("category", "base")
        if category not in categories:
            raise ValueError(
                f"Unknown migration category '{category}'. hint: register it in plugin setup"
            )
        rank = max(
            categories[category]["order"], 100 if op["id"] in deferred_ids else 0
        )
        rank = max([rank, *(assigned[dep] for dep in op["depends_on"])])
        assigned[op["id"]] = rank
    return [
        (name, group)
        for name, definition in categories.items()
        if (group := [op for op in ops if assigned[op["id"]] == definition["order"]])
    ]
