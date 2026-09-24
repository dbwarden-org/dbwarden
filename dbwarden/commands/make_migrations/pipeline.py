import json
import os
from pathlib import Path
from typing import Any

from dbwarden.commands.make_migrations.ch_ops import (
    _check_recreate_rename_conflict,
    _resolve_clickhouse_recreate_ops,
)
from dbwarden.commands.make_migrations.migrate_plan import (
    _build_table_rename_ops,
)
from dbwarden.commands.make_migrations.snapshot_merge import (
    _merge_pending_migrations_into_snapshot,
)
from dbwarden.config import ConfigurationError, get_database, get_multi_db_config
from dbwarden.engine.discovery import (
    _qualified_name,
    extract_tables_from_database,
)
from dbwarden.engine.file_parser import parse_upgrade_statements
from dbwarden.engine.migration_name import Change
from dbwarden.logging import get_logger
from dbwarden.output import warning

logger = get_logger()


def get_pending_migration_statements(migrations_dir: str) -> set[str]:
    """Get all SQL statements from all migration files (for deduplication)."""
    all_statements: set[str] = set()

    if not os.path.exists(migrations_dir):
        return all_statements

    for filename in os.listdir(migrations_dir):
        if not filename.endswith(".sql"):
            continue
        filepath = os.path.join(migrations_dir, filename)
        from dbwarden.merge.marker import is_superseded
        from dbwarden.merge.reconciliation import is_reconciliation
        if is_superseded(filepath) or is_reconciliation(filepath):
            continue
        statements = parse_upgrade_statements(filepath)
        for stmt in statements:
            normalized = stmt.strip()
            if normalized:
                all_statements.add(normalized)

    return all_statements


def get_model_state_path(db_name: str | None = None, legacy: bool = False) -> Path:
    base = Path(".dbwarden")
    if legacy:
        return base / "model_state.json"
    return base / f"model_state.{db_name or 'default'}.json"


def get_current_model_state_path(db_name: str | None = None) -> Path:
    state_path = get_model_state_path(db_name)
    legacy_state_path = get_model_state_path(db_name, legacy=True)
    if state_path.exists():
        if legacy_state_path.exists() and _legacy_state_should_override(db_name, state_path, legacy_state_path):
            return legacy_state_path
        return state_path
    if legacy_state_path.exists():
        return legacy_state_path
    return state_path


def _legacy_state_should_override(
    db_name: str | None,
    state_path: Path,
    legacy_state_path: Path,
) -> bool:
    try:
        legacy_state = json.loads(legacy_state_path.read_text())
    except (json.JSONDecodeError, OSError):
        return False

    legacy_db = legacy_state.get("database")
    if legacy_db:
        return legacy_db == (db_name or "default")

    try:
        return legacy_state_path.stat().st_mtime >= state_path.stat().st_mtime
    except OSError:
        return False


def _run_offline_migrations(
    description: str | None = None, database: str | None = None, migration_type: str = "versioned",
    clickhouse_engine_recreate: bool = False, drop_preserved_clickhouse_table: bool | None = None,
    rename_flags: list[str] | None = None, rename_table_flags: list[str] | None = None,
) -> None:
    from dbwarden.commands.make_migrations.generation import run_generation

    run_generation(description=description, database=database, migration_type=migration_type,
                   clickhouse_engine_recreate=clickhouse_engine_recreate,
                   drop_preserved_clickhouse_table=drop_preserved_clickhouse_table,
                   rename_flags=rename_flags, rename_table_flags=rename_table_flags, offline=True)


# Kept for backward compatibility. Referenced by tests.
def _build_domain_sql(domain: dict) -> str:
    schema = domain.get("schema")
    name = domain["name"]
    qname = _qualified_name(name, schema)
    domain_type = domain.get("type", "text")
    parts = [f"CREATE DOMAIN {qname} AS {domain_type}"]
    if domain.get("default"):
        parts.append(f"DEFAULT {domain['default']}")
    if domain.get("not_null"):
        parts.append("NOT NULL")
    if domain.get("check"):
        parts.append(f"CHECK ({domain['check']})")
    return " ".join(parts) + ";"


