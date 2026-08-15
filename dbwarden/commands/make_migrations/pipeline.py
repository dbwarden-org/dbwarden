import json
import os
import re
import sys
from pathlib import Path
from typing import Any

from dbwarden import __version__
from dbwarden.config import get_database, get_multi_db_config
from dbwarden.constants import RUNS_ALWAYS_FILE_PREFIX, RUNS_ON_CHANGE_FILE_PREFIX
from dbwarden.engine.file_parser import parse_upgrade_statements
from dbwarden.engine.backends.postgresql.render import _quote_pg
from dbwarden.files import atomic_write_text
from dbwarden.engine.discovery import (
    get_all_model_tables,
    auto_discover_model_paths,
    filter_model_tables_by_name,
    validate_model_tables_exist,
    generate_create_table_sql,
    generate_drop_object_sql,
    generate_add_column_sql,
    extract_tables_from_migrations,
    extract_tables_from_database,
    _extract_create_table_columns,
    _qualified_name,
)
from dbwarden.engine.discovery import _get_backend_name as _get_backend_name_md
from dbwarden.engine.migration_name import Change
from dbwarden.engine.offline import model_state_to_dict
from dbwarden.engine.version import (
    get_migrations_directory,
    get_next_migration_number,
    generate_migration_filename,
    generate_repeatable_filename,
)
from dbwarden.logging import get_logger
from dbwarden.output import error, info, success, warning
from dbwarden.commands.make_migrations.ch_ops import (
    _check_recreate_rename_conflict,
    _resolve_clickhouse_recreate_ops,
)
from dbwarden.commands.make_migrations.cli_parsing import (
    RenameIntent,
    _parse_rename_flags,
    _parse_rename_table_flags,
)
from dbwarden.commands.make_migrations.migrate_plan import (
    _build_table_rename_ops,
    _check_migration_scope,
    build_migration_plan,
)
from dbwarden.commands.make_migrations.snapshot_merge import _merge_pending_migrations_into_snapshot


logger = get_logger()


