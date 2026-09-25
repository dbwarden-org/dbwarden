from __future__ import annotations

import json
from pathlib import Path

from sqlglot import ErrorLevel, exp, parse
from sqlglot.errors import SqlglotError

from dbwarden.engine.file_parser import _extract_section_statements
from dbwarden.engine.safety.classifiers import Safety, classify_operation
from dbwarden.engine.safety.plans import bind_plan, read_trusted_plan
from dbwarden.files import atomic_write_text


def pending_file_issues(database, backend):
    from dbwarden.engine.safety.classifiers import LEGACY_SEVERITY
    from dbwarden.engine.safety.plans import severity_metadata
    from dbwarden.engine.version import (
        get_migration_filepaths_by_version,
        get_migrations_directory,
        get_runs_always_filepaths,
        get_runs_on_change_filepaths,
    )
    from dbwarden.exceptions import DirectoryNotFoundError
    from dbwarden.merge.marker import is_superseded
    from dbwarden.models import SafetyIssue
    from dbwarden.repositories.migrations_repo import get_migrated_versions

    try:
        directory = get_migrations_directory(database)
    except DirectoryNotFoundError:
        return []
    applied = set(get_migrated_versions(database))
    paths = [
        path
        for version, path in get_migration_filepaths_by_version(directory).items()
        if version not in applied
    ]
    paths.extend(get_runs_always_filepaths(directory))
    paths.extend(
        get_runs_on_change_filepaths(directory, changed_only=True, db_name=database)
    )
    issues = []
    for filename in paths:
        path = Path(filename)
        if is_superseded(path):
            continue
        plan, _ = read_trusted_plan(path)
        if plan is None:
            try:
                metadata = severity_metadata(
                    classify_sql(path.read_text(encoding="utf-8"), backend),
                    backend,
                    "static",
                )
            except (ValueError, UnicodeError) as exc:
                issues.append(
                    SafetyIssue(
                        "UNKNOWN",
                        "pending_migration",
                        path.name,
                        message=f"Cannot classify pending migration {path.name}: {exc}",
                    )
                )
                continue
        else:
            metadata = plan["severity"]
        for operation in metadata["ops"]:
            level = Safety(operation["severity"])
            if level in {Safety.WARN, Safety.CRITICAL, Safety.UNKNOWN}:
                issues.append(
                    SafetyIssue(
                        LEGACY_SEVERITY[level],
                        operation["kind"],
                        operation.get("table", path.name),
                        column_name=operation.get("column"),
                        message=f"Pending migration {path.name}: {level.value} {operation['kind']}",
                        required_flag="--force",
                    )
                )
    return issues


def _bounded(expression) -> bool:
    if isinstance(expression, exp.And):
        return _bounded(expression.this) or _bounded(expression.expression)
    if isinstance(expression, exp.Or):
        return _bounded(expression.this) and _bounded(expression.expression)
    if isinstance(expression, exp.Between):
        return isinstance(expression.this, exp.Column) and all(
            isinstance(expression.args.get(key), exp.Literal) for key in ("low", "high")
        )
    if isinstance(expression, exp.EQ):
        return isinstance(expression.this, exp.Column) and isinstance(
            expression.expression, exp.Literal
        )
    return False