def _build_sequence_sql(seq: dict) -> str:
    schema = seq.get("schema")
    name = seq["name"]
    qname = _qualified_name(name, schema)
    parts = [f"CREATE SEQUENCE IF NOT EXISTS {qname}"]
    if seq.get("increment") is not None:
        parts.append(f"INCREMENT BY {seq['increment']}")
    if seq.get("minvalue") is not None:
        parts.append(f"MINVALUE {seq['minvalue']}")
    if seq.get("maxvalue") is not None:
        parts.append(f"MAXVALUE {seq['maxvalue']}")
    if seq.get("start") is not None:
        parts.append(f"START WITH {seq['start']}")
    if seq.get("cycle"):
        parts.append("CYCLE")
    else:
        parts.append("NO CYCLE")
    if seq.get("owned_by"):
        parts.append(f"OWNED BY {seq['owned_by']}")
    return " ".join(parts) + ";"


def _drop_domain_sql(domain: dict) -> str:
    schema = domain.get("schema")
    name = domain["name"]
    qname = _qualified_name(name, schema)
    return f"DROP DOMAIN IF EXISTS {qname};"


def _drop_sequence_sql(seq: dict) -> str:
    schema = seq.get("schema")
    name = seq["name"]
    qname = _qualified_name(name, schema)
    return f"DROP SEQUENCE IF EXISTS {qname};"


# The object kinds the PostgreSQL preamble handlers read out of a snapshot.
# Listing them keeps a handler from seeing a missing key when the snapshot came
# from an older dbwarden version or a partial extraction.
_EMPTY_PREAMBLE_SNAPSHOT: dict[str, Any] = {
    "domains": {},
    "sequences": {},
    "functions": {},
    "tables": {},
    "roles": {},
    "default_privileges": {},
    "composite_types": {},
    "extended_stats": {},
    "event_triggers": {},
    "pg_extensions": {},
}


def _prepend_pg_preamble(
    upgrade_sql: str,
    rollback_sql: str,
    changes: list[Change],
    database: str | None,
    snapshot: dict[str, Any] | None = None,
) -> tuple[str, str, list[Change]]:
    """Prepend PostgreSQL preamble SQL (extensions, domains, sequences) to upgrade/rollback SQL.

    ``snapshot`` is the schema snapshot the caller diffed against, when it has
    one. Preamble objects are declared in configuration rather than in models,
    so they are diffed here; without the snapshot they would be compared
    against an empty state and recreated on every run.
    """
    try:
        mc = get_multi_db_config()
        db_name = database or mc.default
        config = get_database(db_name)
        if config.database_type != "postgresql":
            return upgrade_sql, rollback_sql, changes

        from dbwarden.engine.core.registry import RegistryDriver
        from dbwarden.plugin import ObjectPluginRegistry

        has_pg_preamble = any(getattr(config, attr, None) for attr in (
            "pg_sequences", "pg_domains", "pg_functions", "pg_triggers",
            "pg_roles", "pg_default_privileges", "pg_composite_types",
            "pg_extended_statistics", "pg_event_triggers",
        ))
        has_pg_extension = ObjectPluginRegistry.has_handler("pg_extension") and getattr(config, "pg_extensions", None)

        if has_pg_preamble or has_pg_extension:
            _reg = RegistryDriver()
            # Diff the preamble against the database, not against nothing. With
            # an empty snapshot every declared role, domain, sequence, function
            # and trigger reads as new, so each generation emits CREATE for
            # objects that already exist: the second `migrate` fails with
            # "already exists", and an attribute change never becomes an ALTER.
            # The empty shape is still the right input when there is no
            # snapshot - offline generation cannot see the server, and creating
            # is then the only honest instruction.
            _preamble_snapshot = dict(_EMPTY_PREAMBLE_SNAPSHOT)
            if isinstance(snapshot, dict):
                _preamble_snapshot.update(snapshot)
            _up_ops, _rb_ops = _reg.run(_preamble_snapshot, [], config)
            if _up_ops:
                _stmts = _reg.emit_all(_up_ops, db_name=db_name)
                _pg_up = "\n".join(s.upgrade_sql for s in _stmts)
                _pg_rb = "\n".join(s.rollback_sql for s in reversed(_stmts))
                if _pg_up.strip():
                    if upgrade_sql.strip():
                        upgrade_sql = _pg_up + "\n\n" + upgrade_sql
                    else:
                        upgrade_sql = _pg_up
                if _pg_rb.strip():
                    if rollback_sql.strip():
                        rollback_sql = _pg_rb + "\n\n" + rollback_sql
                    else:
                        rollback_sql = _pg_rb
                for op in reversed(_up_ops):
                    _name = (
                        op.upgrade_attrs.get("domain_name")
                        or op.upgrade_attrs.get("seq_name")
                        or op.upgrade_attrs.get("function_name")
                        or op.upgrade_attrs.get("role_name")
                        or op.upgrade_attrs.get("type_name")
                        or op.upgrade_attrs.get("name")
                        or ""
                    )
                    _operation = "create_extension" if op.object_type == "create_pg_extension" else op.object_type
                    changes.insert(0, Change(operation=_operation, table=_name))
    except Exception:
        logger.exception("Failed to prepend PostgreSQL preamble; preamble objects omitted")

    return upgrade_sql, rollback_sql, changes