def get_pending_migration_statements(migrations_dir: str) -> set[str]:
    """Get all SQL statements from all migration files (for deduplication)."""
    all_statements = set()

    if not os.path.exists(migrations_dir):
        return all_statements

    for filename in os.listdir(migrations_dir):
        if not filename.endswith(".sql"):
            continue
        filepath = os.path.join(migrations_dir, filename)
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
    clickhouse_engine_recreate: bool = False,
    drop_preserved_clickhouse_table: bool | None = None,
    rename_flags: list[str] | None = None,
    rename_table_flags: list[str] | None = None,
) -> None:
    from dbwarden.engine.offline import diff_model_states, model_state_to_dict, normalize_model_state
    from dbwarden.engine.snapshot import snapshot_diff_to_sql, _apply_rename_intents

    config = get_database(database)
    multi_config = get_multi_db_config()
    db_name = database or multi_config.default
    model_paths = config.model_paths

    if not model_paths:
        model_paths = auto_discover_model_paths()

    if not model_paths:
        logger.warning("No model paths found. Please set model_paths in dbwarden config")
        warning(
            "No SQLAlchemy models found. Please:\n"
            "1. Create models/ directory with your SQLAlchemy models\n"
            "2. Or set model_paths in dbwarden config"
        )
        return

    state_path = get_model_state_path(db_name)
    legacy_state_path = get_model_state_path(db_name, legacy=True)
    read_state_path = get_current_model_state_path(db_name)

    if not read_state_path.exists():
        error(f"Error: {get_model_state_path(db_name)} not found.")
        warning("Run 'dbwarden export-models' first to establish a baseline.")
        return

    try:
        prev_state = normalize_model_state(json.loads(read_state_path.read_text()))
    except json.JSONDecodeError:
        error(f"Error: {read_state_path} contains invalid JSON.")
        warning("Run 'dbwarden export-models' again to regenerate the state file.")
        return
    from dbwarden import __version__ as _dw_version

    try:
        _migrations_dir = get_migrations_directory(database)
    except Exception:
        _migrations_dir = str(Path.cwd() / config.migrations_dir)
    if not list(Path(_migrations_dir).glob("*.sql")):
        prev_state["tables"] = {}
        prev_state["indexes"] = {}
        prev_state["constraints"] = {}
        prev_state["enums"] = {}

    current_tables = get_all_model_tables(model_paths, db_name=db_name)
    validate_model_tables_exist(current_tables, config.model_tables, db_name)
    current_tables = filter_model_tables_by_name(current_tables, config.model_tables)
    current_state = model_state_to_dict(current_tables, dbwarden_version=_dw_version)

    upgrade_ops, rollback_ops = diff_model_states(prev_state, current_state)

    rename_intents: list[RenameIntent] = []
    if rename_flags:
        rename_intents = _parse_rename_flags(rename_flags)

    confirmed_renames: set[tuple[str, str, str]] = set()
    resolved_from_map: dict[tuple[str, str, str], str] = {}

    for intent in rename_intents:
        key = (intent.table, intent.old_name, intent.new_name)
        confirmed_renames.add(key)
        resolved_from_map[key] = "rename_flag"

    if confirmed_renames:
        upgrade_ops, rollback_ops = _apply_rename_intents(
            upgrade_ops, rollback_ops, confirmed_renames, resolved_from_map
        )

    confirmed_table_intents: set[tuple[str, str]] = set()
    table_resolved_from_map: dict[tuple[str, str], str] = {}

    if rename_table_flags:
        table_intents = _parse_rename_table_flags(rename_table_flags)
        for intent in table_intents:
            key = (intent["old_table"], intent["new_table"])
            confirmed_table_intents.add(key)
            table_resolved_from_map[key] = "rename_flag"

    _check_recreate_rename_conflict(upgrade_ops, confirmed_table_intents)

    if confirmed_table_intents:
        table_rename_ops = _build_table_rename_ops(confirmed_table_intents, table_resolved_from_map)
        upgrade_ops = table_rename_ops["upgrade"] + upgrade_ops
        rollback_ops = table_rename_ops["rollback"] + rollback_ops

    _resolve_clickhouse_recreate_ops(
        upgrade_ops,
        rollback_ops,
        clickhouse_engine_recreate=clickhouse_engine_recreate,
        drop_preserved_clickhouse_table=drop_preserved_clickhouse_table,
    )

    if not upgrade_ops:
        info("No offline schema changes detected between model state and current models.")
        return

    from dbwarden.engine.version import get_migration_filepaths_by_version

    upgrade_sql, rollback_sql, changes = snapshot_diff_to_sql(
        upgrade_ops, rollback_ops, database=database, db_name=db_name,
        enforce_rollback_contract=True,
    )

    upgrade_sql, rollback_sql, changes = _prepend_pg_preamble(
        upgrade_sql, rollback_sql, changes, database,
    )

    upgrade_sql, rollback_sql, changes = _prepend_ch_preamble(
        upgrade_sql, rollback_sql, changes, database,
    )

    if not upgrade_sql.strip():
        info("No offline schema changes detected between model state and current models.")
        return

    from dbwarden.engine.version import get_migrations_directory as _get_migrations_dir
    try:
        migrations_dir = _get_migrations_dir(database)
    except Exception:
        config = get_database(database)
        migrations_dir = str(Path.cwd() / config.migrations_dir)
        Path(migrations_dir).mkdir(parents=True, exist_ok=True)
    existing_statements = get_pending_migration_statements(migrations_dir)

    filtered_statements = []
    filtered_rollback = []
    for u_sql, r_sql in zip(upgrade_sql.strip().split("\n\n"), rollback_sql.strip().split("\n\n")):
        if u_sql.strip() and u_sql.strip() not in existing_statements:
            filtered_statements.append(u_sql.strip())
            filtered_rollback.append(r_sql.strip())

    if not filtered_statements:
        info("No new migrations to generate - all models already covered by existing migrations.")
        return

    safe_desc = re.sub(r"[^a-zA-Z0-9]", "_", (description or "offline_migration")).lower()
    safe_desc = re.sub(r"_+", "_", safe_desc).strip("_")[:60]
    if not safe_desc:
        safe_desc = "offline_migration"
    if migration_type in ("runs_always", "ra"):
        filename = generate_repeatable_filename(db_name, safe_desc, RUNS_ALWAYS_FILE_PREFIX)
    elif migration_type in ("runs_on_change", "roc"):
        filename = generate_repeatable_filename(db_name, safe_desc, RUNS_ON_CHANGE_FILE_PREFIX)
    else:
        existing_versions = get_migration_filepaths_by_version(migrations_dir)
        next_version = f"{int(max(existing_versions.keys(), default='0000')) + 1:04d}"
        filename = generate_migration_filename(db_name, safe_desc, next_version)

    header = "-- upgrade\n\n"
    header += "\n\n".join(filtered_statements)
    header += "\n\n-- rollback\n\n"
    header += "\n\n".join(filtered_rollback)
    header += "\n"

    filepath = os.path.join(migrations_dir, filename)
    with open(filepath, "w") as f:
        f.write(header)

    migration_id = Path(filename).stem
    plan = build_migration_plan(migration_id, changes, "\n\n".join(filtered_statements))
    plan_path = filepath.replace(".sql", ".plan.json")
    atomic_write_text(Path(plan_path), json.dumps(plan, indent=2, default=str) + "\n")

    success(f"Created migration file: {filepath}")
    success(f"Created migration plan: {plan_path}")
    info(f"Tables included: {', '.join(sorted(set(c.table for c in changes if hasattr(c, 'table') and c.table)))}")

    from dbwarden.engine.core.model_state import model_state_json_dumps
    file_state = dict(current_state)
    file_state["database"] = db_name or "default"
    state_payload = model_state_json_dumps(file_state)
    state_path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(state_path, state_payload)
    if legacy_state_path != state_path:
        legacy_state_path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(legacy_state_path, state_payload)
    logger.info(f"Updated model state: {state_path}")


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


