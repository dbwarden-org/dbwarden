from __future__ import annotations

from typing import Any

from dbwarden.engine.model_discovery.type_mapping import _get_backend_name


def _is_clickhouse(db_name: str | None) -> bool:
    return _get_backend_name(db_name) == "clickhouse"


def _suppress_ch_column_ops(ops: list[Any]) -> list[Any]:
    """Remove alter_column_* ops that overlap with ChColumnHandler for CH."""
    suppress = frozenset({
        "alter_column_type",
        "alter_column_nullable",
        "alter_column_default",
        "alter_column_comment",
    })
    return [
        op
        for op in ops
        if (op.object_type if hasattr(op, "object_type") else op.get("type")) not in suppress
    ]


_SYSTEM_TABLE_PREFIXES = ("_dbwarden_", "dbwarden_lock")


def _is_system_table_name(name: str | None) -> bool:
    return bool(name and name.startswith(_SYSTEM_TABLE_PREFIXES))


def _filter_system_objects(snapshot: dict[str, Any]) -> dict[str, Any]:
    filtered = dict(snapshot)
    filtered["tables"] = {
        name: spec
        for name, spec in snapshot.get("tables", {}).items()
        if not _is_system_table_name(name)
    }
    for key in ("indexes", "constraints"):
        filtered[key] = {
            name: spec
            for name, spec in snapshot.get(key, {}).items()
            if not _is_system_table_name(spec.get("table"))
        }
    return filtered