def _prepend_ch_preamble(
    upgrade_sql: str,
    rollback_sql: str,
    changes: list[Change],
    database: str | None,
) -> tuple[str, str, list[Change]]:
    """Prepend ClickHouse preamble SQL (named collections, RBAC) to upgrade/rollback SQL."""
    try:
        mc = get_multi_db_config()
        db_name = database or mc.default
        config = get_database(db_name)
        if config.database_type != "clickhouse":
            return upgrade_sql, rollback_sql, changes

        has_ch_preamble = any(getattr(config, attr, None) for attr in (
            "ch_named_collections", "ch_roles", "ch_users", "ch_quotas",
            "ch_row_policies", "ch_settings_profiles", "ch_grants",
        ))
        if has_ch_preamble:
            from dbwarden.engine.core.registry import RegistryDriver

            _reg = RegistryDriver()
            _up_ops, _rb_ops = _reg.run({"named_collections": {}, "settings_profiles": {}, "roles": {}, "users": {}, "quotas": {}, "row_policies": {}, "grants": {}}, [], config)
            if _up_ops:
                _stmts = _reg.emit_all(_up_ops, db_name=db_name)
                _ch_up = "\n".join(s.upgrade_sql for s in _stmts)
                _ch_rb = "\n".join(s.rollback_sql for s in reversed(_stmts))
                if _ch_up.strip():
                    if upgrade_sql.strip():
                        upgrade_sql = _ch_up + "\n\n" + upgrade_sql
                    else:
                        upgrade_sql = _ch_up
                if _ch_rb.strip():
                    if rollback_sql.strip():
                        rollback_sql = _ch_rb + "\n\n" + rollback_sql
                    else:
                        rollback_sql = _ch_rb
                for op in reversed(_up_ops):
                    changes.insert(0, Change(
                        operation=op.object_type,
                        table=op.upgrade_attrs.get("name", ""),
                    ))
    except Exception:
        logger.exception("Failed to prepend ClickHouse preamble; preamble objects omitted")

    return upgrade_sql, rollback_sql, changes


def _order_tables_for_creation(tables: list[Any]) -> list[Any]:
    """Create referenced tables before tables that add foreign keys to them."""
    by_name = {table.name: table for table in tables}
    ordered: list[Any] = []
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(table: Any) -> None:
        if table.name in visited:
            return
        if table.name in visiting:
            return
        visiting.add(table.name)
        for foreign_key in table.foreign_keys:
            referenced = foreign_key.get("referred_table")
            if referenced in by_name:
                visit(by_name[referenced])
        visiting.remove(table.name)
        visited.add(table.name)
        ordered.append(table)

    for table in tables:
        visit(table)
    return ordered