def _prepend_pg_preamble(
    upgrade_sql: str,
    rollback_sql: str,
    changes: list[Change],
    database: str | None,
) -> tuple[str, str, list[Change]]:
    """Prepend PostgreSQL preamble SQL (extensions, domains, sequences) to upgrade/rollback SQL."""
    try:
        mc = get_multi_db_config()
        db_name = database or mc.default
        config = get_database(db_name)
        if config.database_type != "postgresql":
            return upgrade_sql, rollback_sql, changes

        from dbwarden.plugin import ObjectPluginRegistry
        from dbwarden.engine.core.registry import RegistryDriver

        has_pg_preamble = any(getattr(config, attr, None) for attr in (
            "pg_sequences", "pg_domains", "pg_functions", "pg_triggers",
            "pg_roles", "pg_default_privileges", "pg_composite_types",
            "pg_extended_statistics", "pg_event_triggers",
        ))
        has_pg_extension = ObjectPluginRegistry.has_handler("pg_extension") and getattr(config, "pg_extensions", None)

        if has_pg_preamble or has_pg_extension:
            _reg = RegistryDriver()
            _up_ops, _rb_ops = _reg.run({"domains": {}, "sequences": {}, "functions": {}, "tables": {}, "roles": {}, "default_privileges": {}, "composite_types": {}, "extended_stats": {}, "event_triggers": {}, "pg_extensions": {}}, [], config)
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
    upgrade_sql: str = ""
    rollback_sql: str = ""
    changes: list[Change] = []
    snapshot: Any = None
    confirmed_renames = confirmed_renames or set()
    confirmed_table_intents = confirmed_table_intents or set()

    try:
        from dbwarden.engine.snapshot import (
            RollbackContractError,
            find_latest_snapshot,
            extract_full_schema_snapshot,
            diff_models_against_snapshot,
            snapshot_diff_to_sql,
            _apply_rename_intents,
            _rename_table_sql,
            TableRenameIntent,
            StatementOrder,
            MigrationStatement,
        )

        snapshot = find_latest_snapshot(db_name)
    except Exception:
        snapshot = None

    if snapshot is None:
        try:
            config = get_database(database)
            snapshot = extract_full_schema_snapshot(
                database=db_name,
                sqlalchemy_url=config.sqlalchemy_url,
                database_type=config.database_type,
            )
        except Exception:
            snapshot = None

    if snapshot is not None and migrations_dir:
        _merge_pending_migrations_into_snapshot(snapshot, migrations_dir)

    if snapshot is not None:
        try:
            upgrade_ops, rollback_ops = diff_models_against_snapshot(
                tables, snapshot, database=database, db_name=db_name,
                clickhouse_engine_recreate=clickhouse_engine_recreate,
            )
            if confirmed_renames:
                upgrade_ops, rollback_ops = _apply_rename_intents(
                    upgrade_ops, rollback_ops, confirmed_renames, resolved_from_map
                )

            _check_recreate_rename_conflict(upgrade_ops, confirmed_table_intents)

            for warn in _check_migration_scope(upgrade_ops, database=database):
                logger.warning(warn)

            table_rename_ops = _build_table_rename_ops(confirmed_table_intents, table_resolved_from_map)
            upgrade_ops = table_rename_ops["upgrade"] + upgrade_ops
            rollback_ops = table_rename_ops["rollback"] + rollback_ops
            _resolve_clickhouse_recreate_ops(
                upgrade_ops,
                rollback_ops,
                clickhouse_engine_recreate=clickhouse_engine_recreate,
                drop_preserved_clickhouse_table=drop_preserved_clickhouse_table,
            )

            upgrade_sql, rollback_sql, changes = snapshot_diff_to_sql(
                upgrade_ops, rollback_ops, database=database, db_name=db_name,
                safe_type_change=safe_type_change,
                concurrent=concurrent,
                postgres_auto_using=postgres_auto_using,
                enforce_rollback_contract=True,
            )
        except RollbackContractError:
            raise
        except Exception:
            snapshot = None

    if snapshot is None:
        table_rename_ops = _build_table_rename_ops(confirmed_table_intents, table_resolved_from_map)
        try:
            config = get_database(database)
            existing_tables = extract_tables_from_database(config.sqlalchemy_url)
        except Exception:
            existing_tables = {}

        migration_tables = {}
        if migrations_dir:
            migration_tables = extract_tables_from_migrations(migrations_dir)

        known_tables: dict[str, set[str]] = {
            table_name: {col.lower() for col in columns}
            for table_name, columns in existing_tables.items()
        }
        for table_name, columns in migration_tables.items():
            if table_name in known_tables:
                known_tables[table_name].update({col.lower() for col in columns})
            else:
                known_tables[table_name] = {col.lower() for col in columns}

        upgrade_parts: list[str] = []
        rollback_parts: list[str] = []
        changes: list[Change] = []
        backend = _get_backend_name_md(database)

        tables = _order_tables_for_creation(tables)
        enum_types: dict[str, list[str]] = {}
        for table in tables:
            for col in table.columns:
                pg_type = col.pg_meta.get("pg_type", {})
                if pg_type.get("kind") == "enum":
                    type_name = pg_type.get("type_name", "")
                    if type_name and type_name not in enum_types:
                        enum_types[type_name] = pg_type.get("values", [])
        for enum_name, values in enum_types.items():
            values_sql = ", ".join(repr(v) for v in values)
            upgrade_parts.append(f"CREATE TYPE {enum_name} AS ENUM ({values_sql});")
            rollback_parts.append(f"DROP TYPE IF EXISTS {enum_name};")
            changes.append(Change(operation="create_type", table=enum_name))

        created_tables: set[str] = set()
        for table in tables:
            existing_columns = known_tables.get(table.name, set())

            if not existing_columns:
                created_tables.add(table.name)
                create_sql = generate_create_table_sql(table, db_name)
                upgrade_parts.append(create_sql)
                rollback_parts.append(generate_drop_object_sql(table))
                changes.append(Change(operation="create_table", table=table.name))
            else:
                for column in table.columns:
                    if column.name.lower() not in existing_columns:
                        alter_sql = generate_add_column_sql(
                            table.name, column, db_name, schema=table.schema
                        )
                        upgrade_parts.append(alter_sql)
                        if backend == "postgresql":
                            qtable = _quote_pg(table.name)
                            qschema = _quote_pg(table.schema) if table.schema else None
                            col_name_q = _quote_pg(column.name)
                        else:
                            qtable = table.name
                            qschema = table.schema
                            col_name_q = column.name
                        qname = _qualified_name(qtable, qschema)
                        rollback_parts.append(
                            f"ALTER TABLE {qname} DROP COLUMN {col_name_q}"
                        )
                        changes.append(Change(operation="add_column", table=table.name, target=column.name))
                        known_tables.setdefault(table.name, set()).add(column.name.lower())

        for table in tables:
            if table.name not in created_tables:
                continue
            if backend == "postgresql":
                qtable = _quote_pg(table.name)
                qschema = _quote_pg(table.schema) if table.schema else None
            else:
                qtable = table.name
                qschema = table.schema
            qname = _qualified_name(qtable, qschema)
            for idx in table.indexes:
                idx_cols = idx.expression or ", ".join(idx.columns)
                if not idx_cols:
                    continue
                if backend == "clickhouse":
                    ch_type = idx.clickhouse_type or "minmax"
                    ch_granularity = idx.clickhouse_granularity or 1
                    upgrade_parts.append(
                        f"ALTER TABLE {qname} ADD INDEX IF NOT EXISTS {idx.name} "
                        f"({idx_cols}) "
                        f"TYPE {ch_type} "
                        f"GRANULARITY {ch_granularity};"
                    )
                    rollback_parts.append(
                        f"ALTER TABLE {qname} DROP INDEX {idx.name};"
                    )
                else:
                    upgrade_parts.append(f"CREATE INDEX IF NOT EXISTS {idx.name} ON {qname} ({idx_cols});")
                    rollback_parts.append(f"DROP INDEX IF EXISTS {idx.name};")
                changes.append(Change(operation="add_index", table=table.name, target=idx.name))
            for fk in table.foreign_keys:
                local_cols = ", ".join(fk.get("columns", []))
                ref_table = _quote_pg(fk.get("referred_table", "")) if backend == "postgresql" else fk.get("referred_table", "")
                ref_cols = ", ".join(_quote_pg(c) if backend == "postgresql" else c for c in fk.get("referred_columns", ["id"]))
                loc_cols_q = ", ".join(_quote_pg(c) if backend == "postgresql" else c for c in fk.get("columns", []))
                fk_sql = f"ALTER TABLE {qname} ADD FOREIGN KEY ({loc_cols_q}) REFERENCES {ref_table} ({ref_cols})"
                on_delete = fk.get("on_delete", "NO ACTION")
                on_update = fk.get("on_update", "NO ACTION")
                if on_delete != "NO ACTION":
                    fk_sql += f" ON DELETE {on_delete}"
                if on_update != "NO ACTION":
                    fk_sql += f" ON UPDATE {on_update}"
                fk_sql += ";"
                upgrade_parts.append(fk_sql)
                rollback_parts.append(f"-- DROP FOREIGN KEY ({local_cols}) on {table.name} (name unknown)")
                changes.append(Change(operation="add_foreign_key", table=table.name, target=local_cols))

        rollback_parts.reverse()

        if migrations_dir:
            existing_statements = get_pending_migration_statements(migrations_dir)
            filtered_upgrade_parts = []
            filtered_rollback_parts = []
            filtered_changes = []

            for upgrade_sql, rollback_sql in zip(upgrade_parts, rollback_parts):
                if upgrade_sql.strip() in existing_statements:
                    continue
                filtered_upgrade_parts.append(upgrade_sql)
                filtered_rollback_parts.append(rollback_sql)

            for change, upgrade_sql in zip(changes, upgrade_parts):
                if upgrade_sql.strip() in existing_statements:
                    continue
                filtered_changes.append(change)

            upgrade_parts = filtered_upgrade_parts
            rollback_parts = filtered_rollback_parts
            changes = filtered_changes

        upgrade_sql = "\n\n".join(upgrade_parts)
        rollback_sql = "\n\n".join(rollback_parts)

        if table_rename_ops["upgrade"]:
            from dbwarden.engine.snapshot import snapshot_diff_to_sql
            rename_upgrade, rename_rollback, rename_changes = snapshot_diff_to_sql(
                table_rename_ops["upgrade"], table_rename_ops["rollback"],
                database=database, db_name=db_name,
            )
            if rename_upgrade.strip():
                upgrade_sql = rename_upgrade + "\n\n" + upgrade_sql
                rollback_sql = rename_rollback + "\n\n" + rollback_sql
                changes = rename_changes + changes

    if migrations_dir:
        existing_statements = get_pending_migration_statements(migrations_dir)
        if upgrade_sql.strip():
            from dbwarden.engine.snapshot import _filter_duplicates_from_snapshot_diff
            upgrade_sql, rollback_sql, changes = _filter_duplicates_from_snapshot_diff(
                upgrade_sql, rollback_sql, changes, existing_statements
            )

    upgrade_sql, rollback_sql, changes = _prepend_pg_preamble(
        upgrade_sql, rollback_sql, changes, database,
    )

    upgrade_sql, rollback_sql, changes = _prepend_ch_preamble(
        upgrade_sql, rollback_sql, changes, database,
    )

    return upgrade_sql, rollback_sql, changes
