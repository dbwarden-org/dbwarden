from __future__ import annotations

from pathlib import Path

from sqlalchemy import inspect as sa_inspect, text

from dbwarden import __version__
from dbwarden.config import get_database, get_multi_db_config
from dbwarden.engine.core.model_state import model_state_json_dumps, model_state_to_dict
from dbwarden.engine.core.models import ModelColumn, ModelTable
from dbwarden.engine.version import get_migrations_directory
from dbwarden.output import error, info, success, warning
from dbwarden.repositories import get_migration_records


def _build_model_table_from_inspector(inspector, table_name: str) -> ModelTable:
    """Build a ModelTable from a SQLAlchemy inspector by introspecting the sandbox.

    Only captures columns, types, primary keys, foreign keys, indexes, and
    unique constraints. Backend-specific metadata (PG/CH/MySQL) is not
    populated since the sandbox is typically SQLite.
    """
    columns_info = inspector.get_columns(table_name)
    pk_constraint = inspector.get_pk_constraint(table_name)
    pk_columns = set(pk_constraint.get("constrained_columns", []) or [])
    unique_constraints = inspector.get_unique_constraints(table_name)
    indexes_info = inspector.get_indexes(table_name)
    foreign_keys_raw = inspector.get_foreign_keys(table_name)

    unique_single_cols: set[str] = set()
    for uc in unique_constraints:
        cols = uc.get("column_names", [])
        if len(cols) == 1:
            unique_single_cols.update(cols)

    fk_map: dict[str, str] = {}
    for fk in foreign_keys_raw:
        for constrained, referred in zip(
            fk.get("constrained_columns", []),
            fk.get("referred_columns", []) or [None] * len(fk.get("constrained_columns", [])),
        ):
            if referred:
                fk_map[constrained] = f"{fk['referred_table']}.{referred}"

    fk_options_list: list[dict] = []
    for fk in foreign_keys_raw:
        fk_options_list.append({
            "name": fk.get("name", ""),
            "columns": list(fk.get("constrained_columns", [])),
            "referenced_table": fk.get("referred_table", ""),
            "referenced_columns": list(fk.get("referred_columns", [])),
            "on_delete": fk.get("options", {}).get("ondelete", ""),
            "on_update": fk.get("options", {}).get("onupdate", ""),
        })

    columns: list[ModelColumn] = []
    for col in columns_info:
        col_name = col["name"]
        col_type = str(col.get("type", ""))
        autoinc = None
        if isinstance(col.get("type"), object):
            autoinc = getattr(col["type"], "autoincrement", None)
        columns.append(ModelColumn(
            name=col_name,
            type=col_type,
            nullable=bool(col.get("nullable", True)),
            primary_key=col_name in pk_columns,
            unique=col_name in unique_single_cols,
            default=col.get("default"),
            foreign_key=fk_map.get(col_name),
            comment=col.get("comment"),
            autoincrement=autoinc,
        ))

    indexes: list = []
    for idx in indexes_info:
        idx_name = idx.get("name", "")
        if not idx_name:
            continue
        if idx.get("unique") and set(idx.get("column_names", [])) == pk_columns:
            continue
        indexes.append({
            "name": idx_name,
            "columns": list(idx.get("column_names", [])),
            "unique": bool(idx.get("unique", False)),
        })

    uniques: list[dict] = []
    for uc in unique_constraints:
        uq_name = uc.get("name", "")
        cols = uc.get("column_names", [])
        if len(cols) > 1 or (uq_name and not any(
            c["name"] == uq_name for c in columns_info
        )):
            uniques.append({
                "name": uq_name,
                "columns": list(cols),
            })

    table_comment = None
    try:
        tc = inspector.get_table_comment(table_name)
        if tc and tc.get("text"):
            table_comment = tc["text"]
    except Exception:
        pass

    return ModelTable(
        name=table_name,
        columns=columns,
        indexes=indexes,
        foreign_keys=fk_options_list,
        uniques=uniques,
        comment=table_comment,
    )


def _introspect_via_url(sandbox_url: str) -> dict:
    """Introspect the sandbox database and return a model state dict.

    For SQLite ``:memory:`` databases the caller MUST keep the engine alive
    (or use a file-based URL). Each call creates a fresh engine.
    """
    from sqlalchemy import create_engine

    engine = create_engine(sandbox_url, connect_args={"check_same_thread": False})
    try:
        with engine.connect() as connection:
            inspector = sa_inspect(connection)
            table_names = inspector.get_table_names()

            tables: list[ModelTable] = []
            for tname in table_names:
                if tname.startswith("_"):
                    continue
                table = _build_model_table_from_inspector(inspector, tname)
                tables.append(table)

            return model_state_to_dict(tables, dbwarden_version=__version__)
    finally:
        engine.dispose()