def diff_models_against_snapshot(
    model_tables: list[Any],
    snapshot: dict[str, Any],
    database: str | None = None,
    db_name: str | None = None,
    clickhouse_engine_recreate: bool = False,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    upgrade_ops: list[dict[str, Any]] = []
    rollback_ops: list[dict[str, Any]] = []

    snapshot = _filter_system_objects(snapshot)
    model_tables = [t for t in model_tables if not _is_system_table_name(t.name)]
    snapshot_tables = snapshot.get("tables", {})
    model_by_name = {t.name: t for t in model_tables}
    backend_is_ch = _is_clickhouse(db_name or database)
    has_ch_tables = (
        any(getattr(t, "clickhouse_options", None) for t in model_tables)
        or any(spec.get("ch_options") for spec in snapshot_tables.values())
    )
    is_ch = backend_is_ch or has_ch_tables

    for table in model_tables:
        if table.name.startswith(_SYSTEM_TABLE_PREFIXES):
            continue
        if table.name not in snapshot_tables:
            continue
        if table.object_type == "view":
            continue

        snap_table = snapshot_tables[table.name]
        snap_columns = snap_table.get("columns", {})
        model_columns = {c.name: c for c in table.columns}

    from dbwarden.engine.core.protocol import op_to_dict
    from dbwarden.engine.core.registry import RegistryDriver

    def _extend_ops(target: list[dict[str, Any]], ops: list[Any]) -> None:
        for op in ops:
            target.append(op_to_dict(op))

    from dbwarden.engine.backends.postgresql.handlers import ConstraintHandler
    _con_handler = ConstraintHandler()
    _con_handler._snapshot = snapshot
    _con_handler._view_tables = {t.name for t in model_tables if getattr(t, 'object_type', None) == 'view'}
    _con_driver = RegistryDriver(include_plugins=False)
    _con_driver.register(_con_handler)
    _con_up, _con_rb = _con_driver.run(snapshot, model_tables, None)
    _extend_ops(upgrade_ops, _con_up)
    _extend_ops(rollback_ops, _con_rb)

    from dbwarden.engine.backends.postgresql.handlers import ColumnHandler
    _col_handler = ColumnHandler()
    _col_handler._db_name = db_name
    _col_driver = RegistryDriver(include_plugins=False)
    _col_driver.register(_col_handler)
    _col_up, _col_rb = _col_driver.run(snapshot, model_tables, None)
    if backend_is_ch:
        _col_up = _suppress_ch_column_ops(_col_up)
        _col_rb = _suppress_ch_column_ops(_col_rb)
    _extend_ops(upgrade_ops, _col_up)
    _extend_ops(rollback_ops, _col_rb)

    from dbwarden.engine.backends.clickhouse.handlers import (
        ChAggTargetHandler,
        ChColumnHandler,
        ChCommentHandler,
        ChDataOpHandler,
        ChDictionaryHandler,
        ChMaterializedViewHandler,
        ChProjectionHandler,
        ChSkipIndexHandler,
        ChTableHandler,
    )
    if is_ch:
        _ch_handler = ChTableHandler()
        _ch_handler.clickhouse_engine_recreate = clickhouse_engine_recreate
        _ch_driver = RegistryDriver(include_plugins=False)
        _ch_driver.register(_ch_handler)
        _ch_driver.register(ChColumnHandler())
        _ch_driver.register(ChMaterializedViewHandler())
        _ch_driver.register(ChDictionaryHandler())
        _ch_driver.register(ChProjectionHandler())
        _ch_driver.register(ChSkipIndexHandler())
        _ch_driver.register(ChAggTargetHandler())
        _ch_driver.register(ChDataOpHandler())
        _ch_driver.register(ChCommentHandler())
        _ch_up, _ch_rb = _ch_driver.run(snapshot, model_tables, None)
        _extend_ops(upgrade_ops, _ch_up)
        _extend_ops(rollback_ops, _ch_rb)

    from dbwarden.engine.backends.postgresql.handlers import PgTableHandler
    _pgt_driver = RegistryDriver(include_plugins=False)
    _pgt_driver.register(PgTableHandler())
    _pgt_up, _pgt_rb = _pgt_driver.run(snapshot, model_tables, None)
    _extend_ops(upgrade_ops, _pgt_up)
    _extend_ops(rollback_ops, _pgt_rb)

    from dbwarden.engine.backends.postgresql.handlers import IndexHandler
    _idx_driver = RegistryDriver(include_plugins=False)
    _idx_driver.register(IndexHandler())
    _idx_up, _idx_rb = _idx_driver.run(snapshot, model_tables, None)
    _extend_ops(upgrade_ops, _idx_up)
    _extend_ops(rollback_ops, _idx_rb)

    from dbwarden.engine.backends.postgresql.handlers import (
        PartitionHandler,
        StatisticsHandler,
    )
    _pg_pre_driver = RegistryDriver(include_plugins=False)
    _pg_pre_driver.register(PartitionHandler())
    _pg_pre_driver.register(StatisticsHandler())
    _pg_pre_up, _pg_pre_rb = _pg_pre_driver.run(snapshot, model_tables, None)
    _extend_ops(upgrade_ops, _pg_pre_up)
    _extend_ops(rollback_ops, _pg_pre_rb)

    # Plugin-registered object handlers run exactly once, in their own pass.
    # Every core driver above is scoped to the handlers it registers, so this is
    # the only place plugin ops enter the model diff.
    from dbwarden.plugin import ObjectPluginRegistry as _ObjectPluginRegistry

    _plugin_driver = RegistryDriver(include_plugins=False)
    _has_plugin_handlers = False
    for _registration in _ObjectPluginRegistry.handlers().values():
        _plugin_driver.register(_registration.handler)
        _has_plugin_handlers = True
    if _has_plugin_handlers:
        _plugin_up, _plugin_rb = _plugin_driver.run(snapshot, model_tables, None)
        _extend_ops(upgrade_ops, _plugin_up)
        _extend_ops(rollback_ops, _plugin_rb)

    from dbwarden.engine.backends.postgresql.handlers import ViewHandler
    _view_driver = RegistryDriver(include_plugins=False)
    _view_driver.register(ViewHandler())
    _view_up, _view_rb = _view_driver.run(snapshot, model_tables, None)
    _extend_ops(upgrade_ops, _view_up)
    _extend_ops(rollback_ops, _view_rb)

    from dbwarden.engine.backends.postgresql.handlers import TableHandler
    _table_driver = RegistryDriver(include_plugins=False)
    _table_driver.register(TableHandler())
    _table_up, _table_rb = _table_driver.run(snapshot, model_tables, None)
    _extend_ops(upgrade_ops, _table_up)
    _extend_ops(rollback_ops, _table_rb)

    from dbwarden.engine.backends.mysql.handlers import MyTableHandler
    _my_driver = RegistryDriver(include_plugins=False)
    _my_driver.register(MyTableHandler())
    _my_up, _my_rb = _my_driver.run(snapshot, model_tables, None)
    _extend_ops(upgrade_ops, _my_up)
    _extend_ops(rollback_ops, _my_rb)

    create_tables = {op["table"] for op in upgrade_ops if op.get("type") == "create_table"}
    if create_tables:
        def _is_redundant_initial_table_op(op: dict[str, Any]) -> bool:
            if op.get("type") in {"attach_partition", "detach_partition"}:
                return op.get("partition_name") in create_tables
            if op.get("table") not in create_tables:
                return False
            if op.get("type") in {
                "add_column",
                "drop_column",
                "alter_column_type",
                "alter_column_nullable",
                "alter_column_default",
                "alter_column_autoincrement",
                "alter_column_comment",
                "alter_table_comment",
            }:
                return True
            if op.get("type") == "alter_pg_partition":
                return True
            # MySQL table options for a brand-new table are emitted inline by
            # CREATE TABLE, so a separate ALTER TABLE is redundant (and would
            # target a table that does not exist yet).
            if op.get("type") == "alter_my_table":
                return True
            if op.get("type") == "alter_pg_table" and op.get("key") in {
                "pg_partition",
                "pg_partition_of",
                "pg_partition_bound",
                "pg_inherits",
                "pg_tablespace",
                "pg_unlogged",
            }:
                return True
            return False

        upgrade_ops = [op for op in upgrade_ops if not _is_redundant_initial_table_op(op)]
        rollback_ops = [op for op in rollback_ops if not _is_redundant_initial_table_op(op)]

    for op in upgrade_ops + rollback_ops:
        if op.get("type") == "recreate_ch_table":
            tname = op["table"]
            mvs = sorted(
                mt.name for mt in model_tables
                if mt.clickhouse_options.get("ch_to_table") == tname
                and snapshot_tables.get(mt.name, {}).get("ch_options", {}).get("ch_to_table") == tname
            )
            if mvs:
                op["dependent_mvs"] = mvs

    recreate_tables = {op["table"] for op in upgrade_ops if op.get("type") == "recreate_ch_table"}
    if recreate_tables:
        allowed = {"recreate_ch_table", "drop_table", "create_table", "rename_table", "alter_enum_add_value", "create_type", "drop_type"}
        upgrade_ops = [op for op in upgrade_ops if op.get("table") not in recreate_tables or op.get("type") in allowed]
        rollback_ops = [op for op in rollback_ops if op.get("table") not in recreate_tables or op.get("type") in allowed]

    return upgrade_ops, rollback_ops