def generate_migration_sql(
    tables: list,
    migrations_dir: str | None = None,
    database: str | None = None,
    db_name: str | None = None,
    confirmed_renames: set[tuple[str, str, str]] | None = None,
    resolved_from_map: dict[tuple[str, str, str], str] | None = None,
    safe_type_change: bool = False,
    confirmed_table_intents: set[tuple[str, str]] | None = None,
    table_resolved_from_map: dict[tuple[str, str], str] | None = None,
    concurrent: bool = True,
    clickhouse_engine_recreate: bool = False,
    drop_preserved_clickhouse_table: bool | None = None,
    postgres_auto_using: bool = False,
) -> tuple[str, str, list[Change]]:
    from dbwarden.engine.generation_state import effective_state
    from dbwarden.engine.safety.plans import read_trusted_plan
    from dbwarden.engine.snapshot import (
        _apply_rename_intents,
        diff_models_against_snapshot,
        extract_full_schema_snapshot,
        find_latest_snapshot,
        snapshot_diff_to_sql,
    )
    from dbwarden.engine.version import get_migration_filepaths_by_version

    snapshot = find_latest_snapshot(db_name)
    incomplete = False
    if snapshot is None:
        config = None
        try:
            config = get_database(database)
            snapshot = extract_full_schema_snapshot(
                database=db_name, sqlalchemy_url=config.sqlalchemy_url,
                database_type=config.database_type,
            )
        except (OSError, ValueError, ConfigurationError):
            incomplete = True
            warning("No snapshot available; compatibility preview uses column names only. Existing column attributes cannot be compared. Review SQL before applying.")
            try:
                names = extract_tables_from_database(config.sqlalchemy_url) if config else {}
            except ConfigurationError:
                names = {}
            snapshot = {"tables": {name: {"columns": {column: {"type": "unknown"} for column in columns}} for name, columns in names.items()}}
    if migrations_dir:
        files = get_migration_filepaths_by_version(migrations_dir)
        if any(read_trusted_plan(path)[0] for path in files.values()):
            snapshot, _ = effective_state(snapshot, files)
        else:
            _merge_pending_migrations_into_snapshot(snapshot, migrations_dir)
    for table in tables:
        columns = snapshot.get("tables", {}).get(table.name, {}).get("columns", {})
        for column in table.columns:
            if columns.get(column.name, {}).get("type") == "unknown":
                columns[column.name] = column.to_dict()
    upgrade_ops, rollback_ops = diff_models_against_snapshot(
        tables, snapshot, database=database, db_name=db_name,
        clickhouse_engine_recreate=clickhouse_engine_recreate,
    )
    if incomplete:
        existing = set(snapshot.get("tables", {}))
        constraints = {"add_foreign_key", "add_unique_constraint", "add_check_constraint", "add_exclude_constraint"}
        upgrade_ops = [op for op in upgrade_ops if not (op.get("table") in existing and op["type"] in constraints)]
    if confirmed_renames:
        upgrade_ops, rollback_ops = _apply_rename_intents(upgrade_ops, rollback_ops, confirmed_renames, resolved_from_map)
    _check_recreate_rename_conflict(upgrade_ops, confirmed_table_intents or set())
    renames = _build_table_rename_ops(confirmed_table_intents or set(), table_resolved_from_map)
    upgrade_ops = renames["upgrade"] + upgrade_ops
    rollback_ops = renames["rollback"] + rollback_ops
    from dbwarden.engine.model_discovery.type_mapping import _get_backend_name
    if _get_backend_name(db_name) == "sqlite":
        created = {op["table"] for op in upgrade_ops if op["type"] == "create_table"}
        inline = {"add_foreign_key", "add_unique_constraint", "add_check_constraint"}
        upgrade_ops = [op for op in upgrade_ops if not (op["type"] in inline and op.get("table") in created)]
    ranks = {table.name: index for index, table in enumerate(_order_tables_for_creation(tables))}
    upgrade_ops.sort(key=lambda op: ranks.get(op.get("table"), len(ranks)) if op["type"] == "create_table" else len(ranks))
    _resolve_clickhouse_recreate_ops(upgrade_ops, rollback_ops, clickhouse_engine_recreate, drop_preserved_clickhouse_table)
    upgrade, rollback, changes = snapshot_diff_to_sql(
        upgrade_ops, rollback_ops, database=database, db_name=db_name,
        safe_type_change=safe_type_change, concurrent=concurrent,
        postgres_auto_using=postgres_auto_using, enforce_rollback_contract=True,
    )
    upgrade, rollback, changes = _prepend_pg_preamble(upgrade, rollback, changes, database, snapshot)
    return _prepend_ch_preamble(upgrade, rollback, changes, database)
