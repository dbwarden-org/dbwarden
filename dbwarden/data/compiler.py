from __future__ import annotations

import csv
import inspect
import json
import re
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path

from sqlalchemy import UniqueConstraint
from sqlalchemy.sql.sqltypes import (
    JSON,
    BigInteger,
    Boolean,
    Date,
    DateTime,
    Float,
    Integer,
    LargeBinary,
    Numeric,
    SmallInteger,
    String,
    Time,
)

from dbwarden.constants import INTERNAL_TABLE_PREFIXES

from .declarations import (
    ArchiveTable,
    DataMeta,
    DataTransition,
    HistoricalTable,
    _normalize_transition_coverage,
)
from .expressions import _expression_parameter_names, canonical_expression
from .ir import canonical_bytes, canonical_value, digest, empty_spec, seal_spec


@dataclass(frozen=True)
class DiscoveredData:
    models: tuple[type, ...]
    transitions: tuple[type, ...]


_LINEAGE_FIELDS = (
    "format_version",
    "snapshot_id",
    "database",
    "backend",
    "schema_checksum",
    "data_checksum",
    "created_by_migration",
    "parent_snapshot_ids",
)


def _json_object(pairs):
    result = {}
    for key, value in pairs:
        if not isinstance(key, str):
            raise TypeError("JSON object keys must be strings")
        key = unicodedata.normalize("NFC", key)
        if key in result:
            raise ValueError(f"Unicode-normalized duplicate JSON key: {key}")
        result[key] = value
    return result


def _type_family(value) -> str:
    if isinstance(value, (BigInteger, SmallInteger, Integer)):
        return "integer"
    if isinstance(value, (Numeric, Float)):
        return "numeric"
    if isinstance(value, String):
        return "text"
    if isinstance(value, Boolean):
        return "boolean"
    if isinstance(value, DateTime):
        return "datetime"
    if isinstance(value, Date):
        return "date"
    if isinstance(value, Time):
        return "time"
    if isinstance(value, JSON):
        return "json"
    if isinstance(value, LargeBinary):
        return "binary"
    if not isinstance(value, str):
        raise TypeError(f"Unsupported column type metadata: {value!r}")
    normalized = re.sub(r"\s+", "", value).lower()
    while normalized.startswith(
        ("nullable(", "lowcardinality(")
    ) and normalized.endswith(")"):
        normalized = normalized[normalized.index("(") + 1 : -1]
    base = normalized.split("(", 1)[0]
    if base in {
        "tinyint",
        "smallint",
        "int",
        "integer",
        "bigint",
        "int8",
        "int16",
        "int32",
        "int64",
        "uint8",
        "uint16",
        "uint32",
        "uint64",
    }:
        return "integer"
    if base in {
        "decimal",
        "numeric",
        "real",
        "float",
        "float32",
        "float64",
        "double",
        "doubleprecision",
    }:
        return "numeric"
    if base in {
        "char",
        "nchar",
        "varchar",
        "nvarchar",
        "string",
        "text",
        "clob",
        "uuid",
        "fixedstring",
    }:
        return "text"
    if base in {"bool", "boolean"}:
        return "boolean"
    if base == "date":
        return "date"
    if base in {"datetime", "datetime64", "timestamp", "timestamptz"}:
        return "datetime"
    if base == "time":
        return "time"
    if base in {"json", "jsonb"}:
        return "json"
    if base in {"blob", "binary", "varbinary", "bytea"}:
        return "binary"
    raise ValueError(f"Unsupported column type metadata: {value!r}")


def _snapshot_columns(value, *, table: str) -> dict[str, dict]:
    if isinstance(value, list):
        result = {}
        for item in value:
            if (
                not isinstance(item, dict)
                or not isinstance(item.get("name"), str)
                or not item["name"]
            ):
                raise ValueError(f"{table}: malformed snapshot column metadata")
            name = unicodedata.normalize("NFC", item["name"])
            if name in result:
                raise ValueError(f"{table}: duplicate snapshot column {name}")
            result[name] = item
        value = result
    if not isinstance(value, dict):
        raise TypeError(f"{table}: snapshot columns must be an object or array")
    result = {}
    for raw_name, metadata in value.items():
        if (
            not isinstance(raw_name, str)
            or not raw_name
            or not isinstance(metadata, dict)
        ):
            raise ValueError(f"{table}: malformed snapshot column metadata")
        name = unicodedata.normalize("NFC", raw_name)
        if name in result:
            raise ValueError(f"{table}: duplicate snapshot column {name}")
        result[name] = metadata
    return result


def _snapshot_column_info(
    columns: Mapping[str, dict], *, table: str
) -> dict[str, tuple[str, bool]]:
    result = {}
    for name, metadata in columns.items():
        raw_type = (
            metadata.get("type")
            or metadata.get("data_type")
            or metadata.get("col_type")
        )
        if raw_type is None:
            continue
        family = _type_family(raw_type)
        nullable = metadata.get("nullable", True)
        if type(nullable) is not bool:
            raise ValueError(f"{table}.{name}: nullable metadata must be boolean")
        result[name] = (family, nullable)
        result[f"{table}.{name}"] = (family, nullable)
    return result


def _model_column_info(table) -> dict[str, tuple[str, bool]]:
    result = {}
    for column in table.c:
        info = (_type_family(column.type), bool(column.nullable))
        result[column.name] = info
        result[f"{table.name}.{column.name}"] = info
    return result


def _literal_info(value) -> tuple[str, bool]:
    if value is None:
        return "null", True
    if type(value) is bool:
        return "boolean", False
    if type(value) is int:
        return "integer", False
    if type(value) is float:
        return "numeric", False
    if isinstance(value, str):
        return "text", False
    if isinstance(value, dict):
        return {"decimal": "numeric", "date": "date", "datetime": "datetime"}.get(
            value.get("$type"), "unknown"
        ), False
    return "unknown", False


def _same_ast(left, right) -> bool:
    if left.get("op") == right.get("op") == "in" and _same_ast(
        left["value"], right["value"]
    ):
        return {canonical_bytes(item) for item in left["values"]} == {
            canonical_bytes(item) for item in right["values"]
        }
    return canonical_bytes(left) == canonical_bytes(right)


def _implies(domain, predicate) -> bool:
    if _same_ast(domain, predicate):
        return True
    if domain.get("op") == "and":
        return _implies(domain["left"], predicate) or _implies(
            domain["right"], predicate
        )
    if domain.get("op") == "or":
        return _implies(domain["left"], predicate) and _implies(
            domain["right"], predicate
        )
    if predicate.get("op") == "or":
        return _implies(domain, predicate["left"]) or _implies(
            domain, predicate["right"]
        )
    return False