def recover_model_state_cmd(database: str | None = None) -> None:
    """Recover a deleted model state file by replaying migrations in a sandbox.

    Reads applied migration records from the live database, replays the SQL
    against an ephemeral sandbox (SQLite or same-engine via testcontainers),
    then writes the reconstructed model state to ``.dbwarden/model_state.*.json``.
    """
    try:
        db_name = database or get_multi_db_config().default
    except Exception:
        db_name = database or "default"

    config = get_database(database)

    migration_records = get_migration_records(database)
    if not migration_records:
        warning("No applied migrations found. Nothing to recover.")
        return

    migrations_dir = get_migrations_directory(database)

    versioned_sqls: list[tuple[str, str, list[str]]] = []
    repeatable_sqls: list[tuple[str, list[str]]] = []

    for record in migration_records:
        filepath = str(Path(migrations_dir) / record.filename)
        if not Path(filepath).exists():
            warning(f"Migration file not found: {record.filename}")
            continue

        from dbwarden.engine.file_parser import parse_upgrade_statements
        statements = parse_upgrade_statements(filepath)

        if record.migration_type in ("runs_always", "runs_on_change"):
            repeatable_sqls.append((record.filename, statements))
        else:
            versioned_sqls.append((record.version or "", record.filename, statements))

    if not versioned_sqls and not repeatable_sqls:
        error("No migration files found on disk to replay.")
        return

    from dbwarden.plugin import HookRegistry
    from dbwarden.engine.sandbox import SQLiteSandboxProvider

    sandbox_url: str | None = None
    sandbox_db_type: str | None = None
    sandbox_provider: SQLiteSandboxProvider | None = None
    if HookRegistry.is_registered("sandbox_provider_start"):
        result = HookRegistry.execute_single("sandbox_provider_start", config.database_type)
        if isinstance(result, tuple) and len(result) == 2:
            sandbox_url, sandbox_db_type = result
            info(f"Using {sandbox_db_type} sandbox via plugin.")
    if sandbox_url is None:
        sandbox_provider = SQLiteSandboxProvider()
        sandbox_url = sandbox_provider.start()
        sandbox_db_type = sandbox_provider.get_database_type()
        if config.database_type != "sqlite":
            warning("Falling back to SQLite sandbox. Some database-specific SQL may fail.")
    warning(f"Sandbox started ({sandbox_db_type}): {sandbox_url}")

    from dbwarden.database.connection import get_db_connection, sandbox_override

    failed = 0
    state: dict | None = None
    with sandbox_override(sandbox_url, sandbox_db_type):
        from dbwarden.repositories import create_migrations_table_if_not_exists, run_migration

        create_migrations_table_if_not_exists(database)

        for version, filename, statements in versioned_sqls:
            try:
                run_migration(
                    sql_statements=statements,
                    version=version,
                    migration_operation="upgrade",
                    filename=filename,
                    db_name=database,
                )
            except Exception as e:
                warning(f"Migration {version} {filename}: {e}")
                failed += 1

        for filename, statements in repeatable_sqls:
            try:
                run_migration(
                    sql_statements=statements,
                    version=None,
                    migration_operation="upgrade",
                    filename=filename,
                    migration_type="runs_always",
                    db_name=database,
                )
            except Exception as e:
                warning(f"Repeatable {filename}: {e}")
                failed += 1

        if versioned_sqls:
            success(f"Replayed {len(versioned_sqls)} versioned and {len(repeatable_sqls)} repeatable migration(s).")
        if failed:
            warning(f"Completed with {failed} warning(s). Model state may be incomplete.")

        from dbwarden.database.connection import get_db_connection as _get_sandbox_conn

        with _get_sandbox_conn(database) as _sandbox_conn:
            _inspector = sa_inspect(_sandbox_conn)
            _table_names = _inspector.get_table_names()
            _tables: list[ModelTable] = []
            for _tname in _table_names:
                if _tname.startswith("_"):
                    continue
                _tables.append(_build_model_table_from_inspector(_inspector, _tname))
            state = model_state_to_dict(_tables, dbwarden_version=__version__)

    if HookRegistry.is_registered("sandbox_provider_stop"):
        HookRegistry.execute_single("sandbox_provider_stop")
    elif sandbox_provider is not None:
        sandbox_provider.stop()
    warning("Sandbox stopped.")

    from dbwarden.commands.make_migrations import get_model_state_path

    state_path = get_model_state_path(db_name)
    legacy_path = get_model_state_path(db_name, legacy=True)
    state_path.parent.mkdir(parents=True, exist_ok=True)
    file_state = dict(state)
    file_state["database"] = db_name
    payload = model_state_json_dumps(file_state)
    if legacy_path != state_path:
        legacy_path.parent.mkdir(parents=True, exist_ok=True)
        from dbwarden.files import atomic_write_text
        atomic_write_text(legacy_path, payload)
    from dbwarden.files import atomic_write_text
    atomic_write_text(state_path, payload)

    table_count = len(state.get("tables", {}))
    success(f"Model state recovered to {state_path} ({table_count} table(s)).")