def _map_statement(node) -> list[dict]:
    if any(
        isinstance(child, (exp.Command, exp.Execute, exp.Anonymous))
        for child in node.walk()
    ):
        raise ValueError("procedural, dynamic, or unrecognized function call")
    if any(
        isinstance(child, (exp.Insert, exp.Update, exp.Delete))
        for child in node.walk()
        if child is not node
    ):
        raise ValueError("nested data modification requires manual review")
    table = node.find(exp.Table)
    common = {"table": table.sql() if table else ""}
    if isinstance(node, exp.Select):
        if node.args.get("locks") or node.args.get("into"):
            raise ValueError(
                "SELECT with locking or INTO needs explicit classification"
            )
        return [{"type": "select", **common}]
    if isinstance(node, exp.Create):
        kind = node.args.get("kind", "").upper()
        if kind not in {"TABLE", "VIEW", "INDEX", "SCHEMA", "SEQUENCE"}:
            raise ValueError(f"unmapped CREATE {kind}")
        if node.args.get("replace"):
            raise ValueError(
                "CREATE OR REPLACE requires object-specific classification"
            )
        operation = {"type": "create_" + kind.lower(), **common}
        if kind == "INDEX":
            operation["concurrently"] = bool(node.args.get("concurrently"))
        return [operation]
    if isinstance(node, exp.Drop):
        kind = node.args.get("kind", "").upper()
        if kind not in {"TABLE", "VIEW", "INDEX", "SCHEMA", "SEQUENCE"}:
            raise ValueError(f"unmapped DROP {kind}")
        return [{"type": "drop_" + kind.lower(), **common}]
    if isinstance(node, exp.TruncateTable):
        return [{"type": "truncate", **common}]
    if isinstance(node, (exp.Insert, exp.Update, exp.Delete)):
        where = node.args.get("where")
        if isinstance(node, exp.Insert):
            source = node.args.get("expression")
            where = source.args.get("where") if isinstance(source, exp.Select) else None
        bounded = bool(where and _bounded(where.this))
        return [{"type": node.key, "bounded_key": bounded, **common}]
    if isinstance(node, exp.Alter):
        result = []
        for action in node.args.get("actions", []):
            if isinstance(action, exp.ColumnDef):
                constraints = action.args.get("constraints", [])
                nullable = not any(
                    isinstance(c.args.get("kind"), exp.NotNullColumnConstraint)
                    for c in constraints
                )
                volatile = any(
                    isinstance(child, exp.Func)
                    for c in constraints
                    for child in c.walk()
                )
                op = {
                    "type": "add_column",
                    "column": action.name,
                    "definition": {"nullable": nullable},
                    "volatile_default": volatile,
                }
            elif isinstance(action, exp.AlterColumn):
                if action.args.get("dtype"):
                    raise ValueError(
                        "ALTER COLUMN TYPE requires previous type metadata; hint: generate a typed plan from the schema"
                    )
                elif "allow_null" in action.args:
                    op = {
                        "type": "alter_column_nullable",
                        "column": action.name,
                        "nullable": action.args["allow_null"],
                    }
                elif action.args.get("default") is not None or action.args.get("drop"):
                    op = {"type": "alter_column_default", "column": action.name}
                else:
                    raise ValueError("unmapped ALTER COLUMN")
            elif isinstance(action, exp.RenameColumn):
                op = {
                    "type": "rename_column",
                    "column": action.this.name,
                    "new_name": action.args["to"].name,
                }
            elif isinstance(action, exp.AlterRename):
                op = {"type": "rename_table", "new_table": action.this.name}
            elif isinstance(action, exp.Drop) and action.args.get("kind") == "COLUMN":
                op = {"type": "drop_column"}
            else:
                raise ValueError(f"unmapped ALTER action {type(action).__name__}")
            result.append({**op, **common})
        if not result:
            raise ValueError("empty or unrecognized ALTER")
        return result
    raise ValueError(f"unmapped statement {type(node).__name__}")


def classify_sql(content: str, backend: str) -> list[dict]:
    if "/*!" in content:
        raise ValueError("executable MySQL comment")
    if not any(line.strip() == "-- upgrade" for line in content.splitlines()):
        raise ValueError("missing upgrade section")
    dialect = {"postgresql": "postgres", "mariadb": "mysql"}.get(backend, backend)
    operations: list[dict] = []
    for statement in _extract_section_statements(content, "-- upgrade"):
        if "/*!" in statement:
            raise ValueError("executable MySQL comment")
        if backend == "postgresql":
            from pglast.parser import ParseError, parse_sql_json

            try:
                native = json.loads(parse_sql_json(statement))["stmts"]
            except ParseError as exc:
                raise ValueError(f"PostgreSQL parse failed: {exc}") from exc
            mapped = [_map_pg_extra(item["stmt"]) for item in native]
            if native and all(item is not None for item in mapped):
                operations.extend(
                    op for group in mapped if group is not None for op in group
                )
                continue
        try:
            nodes = parse(statement, read=dialect, error_level=ErrorLevel.RAISE)
        except SqlglotError as exc:
            raise ValueError(f"parse failed: {exc}") from exc
        for node in nodes:
            if node is not None:
                operations.extend(_map_statement(node))
    if any(classify_operation(op, backend) == Safety.UNKNOWN for op in operations):
        raise ValueError("operation has no normative classification")
    return operations