def _partial_coverage(node):
    if node.get("op") == "case" and not node.get("has_else"):
        predicates = [branch["when"] for branch in node["whens"]]
        coverage = predicates[0]
        for predicate in predicates[1:]:
            coverage = {"op": "or", "left": coverage, "right": predicate}
        return coverage
    if node.get("op") == "mapping" and not node.get("has_else"):
        return {
            "op": "in",
            "value": node["value"],
            "values": [item["key"] for item in node["entries"]],
        }
    return None


def _partial_nodes(node):
    if not isinstance(node, dict):
        return
    coverage = _partial_coverage(node)
    if coverage is not None:
        yield coverage
    for value in node.values():
        if isinstance(value, dict):
            yield from _partial_nodes(value)
        elif isinstance(value, list):
            for item in value:
                yield from _partial_nodes(item)


def _domain_nonnull_columns(node) -> set[str]:
    if not isinstance(node, dict):
        return set()
    if node.get("op") == "and":
        return _domain_nonnull_columns(node["left"]) | _domain_nonnull_columns(
            node["right"]
        )
    if node.get("op") == "is_not" and node.get("right") == {
        "op": "literal",
        "value": None,
    }:
        column = node.get("left", {})
        if column.get("op") == "column":
            name = column["name"]
            table = column.get("table")
            return {name, f"{table}.{name}"} if table else {name}
    return set()


def _merge_families(families) -> str:
    values = {family for family in families if family != "null"}
    if not values:
        return "null"
    if values <= {"integer", "numeric"}:
        return "numeric" if "numeric" in values else "integer"
    if len(values) == 1:
        return values.pop()
    raise ValueError("Expression branches have incompatible types")


def _expression_info(spec, columns, *, domain=None) -> tuple[str, bool]:
    parameters = {item["name"]: item for item in spec.get("bound_parameters", [])}
    nonnull = _domain_nonnull_columns(domain) if domain else set()

    def infer(node):
        op = node["op"]
        if op == "column":
            name = node["name"]
            reference = f"{node['table']}.{name}" if node.get("table") else name
            if reference not in columns:
                raise ValueError(
                    f"Missing type metadata for referenced column {reference}"
                )
            family, nullable = columns[reference]
            return family, nullable and reference not in nonnull and name not in nonnull
        if op == "literal":
            return _literal_info(node["value"])
        if op == "parameter":
            item = parameters.get(node["name"])
            if item is None:
                raise ValueError(f"Missing bound parameter {node['name']}")
            family = (
                _type_family(node["type"])
                if node.get("type")
                else _literal_info(item["value"])[0]
            )
            return family, item["value"] is None
        if op == "cast":
            _source_family, nullable = infer(node["value"])
            return _type_family(node["type"]), nullable
        if op == "function":
            values = [infer(item) for item in node["args"]]
            if node["name"] in {"lower", "upper", "trim", "concat"}:
                if any(family != "text" for family, _nullable in values):
                    raise ValueError(f"{node['name']} requires text arguments")
                return "text", any(nullable for _family, nullable in values)
            if node["name"] == "json_extract":
                if values[0][0] not in {"json", "text"} or values[1][0] != "text":
                    raise ValueError("json_extract requires JSON/text and a text path")
                return "text", True
            if node["name"] == "split_part":
                if (
                    values[0][0] != "text"
                    or values[1][0] != "text"
                    or values[2][0] != "integer"
                ):
                    raise ValueError(
                        "split_part requires text, text, and integer arguments"
                    )
                return "text", values[0][1]
            if node["name"] == "coalesce":
                return _merge_families(family for family, _nullable in values), all(
                    nullable for _family, nullable in values
                )
            if node["name"] == "date_trunc":
                if values[1][0] not in {"date", "datetime"}:
                    raise ValueError("date_trunc requires a date or datetime value")
                return "datetime", values[1][1]
            raise ValueError(f"Unsupported expression function {node['name']}")
        if op in {"add", "sub", "mul", "div", "mod"}:
            values = [infer(node["left"]), infer(node["right"])]
            if any(
                family not in {"integer", "numeric"} for family, _nullable in values
            ):
                raise ValueError(f"{op} requires numeric arguments")
            return _merge_families(family for family, _nullable in values), any(
                nullable for _family, nullable in values
            )
        if op in {"eq", "ne", "lt", "le", "gt", "ge"}:
            values = [infer(node["left"]), infer(node["right"])]
            _merge_families(family for family, _nullable in values)
            return "boolean", any(nullable for _family, nullable in values)
        if op in {"and", "or"}:
            values = [infer(node["left"]), infer(node["right"])]
            if any(family != "boolean" for family, _nullable in values):
                raise ValueError(f"{op} requires boolean arguments")
            return "boolean", any(nullable for _family, nullable in values)
        if op == "not":
            family, nullable = infer(node["value"])
            if family != "boolean":
                raise ValueError("not requires a boolean argument")
            return family, nullable
        if op == "neg":
            family, nullable = infer(node["value"])
            if family not in {"integer", "numeric"}:
                raise ValueError("negation requires a numeric argument")
            return family, nullable
        if op in {"is", "is_not"}:
            infer(node["left"])
            infer(node["right"])
            return "boolean", False
        if op == "in":
            source = infer(node["value"])
            values = [infer(item) for item in node["values"]]
            _merge_families([source[0], *(family for family, _nullable in values)])
            return "boolean", source[1] or any(nullable for _family, nullable in values)
        if op == "case":
            branches = [
                (infer(item["when"]), infer(item["then"])) for item in node["whens"]
            ]
            if any(condition[0] != "boolean" for condition, _result in branches):
                raise ValueError("case conditions must be boolean")
            results = [result for _condition, result in branches]
            complete = node.get("has_else") or (
                domain is not None and _implies(domain, _partial_coverage(node))
            )
            if node.get("has_else"):
                results.append(infer(node["else"]))
            return _merge_families(
                family for family, _nullable in results
            ), not complete or any(nullable for _family, nullable in results)
        if op == "mapping":
            source = infer(node["value"])
            entries = [
                (infer(item["key"]), infer(item["value"])) for item in node["entries"]
            ]
            _merge_families([source[0], *(key[0] for key, _result in entries)])
            results = [result for _key, result in entries]
            complete = node.get("has_else") or (
                domain is not None and _implies(domain, _partial_coverage(node))
            )
            if node.get("has_else"):
                results.append(infer(node["else"]))
            return _merge_families(
                family for family, _nullable in results
            ), not complete or any(nullable for _family, nullable in results)
        raise ValueError(f"Unsupported expression operator {op}")

    return infer(spec["canonical_ast"])


def _compatible(source: str, target: str) -> bool:
    return source == target or source == "integer" and target == "numeric"


def _validate_assignment(spec, column, columns, *, label: str, domain=None) -> None:
    source_family, nullable = _expression_info(spec, columns, domain=domain)
    target_family = _type_family(column.type)
    if source_family == "null":
        if not column.nullable:
            raise ValueError(
                f"{label}: NULL expression cannot populate non-nullable target"
            )
    elif not _compatible(source_family, target_family):
        raise ValueError(
            f"{label}: {source_family} expression is incompatible with {target_family} target; use an explicit cast"
        )
    if nullable and not column.nullable:
        raise ValueError(
            f"{label}: nullable expression cannot populate non-nullable target"
        )


def _require_boolean(spec, columns, *, label: str) -> None:
    family, _nullable = _expression_info(spec, columns)
    if family != "boolean":
        raise ValueError(f"{label} must be a boolean expression")


def discover_data(model_paths, data_paths=(), *, model_tables=None) -> DiscoveredData:
    import sys

    from dbwarden.engine.model_discovery.path_discovery import (
        _collect_model_files,
        load_model_from_path,
    )

    root = str(Path.cwd().resolve())
    if root not in sys.path:
        sys.path.insert(0, root)

    models, transitions, seen_files = {}, {}, set()
    for configured, allow_models in ((model_paths, True), (data_paths, False)):
        paths = (
            [configured] if isinstance(configured, (str, Path)) else list(configured)
        )
        for value in paths:
            path = Path(value)
            if not path.exists():
                raise FileNotFoundError(
                    f"Configured data declaration path does not exist: {path}"
                )
            if path.is_symlink():
                raise ValueError(
                    f"Configured data declaration path cannot be a symlink: {path}"
                )
            if not path.is_file() and not path.is_dir():
                raise ValueError(
                    f"Configured data declaration path is not a regular file or directory: {path}"
                )
        for path in sorted(_collect_model_files(paths)):
            resolved = Path(path).resolve()
            if resolved in seen_files:
                continue
            seen_files.add(resolved)
            if str(path).endswith(".data.py"):
                raise ValueError(
                    "Frozen .data.py artifacts cannot be live declarations"
                )
            module = load_model_from_path(path)
            if module is None:
                continue
            for value in vars(module).values():
                if not isinstance(value, type) or value.__module__ != module.__name__:
                    continue
                if issubclass(value, DataTransition) and value is not DataTransition:
                    declaration_id = _declaration_id(value)
                    if (
                        declaration_id in transitions
                        and transitions[declaration_id] is not value
                    ):
                        raise ValueError(
                            f"Duplicate data transition declaration ID: {declaration_id}"
                        )
                    transitions[declaration_id] = value
                elif allow_models and getattr(value, "__table__", None) is not None:
                    if (
                        model_tables is not None
                        and value.__table__.name not in model_tables
                    ):
                        continue
                    table_name = _table_name(value.__table__)
                    if table_name in models and models[table_name] is not value:
                        raise ValueError(
                            f"Multiple mapped classes own data table {table_name}"
                        )
                    models[table_name] = value
    return DiscoveredData(
        tuple(models[key] for key in sorted(models)),
        tuple(transitions[key] for key in sorted(transitions)),
    )


def _declaration_id(value) -> str:
    explicit = getattr(value, "declaration_id", None)
    if explicit:
        if not isinstance(explicit, str):
            raise ValueError("declaration_id must be a string")
        return explicit
    path = Path(inspect.getfile(value)).resolve()
    try:
        module = path.relative_to(Path.cwd().resolve()).with_suffix("").as_posix()
    except ValueError as exc:
        raise ValueError("Data declarations must belong to the project root") from exc
    return f"{module}:{value.__qualname__}"


def _table_name(table) -> str:
    return f"{table.schema}.{table.name}" if table.schema else table.name


def _table_ref(table, database) -> dict:
    return {"database": database, "schema": table.schema, "table": table.name}


def _model_schema(models):
    from sqlalchemy import CheckConstraint

    result = []
    for model in sorted(models, key=lambda item: _table_name(item.__table__)):
        table = model.__table__
        foreign_keys = []
        for constraint in table.foreign_key_constraints:
            targets = [element.column for element in constraint.elements]
            target_table = targets[0].table
            foreign_keys.append(
                {
                    "name": constraint.name,
                    "columns": [element.parent.name for element in constraint.elements],
                    "target": {
                        "schema": target_table.schema,
                        "table": target_table.name,
                        "columns": [column.name for column in targets],
                    },
                }
            )
        result.append(
            {
                "schema": table.schema,
                "name": table.name,
                "columns": [
                    {
                        "name": column.name,
                        "type": str(column.type),
                        "nullable": bool(column.nullable),
                        "primary_key": bool(column.primary_key),
                        "unique": bool(column.unique),
                    }
                    for column in table.columns
                ],
                "foreign_keys": sorted(
                    foreign_keys,
                    key=lambda item: canonical_bytes([item["columns"], item["target"]]),
                ),
                "unique_constraints": sorted(
                    (
                        {
                            "name": constraint.name,
                            "columns": [column.name for column in constraint.columns],
                        }
                        for constraint in table.constraints
                        if isinstance(constraint, UniqueConstraint)
                    ),
                    key=canonical_bytes,
                ),
                "check_constraints": sorted(
                    (
                        {
                            "name": constraint.name,
                            "expression": str(constraint.sqltext),
                        }
                        for constraint in table.constraints
                        if isinstance(constraint, CheckConstraint)
                    ),
                    key=canonical_bytes,
                ),
            }
        )
    return result


def _key(table, key, *, label) -> list[str]:
    key = list(key or [column.name for column in table.primary_key.columns])
    if not key or len(set(key)) != len(key) or any(name not in table.c for name in key):
        raise ValueError(f"{label}: identity requires distinct existing columns")
    candidates = [{column.name for column in table.primary_key.columns}]
    candidates.extend(
        {column.name for column in constraint.columns}
        for constraint in table.constraints
        if isinstance(constraint, UniqueConstraint)
    )
    candidates.extend(
        {column.name for column in index.columns}
        for index in table.indexes
        if index.unique and all(hasattr(column, "name") for column in index.columns)
    )
    if set(key) not in candidates:
        raise ValueError(f"{label}: key must match a primary or unique constraint")
    return key