def _map_pg_extra(statement: dict) -> list[dict] | None:
    kind, attrs = next(iter(statement.items()))
    if kind == "CreateSchemaStmt" and attrs.get("schemaElts"):
        raise ValueError(
            "CREATE SCHEMA with nested statements requires separate classification"
        )
    if kind == "AlterEnumStmt" and attrs.get("oldVal"):
        raise ValueError("enum value rename requires an explicit migration plan")
    if kind in {
        "DoStmt",
        "ExecuteStmt",
        "CallStmt",
        "CreateFunctionStmt",
        "CreateTrigStmt",
        "RuleStmt",
    }:
        raise ValueError(f"procedural or opaque PostgreSQL statement {kind}")
    kinds = {
        "CreateSchemaStmt": "create_schema",
        "CreateSeqStmt": "create_sequence",
        "AlterSeqStmt": "alter_sequence",
        "CreateEnumStmt": "create_type",
        "AlterEnumStmt": "alter_enum_add_value",
        "CreateExtensionStmt": "create_pg_extension",
        "AlterExtensionStmt": "alter_pg_extension",
        "CreateRoleStmt": "create_role",
        "AlterRoleStmt": "alter_role",
        "DropRoleStmt": "drop_role",
        "CompositeTypeStmt": "create_composite_type",
    }
    if kind in kinds:
        return [{"type": kinds[kind]}]
    if kind == "GrantStmt":
        return [{"type": "add_grant" if attrs.get("is_grant") else "revoke_grant"}]
    if kind == "CommentStmt":
        return [
            {
                "type": "alter_column_comment"
                if attrs.get("objtype") == "OBJECT_COLUMN"
                else "alter_table_comment"
            }
        ]
    return None


def classify_file(path: Path, backend: str) -> tuple[str, str]:
    trusted, _ = read_trusted_plan(path)
    if trusted:
        return trusted["severity"]["file"], "unchanged"
    plan_path = path.with_suffix(".plan.json")
    if plan_path.exists():
        try:
            existing = json.loads(plan_path.read_text(encoding="utf-8"))
        except (ValueError, OSError, UnicodeError):
            return (
                "UNKNOWN",
                "malformed existing plan; hint: review and remove it before reclassification",
            )
        if not isinstance(existing, dict) or not isinstance(
            existing.get("severity", {}), dict
        ):
            return (
                "UNKNOWN",
                "malformed existing plan; hint: review it before reclassification",
            )
        if existing.get("severity", {}).get("provenance") == "generated" or (
            "severity" not in existing and "migration_id" in existing
        ):
            return (
                "UNKNOWN",
                "generated plan cannot be overwritten; hint: regenerate it",
            )
    try:
        content = path.read_text(encoding="utf-8")
        operations = classify_sql(content, backend)
        plan = bind_plan(
            {"migration_id": path.stem, "operations": operations},
            content,
            operations,
            backend,
            provenance="static",
            # Resolve the project key from the plan's own location so the
            # written plan verifies wherever the project root is discoverable.
            project_root=path.parent,
        )
        atomic_write_text(plan_path, json.dumps(plan, sort_keys=True, indent=2) + "\n")
        return plan["severity"]["file"], "classified"
    except (ValueError, OSError, UnicodeError) as exc:
        return "UNKNOWN", str(exc)