def _generated(column) -> bool:
    return bool(
        column.server_default is not None
        or column.identity is not None
        or column.computed is not None
        or (
            column.primary_key
            and isinstance(column.type, (BigInteger, SmallInteger, Integer))
            and column.autoincrement in (True, "auto")
        )
    )


def _archive_ref(value, database, *, label):
    if not isinstance(value, ArchiveTable):
        raise TypeError(f"{label} requires archive_table(...)")
    if value.table.startswith(INTERNAL_TABLE_PREFIXES):
        raise ValueError("Archive destinations cannot use internal DBWarden names")
    return {"database": database, "schema": value.schema, "table": value.table}


def _value(value, column, *, csv_input=False):
    if value is None:
        if not column.nullable:
            raise ValueError(f"{column.name}: NULL is not allowed")
        return None
    try:
        expected = column.type.python_type
    except (AttributeError, NotImplementedError) as exc:
        raise ValueError(
            f"{column.name}: unsupported managed-row type {column.type}"
        ) from exc
    if csv_input:
        try:
            if expected is bool:
                if value.lower() not in {"true", "false"}:
                    raise ValueError("expected true or false")
                value = value.lower() == "true"
            elif expected in {date, datetime}:
                value = expected.fromisoformat(value)
            elif expected in {dict, list}:
                value = json.loads(value)
            elif expected is not str:
                value = expected(value)
        except (TypeError, ValueError, InvalidOperation) as exc:
            raise ValueError(
                f"{column.name}: invalid CSV value for {column.type}"
            ) from exc
    if expected is Decimal and type(value) is int:
        value = Decimal(value)
    if expected is float and type(value) is int:
        value = float(value)
    if isinstance(column.type, JSON):
        try:
            return canonical_value(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{column.name}: invalid JSON value") from exc
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError(f"{column.name}: datetimes must be timezone-aware")
        value = value.astimezone(timezone.utc).replace(microsecond=0)
    if not isinstance(value, expected) or (expected is int and isinstance(value, bool)):
        raise ValueError(
            f"{column.name}: expected {expected.__name__}, got {type(value).__name__}"
        )
    if (
        isinstance(value, str)
        and getattr(column.type, "length", None)
        and len(value) > column.type.length
    ):
        raise ValueError(f"{column.name}: value exceeds declared length")
    return canonical_value(value)


def _load_rows(declaration, table, project_root) -> tuple[list[dict], dict]:
    csv_input = False
    source = {"kind": "inline", "path": None, "checksum": None, "rows": None}
    values = declaration.values
    if declaration.source is not None:
        root = Path(project_root).resolve()
        supplied = Path(declaration.source)
        unresolved = root / supplied
        path = unresolved.resolve()
        if (
            supplied.is_absolute()
            or not path.is_relative_to(root)
            or any(
                part.is_symlink()
                for part in [unresolved, *unresolved.parents]
                if part != root.parent
            )
            or not path.is_file()
        ):
            raise ValueError(
                "Static data source must be a regular file inside the project"
            )
        content = path.read_bytes()
        source.update(
            kind=path.suffix.lower().removeprefix("."),
            path=path.relative_to(root).as_posix(),
        )
        if path.suffix.lower() == ".json":
            try:
                values = json.loads(
                    content.decode("utf-8-sig"), object_pairs_hook=_json_object
                )
            except (UnicodeError, json.JSONDecodeError, ValueError) as exc:
                raise ValueError(
                    f"Invalid static JSON data source: {path.name}"
                ) from exc
        elif path.suffix.lower() == ".csv":
            import io

            try:
                reader = csv.DictReader(io.StringIO(content.decode("utf-8-sig")))
                fieldnames = [
                    unicodedata.normalize("NFC", name)
                    for name in (reader.fieldnames or [])
                ]
                if (
                    not fieldnames
                    or len(set(fieldnames)) != len(fieldnames)
                    or any("\x00" in name for name in fieldnames)
                ):
                    raise ValueError
                reader.fieldnames = fieldnames
                values = list(reader)
            except (UnicodeError, csv.Error, ValueError):
                raise ValueError("CSV headers must be present and unique")
            if any(None in row for row in values):
                raise ValueError("CSV rows contain more fields than the header")
            csv_input = True
        else:
            raise ValueError("Static row sources support .csv and .json")
    if not isinstance(values, (list, tuple)) or any(
        not isinstance(row, dict) for row in values
    ):
        raise ValueError("Managed row input must be an array of objects")
    normalized = []
    for row in values:
        if any(not isinstance(name, str) for name in row):
            raise ValueError("Managed-row column names must be strings")
        pairs = [
            (unicodedata.normalize("NFC", name), value) for name, value in row.items()
        ]
        if any(not name or "\x00" in name for name, _ in pairs) or len(
            {name for name, _ in pairs}
        ) != len(pairs):
            raise ValueError(
                "Managed-row columns must be nonempty and Unicode-normalized unique"
            )
        row = dict(pairs)
        if any(name not in table.c for name in row):
            raise ValueError(f"Unknown managed-row columns for {table.name}")
        normalized.append(
            {
                name: _value(value, table.c[name], csv_input=csv_input)
                for name, value in row.items()
            }
        )
    source["rows"] = normalized
    return normalized, source


def _expr(value, columns, parameters, backend, settings=None):
    names = _expression_parameter_names(value)
    supplied = (
        None
        if parameters is None
        else {name: parameters[name] for name in names if name in parameters}
    )
    return canonical_expression(
        value,
        columns=columns,
        parameters=supplied,
        backend=backend,
        settings=settings,
    )


def _cast_spec(spec, column, columns, backend):
    frozen = dict(spec)
    frozen["canonical_ast"] = {
        "op": "cast",
        "value": spec["canonical_ast"],
        "type": str(column.type),
    }
    return canonical_expression(
        frozen,
        columns=columns,
        backend=backend,
        settings=spec.get("backend_settings"),
    )


def _rollback(policy, **kwargs):
    return {
        "policy": policy,
        "preservation_ref": None,
        "capture_ref": None,
        "proof_status": "irreversible" if policy == "irreversible" else "guarded",
        "limitations": [],
        **kwargs,
    }


def _latest_compatible_snapshot(
    source,
    *,
    database,
    backend,
    registry_path=None,
    schema_dir=None,
):
    from .snapshots import _read_registry, _registry_path, resolve_snapshot

    registry = _read_registry(_registry_path(registry_path))
    entries = [
        item
        for item in registry["snapshots"]
        if item.get("database") == database and item.get("backend") == backend
    ]
    by_id = {}
    for item in entries:
        identity = item.get("snapshot_id")
        if not isinstance(identity, str) or not identity or identity in by_id:
            raise ValueError(f"Ambiguous snapshot lineage for database {database}")
        by_id[identity] = item
    parent_ids = {
        parent
        for item in entries
        for parent in item.get("parent_snapshot_ids", [])
        if parent in by_id
    }
    heads = sorted(set(by_id) - parent_ids)
    if not heads:
        raise ValueError(
            f"No registered snapshot contains {source.table}; specify snapshot="
        )
    if len(heads) != 1:
        raise ValueError(
            f"Latest compatible snapshot for {source.table} is ambiguous; specify snapshot="
        )
    reachable = set()
    pending = [heads[0]]
    while pending:
        identity = pending.pop()
        if identity in reachable:
            continue
        reachable.add(identity)
        parents = by_id[identity].get("parent_snapshot_ids", [])
        if not isinstance(parents, list) or any(
            parent not in by_id for parent in parents
        ):
            raise ValueError(f"Invalid snapshot lineage at {identity}")
        pending.extend(parents)
    compatible = []
    for identity in sorted(reachable):
        snapshot = resolve_snapshot(
            identity,
            database=database,
            backend=backend,
            registry_path=registry_path,
            schema_dir=schema_dir,
        )
        tables = snapshot.get("schema_state", {}).get("tables", {})
        qualified = f"{source.schema}.{source.table}" if source.schema else source.table
        state = tables.get(qualified)
        if source.schema and state is None:
            candidate = tables.get(source.table)
            if isinstance(candidate, dict) and candidate.get("schema") == source.schema:
                state = candidate
        if isinstance(state, dict) and state.get("schema") in (None, source.schema):
            compatible.append((identity, snapshot))
    compatible_ids = {identity for identity, _snapshot in compatible}
    newest = []
    for identity, snapshot in compatible:
        descendants = set()
        for candidate in compatible_ids - {identity}:
            pending = list(by_id[candidate].get("parent_snapshot_ids", []))
            while pending:
                parent = pending.pop()
                if parent == identity:
                    descendants.add(candidate)
                    break
                pending.extend(by_id[parent].get("parent_snapshot_ids", []))
        if not descendants:
            newest.append(snapshot)
    if len(newest) != 1:
        raise ValueError(
            f"Latest compatible snapshot for {source.table} is ambiguous or missing; specify snapshot="
        )
    return newest[0]


def required_parameters(discovered: DiscoveredData) -> tuple[str, ...]:
    if not isinstance(discovered, DiscoveredData):
        raise TypeError("required_parameters requires DiscoveredData")
    names = set()

    def add(value):
        if value is not None:
            names.update(_expression_parameter_names(value))

    for model in discovered.models:
        data = getattr(model, "Data", None)
        if not isinstance(data, type) or not issubclass(data, DataMeta):
            continue
        for validation in data.validations:
            add(validation.expression)
        if data.managed_rows is not None:
            add(data.managed_rows.scope)
        for declaration in data.transformations:
            add(declaration.expression)
            add(declaration.when)
    from .advanced import MergedSource

    for transition in discovered.transitions:
        for target in transition.targets:
            add(target.where)
            for _name, expression in target.mappings:
                add(expression)
        if isinstance(transition.source, MergedSource):
            for merge_input in transition.source.inputs:
                for expression in merge_input.mappings.values():
                    add(expression)
            for rule in transition.source.values.values():
                add(rule.expression)
    return tuple(sorted(names))


def compile_data(
    discovered: DiscoveredData,
    *,
    database: str,
    backend: str,
    parameters=None,
    project_root=None,
    registry_path=None,
    schema_dir=None,
) -> dict:
    from .snapshots import resolve_snapshot

    if not isinstance(discovered, DiscoveredData):
        raise TypeError("compile_data requires DiscoveredData")
    if (
        not isinstance(database, str)
        or not database
        or not isinstance(backend, str)
        or not backend
    ):
        raise ValueError("database and backend must be non-empty strings")
    if parameters is not None and not isinstance(parameters, Mapping):
        raise TypeError("parameters must be a mapping")
    if any(
        not isinstance(model, type) or getattr(model, "__table__", None) is None
        for model in discovered.models
    ):
        raise TypeError("DiscoveredData.models must contain mapped model classes")
    if any(
        not isinstance(item, type) or not issubclass(item, DataTransition)
        for item in discovered.transitions
    ):
        raise TypeError(
            "DiscoveredData.transitions must contain DataTransition classes"
        )
    project_root = Path(project_root or Path.cwd())
    spec = empty_spec(database, backend)
    spec["validations"] = []
    models = {_table_name(model.__table__): model for model in discovered.models}
    declarations = []
    for table_name, model in sorted(models.items()):
        if model.__table__.name.startswith(INTERNAL_TABLE_PREFIXES):
            raise ValueError(
                "Internal DBWarden table names cannot own declarative data"
            )
        data = getattr(model, "Data", None)
        if data is None:
            continue
        if not isinstance(data, type) or not issubclass(data, DataMeta):
            raise TypeError(f"{model.__name__}.Data must inherit DataMeta")
        table = model.__table__
        columns = [
            name
            for column in table.c
            for name in (column.name, f"{table.name}.{column.name}")
        ]
        column_info = _model_column_info(table)
        base_id = _declaration_id(model) + ".Data"
        validations = []
        for validation in data.validations:
            validation_expression = _expr(
                validation.expression, columns, parameters, backend
            )
            _require_boolean(
                validation_expression,
                column_info,
                label=f"{base_id} validation",
            )
            validations.append(
                {"expression": validation_expression, "message": validation.message}
            )
        if validations:
            item = {
                "declaration_id": base_id + ".validations",
                "target_table": _table_ref(table, database),
                "validations": validations,
                "category": None,
            }
            spec["validations"].append(item)
            declarations.append(item)
        if data.managed_rows is not None:
            declaration = data.managed_rows
            key = _key(table, declaration.key, label=base_id)
            values, source = _load_rows(declaration, table, project_root)
            owned = list(
                declaration.owned_columns
                or sorted(set().union(*(set(row) for row in values)) - set(key))
            )
            if any(name not in table.c or name in key for name in owned):
                raise ValueError(
                    f"{base_id}: owned_columns must name existing non-key columns"
                )
            identities = set()
            for row in values:
                if set(row) != set(key + owned):
                    raise ValueError(
                        f"{base_id}: every row must contain exactly key and owned_columns"
                    )
                identity = canonical_bytes([row[name] for name in key])
                if any(row[name] is None for name in key) or identity in identities:
                    raise ValueError(
                        f"{base_id}: duplicate or NULL managed-row identity"
                    )
                identities.add(identity)
            for column in table.c:
                if (
                    not column.nullable
                    and not _generated(column)
                    and column.name not in key + owned
                ):
                    raise ValueError(
                        f"{base_id}: missing required insert column {column.name}"
                    )
            source["rows"] = sorted(
                values, key=lambda row: canonical_bytes([row[name] for name in key])
            )
            source["checksum"] = digest(source["rows"], "row-source")
            item = {
                "declaration_id": base_id + ".managed_rows",
                "target_table": _table_ref(table, database),
                "key_columns": key,
                "row_source": source,
                "owned_columns": sorted(owned),
                "on_missing": declaration.on_missing,
                "scope": None
                if declaration.scope is None
                else _expr(declaration.scope, columns, parameters, backend),
                "acknowledgement": declaration.acknowledge_delete
                or declaration.acknowledge_archive,
                "archive": None
                if declaration.archive_to is None
                else {
                    "destination": _archive_ref(
                        declaration.archive_to,
                        database,
                        label=f"{base_id} managed-row archive",
                    ),
                    "columns": [column.name for column in table.c],
                    "identity_edge": digest(
                        [
                            base_id,
                            source["checksum"],
                            declaration.archive_to.table,
                            declaration.archive_to.schema,
                        ],
                        "managed-archive-edge",
                    ),
                },
                "rollback": _rollback(declaration.rollback),
                "category": declaration.category,
            }
            if item["scope"] is not None:
                _require_boolean(
                    item["scope"],
                    column_info,
                    label=f"{base_id} managed-row scope",
                )
            if (
                item["archive"]
                and item["archive"]["destination"] == item["target_table"]
            ):
                raise ValueError(
                    "Managed-row archive destination cannot equal its target"
                )
            spec["managed_rows"].append(item)
            declarations.append(item)
        targets = set()
        for declaration in data.transformations:
            if declaration.target not in table.c or declaration.target in targets:
                raise ValueError(
                    f"{base_id}: missing or duplicate transformation target {declaration.target}"
                )
            targets.add(declaration.target)
            source_expression = _expr(
                declaration.expression,
                columns,
                parameters,
                backend,
                declaration.settings,
            )
            if declaration.rollback != "capture" and any(
                ref.rsplit(".", 1)[-1] == declaration.target
                for ref in source_expression["referenced_columns"]
            ):
                raise ValueError(
                    f"{base_id}: a transformation that reads its target column "
                    "requires rollback=capture"
                )
            domain = (
                None
                if declaration.when is None
                else _expr(
                    declaration.when, columns, parameters, backend, declaration.settings
                )
            )
            if domain is not None:
                _require_boolean(
                    domain,
                    column_info,
                    label=f"{base_id} transformation domain",
                )
            if not source_expression.get("total_domain_complete", True):
                if domain is None or declaration.on_unmatched != "keep":
                    raise ValueError(
                        f"{base_id}: partial expressions require when= and on_unmatched=keep"
                    )
                if any(
                    not _implies(domain["canonical_ast"], coverage)
                    for coverage in _partial_nodes(source_expression["canonical_ast"])
                ):
                    raise ValueError(
                        f"{base_id}: transformation domain does not prove partial expression coverage"
                    )
            _validate_assignment(
                source_expression,
                table.c[declaration.target],
                column_info,
                label=f"{base_id}.{declaration.target}",
                domain=None if domain is None else domain["canonical_ast"],
            )
            expression = _cast_spec(
                source_expression,
                table.c[declaration.target],
                columns,
                backend,
            )
            if (
                declaration.rollback == "clear"
                and not table.c[declaration.target].nullable
            ):
                raise ValueError(
                    f"{base_id}: clear rollback requires a nullable target"
                )
            item = {
                "declaration_id": base_id + ".derive." + declaration.target,
                "target_table": _table_ref(table, database),
                "target_columns": [declaration.target],
                "expression": expression,
                "source_expression": source_expression,
                "domain": domain,
                "on_unmatched": declaration.on_unmatched,
                "rollback": _rollback(
                    declaration.rollback,
                    capture_ref=(
                        "_dbwarden_capture_"
                        + digest(base_id + ".derive." + declaration.target, "capture")[
                            :20
                        ]
                        if declaration.rollback == "capture"
                        else None
                    ),
                ),
                "max_rows": declaration.max_rows,
                "key_columns": [column.name for column in table.primary_key.columns],
                "execution": {},
                "category": declaration.category,
            }
            if declaration.rollback == "capture" and not item["key_columns"]:
                raise ValueError(f"{base_id}: capture rollback requires a primary key")
            if declaration.execution is not None:
                from .batches import execution_spec

                item["execution"] = execution_spec(declaration.execution)
                if set(item["execution"]["key"]) != set(item["key_columns"]):
                    raise ValueError(
                        "Transformation batch key must equal the target primary key"
                    )
                if declaration.target in item["execution"]["key"]:
                    raise ValueError(
                        "Transformation target cannot be part of its batch key"
                    )
            spec["transformations"].append(item)
            declarations.append(item)
    used_sources = set()
    for transition in discovered.transitions:
        coverage_mode = _normalize_transition_coverage(
            transition.coverage, transition.overlap
        )
        declaration_id = _declaration_id(transition)
        source = transition.source
        from .advanced import MergedSource, compile_merge_source

        merge_metadata = None
        merge_lineages = []
        mapped_source = not isinstance(source, (HistoricalTable, MergedSource))
        if isinstance(source, MergedSource):
            source, snapshot, merge_metadata = compile_merge_source(
                source,
                database=database,
                backend=backend,
                parameters=parameters,
                registry_path=registry_path,
                schema_dir=schema_dir,
            )
            if len(transition.targets) != 1:
                raise ValueError("A merged transition requires exactly one target")
            for merge_input in merge_metadata["inputs"]:
                ref = merge_input["source"]
                name = (
                    f"{ref['schema']}.{ref['table']}"
                    if ref.get("schema")
                    else ref["table"]
                )
                if name in models:
                    raise ValueError(f"Merge source {name} is still a desired model")
                if name in used_sources:
                    raise ValueError(
                        "A historical source can belong to only one transition"
                    )
                used_sources.add(name)
                merge_lineages.append(merge_input["snapshot"])
        elif not isinstance(source, HistoricalTable):
            source_table = getattr(source, "__table__", None)
            if source_table is None or not transition.source_snapshot:
                raise ValueError(
                    f"{declaration_id}: ORM sources require source_snapshot"
                )
            source = HistoricalTable(
                source_table.name, transition.source_snapshot, source_table.schema
            )
        source_name = (
            f"{source.schema}.{source.table}" if source.schema else source.table
        )
        if merge_metadata is None and source_name in used_sources:
            raise ValueError(
                "Multiple transitions from one source require a single multi-target declaration"
            )
        if merge_metadata is None and source_name in models:
            if not mapped_source or transition.on_complete != "keep":
                raise ValueError(
                    f"Historical source {source_name} is still a desired model; use a mapped source with on_complete=keep"
                )
        elif transition.on_complete == "keep":
            raise ValueError(
                "on_complete=keep requires the mapped source to remain in model_paths"
            )
        if merge_metadata is None:
            used_sources.add(source_name)
            snapshot = (
                _latest_compatible_snapshot(
                    source,
                    database=database,
                    backend=backend,
                    registry_path=registry_path,
                    schema_dir=schema_dir,
                )
                if source.snapshot is None
                else resolve_snapshot(
                    source.snapshot,
                    database=database,
                    backend=backend,
                    registry_path=registry_path,
                    schema_dir=schema_dir,
                )
            )
            source = HistoricalTable(
                source.table, snapshot["snapshot_id"], source.schema
            )
        state = snapshot["schema_state"]
        tables = state.get("tables", {}) if isinstance(state, dict) else None
        if not isinstance(tables, dict):
            raise TypeError(f"Pinned snapshot {source.snapshot} has malformed tables")
        source_state = tables.get(source_name)
        if source.schema:
            bare_state = tables.get(source.table)
            if (
                isinstance(bare_state, dict)
                and bare_state.get("schema") == source.schema
            ):
                if source_state is not None and canonical_bytes(
                    source_state
                ) != canonical_bytes(bare_state):
                    raise ValueError(
                        f"{source_name} is ambiguous in pinned snapshot {source.snapshot}"
                    )
                source_state = bare_state
        if isinstance(source_state, dict) and source_state.get("schema") not in (
            None,
            source.schema,
        ):
            raise ValueError(
                f"{source_name} schema does not match pinned snapshot {source.snapshot}"
            )
        if source_state is None:
            raise ValueError(
                f"{source_name} is missing from pinned snapshot {source.snapshot}"
            )
        if not isinstance(source_state, dict):
            raise TypeError(f"{source_name}: malformed snapshot table metadata")
        source_columns = _snapshot_columns(
            source_state.get("columns"), table=source_name
        )
        source_info = _snapshot_column_info(source_columns, table=source.table)
        columns = [
            name
            for column in source_columns
            for name in (column, f"{source.table}.{column}")
        ]
        identity = []
        for value in transition.source_identity:
            if isinstance(value, str):
                name = value
            else:
                identity_expression = _expr(value, columns, parameters, backend)
                ast = identity_expression["canonical_ast"]
                refs = identity_expression["referenced_columns"]
                if ast.get("op") != "column" or len(refs) != 1:
                    raise ValueError(
                        "Source identity must contain direct column references"
                    )
                name = refs[0].rsplit(".", 1)[-1]
            identity.append(name)
        if (
            not identity
            or len(set(identity)) != len(identity)
            or any(name not in source_columns for name in identity)
        ):
            raise ValueError(
                "Transitions require distinct source_identity columns from the pinned snapshot"
            )
        for name in identity:
            if name not in source_info:
                raise ValueError(
                    f"Missing type metadata for source identity {source_name}.{name}"
                )
        targets = []
        target_names = set()
        target_tables = {}
        for target in transition.targets:
            table = getattr(target.model, "__table__", None)
            if table is None or _table_name(table) not in models:
                raise ValueError(
                    "Transition targets must be current models in model_paths"
                )
            if _table_name(table) in target_names:
                raise ValueError(
                    "Multiple mappings to one target are many-to-one; not supported"
                )
            target_names.add(_table_name(table))
            if _table_name(table) == source_name:
                raise ValueError(
                    "A transition target cannot be its retained source table"
                )
            target_tables[_table_name(table)] = table
            key = _key(table, target.key, label=declaration_id)
            mappings = {}
            source_mappings = {}
            for name, expression in target.mappings:
                if name not in table.c:
                    raise ValueError(f"Unknown transition target column {name}")
                source_mappings[name] = _expr(expression, columns, parameters, backend)
                if not source_mappings[name].get("total_domain_complete", True):
                    raise ValueError("Transition mappings require total expressions")
                _validate_assignment(
                    source_mappings[name],
                    table.c[name],
                    source_info,
                    label=f"{declaration_id} mapping {table.name}.{name}",
                )
                mappings[name] = _cast_spec(
                    source_mappings[name], table.c[name], columns, backend
                )
            if not set(key) <= mappings.keys():
                raise ValueError(
                    "Every transition target identity column must be mapped"
                )
            for column in table.c:
                if (
                    not column.nullable
                    and not _generated(column)
                    and column.name not in mappings
                ):
                    raise ValueError(
                        f"Missing required target column {table.name}.{column.name}"
                    )
            predicate = (
                None
                if target.where is None
                else _expr(target.where, columns, parameters, backend)
            )
            if predicate is not None:
                _require_boolean(
                    predicate,
                    source_info,
                    label=f"{declaration_id} predicate for {table.name}",
                )
            targets.append(
                {
                    "target_model": {
                        "import_path": _declaration_id(target.model).split(":")[0],
                        "model_name": target.model.__qualname__,
                        "table": _table_ref(table, database),
                    },
                    "target_table": _table_ref(table, database),
                    "mappings": mappings,
                    "source_mappings": source_mappings,
                    "predicate": predicate,
                    "depends_on": [],
                    "identity": key,
                    "priority": target.priority,
                    "conflict": {
                        "policy": target.on_conflict,
                        "owned_columns": sorted(mappings),
                        "equivalence_expression": None,
                        "acknowledgement": target.acknowledge_overwrite,
                    },
                }
            )
        by_name = {name: target for name, target in zip(target_tables, targets)}
        dependencies = {name: set() for name in by_name}
        source_aliases = {source.table, source_name}
        for name, table in target_tables.items():
            for foreign_key in table.foreign_keys:
                referenced = foreign_key.target_fullname.rsplit(".", 1)[0]
                if referenced in source_aliases and transition.on_complete != "keep":
                    raise ValueError(
                        f"Transition target {name} has a foreign key to removed historical source {source_name}"
                    )
                if referenced in by_name and referenced != name:
                    dependencies[name].add(referenced)
            by_name[name]["depends_on"] = [
                by_name[dependency]["target_table"]
                for dependency in sorted(dependencies[name])
            ]
        ordered_targets = []
        remaining = set(by_name)
        while remaining:
            ready = sorted(
                name for name in remaining if not dependencies[name] & remaining
            )
            if not ready:
                raise ValueError(
                    "Transition target foreign-key cycle requires deferred constraints, which are unsupported"
                )
            for name in ready:
                ordered_targets.append(by_name[name])
                remaining.remove(name)
        targets = ordered_targets
        if transition.overlap == "priority" and (
            any(target["priority"] is None for target in targets)
            or len({target["priority"] for target in targets}) != len(targets)
        ):
            raise ValueError(
                "Priority overlap requires distinct explicit target priorities"
            )
        if transition.overlap != "priority" and any(
            target["priority"] is not None for target in targets
        ):
            raise ValueError(
                "Target priorities require coverage=all_assigned_once and priority overlap"
            )
        lineage = {key: snapshot.get(key) for key in _LINEAGE_FIELDS}
        item = {
            "declaration_id": declaration_id,
            "source": {
                "database": database,
                "schema": source.schema,
                "table": source.table,
            },
            "source_snapshot": lineage,
            "identity": {
                "source_identity": identity,
                "target_identity": [target["identity"] for target in targets],
                "relationship_cardinality": "one_to_many"
                if len(targets) > 1
                else "one_to_one",
                "identity_edges": [],
                "duplicate_policy": "error",
            },
            "coverage": {
                "mode": coverage_mode,
                "on_unmatched": transition.on_unmatched,
                "archive_acknowledgement": transition.acknowledge_archive,
                "overlap_policy": transition.overlap,
                "target_priorities": [target["priority"] for target in targets],
                "validation_queries": [],
            },
            "targets": targets,
            "conflict": {
                "policy": "per_target",
                "owned_columns": None,
                "equivalence_expression": None,
                "acknowledgement": None,
            },
            "completion": {
                "policy": transition.on_complete,
                "acknowledgement": transition.acknowledge_drop
                or transition.acknowledge_archive,
                "destination": None
                if transition.archive_to is None
                else _archive_ref(
                    transition.archive_to,
                    database,
                    label=f"{declaration_id} completion archive",
                ),
            },
            "rollback": _rollback(transition.rollback),
            "execution": {
                "max_rows": transition.max_rows,
                "transaction": "required",
                "success": "once_per_epoch",
            },
            "category": transition.category,
            "source_columns": list(source_columns),
        }
        if transition.unmatched_archive_to is not None:
            item["coverage"]["archive_destination"] = _archive_ref(
                transition.unmatched_archive_to,
                database,
                label=f"{declaration_id} unmatched archive",
            )
        if merge_metadata is not None:
            item["merge"] = merge_metadata
            item["identity"]["relationship_cardinality"] = "many_to_one"
        if transition.execution is not None:
            from .batches import execution_spec

            item["execution"].update(execution_spec(transition.execution))
            if tuple(item["execution"]["key"]) != tuple(identity):
                raise ValueError("Transition batch key must equal source_identity")
        item["transition_id"] = digest(item, "transition")
        item["identity"]["identity_edges"] = [
            digest(
                [
                    item["transition_id"],
                    item["identity"]["source_identity"],
                    target["target_table"],
                    target["identity"],
                ],
                "edge-definition",
            )
            for target in item["targets"]
        ]
        item["rollback"]["preservation_ref"] = (
            "_dbwarden_preserved_" + item["transition_id"][:20]
            if transition.on_complete == "preserve"
            else None
        )
        if transition.on_complete == "archive":
            destination = item["completion"]["destination"]
            if destination.get("schema") != item["source"].get("schema"):
                raise ValueError("Source archive must remain in the source schema")
            item["rollback"]["preservation_ref"] = destination["table"]
        destinations = [
            value
            for value in (
                item["completion"].get("destination"),
                item["coverage"].get("archive_destination"),
            )
            if value is not None
        ]
        if len({canonical_bytes(value) for value in destinations}) != len(destinations):
            raise ValueError("Transition archive destinations must be distinct")
        if any(value == item["source"] for value in destinations):
            raise ValueError("Transition archive destination cannot equal the source")
        spec["transitions"].append(item)
        declarations.append(item)
        if lineage not in spec["source_snapshots"]:
            spec["source_snapshots"].append(lineage)
        for merge_lineage in merge_lineages:
            if merge_lineage not in spec["source_snapshots"]:
                spec["source_snapshots"].append(merge_lineage)
    ids = [item["declaration_id"] for item in declarations]
    if len(set(ids)) != len(ids):
        raise ValueError("Duplicate data declaration IDs")
    for key in ("managed_rows", "transformations", "validations", "transitions"):
        spec[key].sort(key=lambda item: item["declaration_id"])
    archive_destinations = []
    for item in spec["managed_rows"]:
        if item.get("archive"):
            archive_destinations.append(
                (item["declaration_id"], item["archive"]["destination"])
            )
    for item in spec["transitions"]:
        for destination in (
            item.get("completion", {}).get("destination"),
            item.get("coverage", {}).get("archive_destination"),
        ):
            if destination:
                archive_destinations.append((item["declaration_id"], destination))
    seen_archives = {}
    for declaration_id, destination in archive_destinations:
        name = (
            f"{destination['schema']}.{destination['table']}"
            if destination.get("schema")
            else destination["table"]
        )
        if name in models:
            raise ValueError(f"Archive destination {name} overlaps a desired model")
        if name in seen_archives:
            raise ValueError(
                f"Archive destination {name} is shared by {seen_archives[name]} and {declaration_id}"
            )
        seen_archives[name] = declaration_id
    spec["source_snapshots"].sort(key=canonical_bytes)
    if parameters is not None:
        used_parameters = set()

        def collect(value):
            if isinstance(value, dict):
                entries = value.get("bound_parameters")
                if isinstance(entries, list):
                    used_parameters.update(
                        entry.get("name")
                        for entry in entries
                        if isinstance(entry, dict)
                    )
                for child in value.values():
                    collect(child)
            elif isinstance(value, list):
                for child in value:
                    collect(child)

        collect(declarations)
        unknown = sorted(set(parameters) - used_parameters)
        if unknown:
            raise ValueError(f"Unknown bound parameters: {', '.join(unknown)}")
    spec["declaration_set"] = {
        "declaration_ids": sorted(ids),
        "declaration_checksum": digest(
            sorted(declarations, key=lambda item: item["declaration_id"]),
            "declarations",
        ),
    }
    spec["model_schema"] = _model_schema(discovered.models)
    return seal_spec(spec)
