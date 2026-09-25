"""Data-losing rollbacks must be visible to operators at default verbosity.

Covers the "downgrade ran clean, data was gone" failure class: dropping a
column (natively or via a SQLite table rebuild) restores the schema on
rollback but not the row data. That consequence must surface as a warning at
generation time without requiring verbose mode — previously it was only a
CRITICAL severity signal at apply time.
"""

import logging
from types import SimpleNamespace

from dbwarden.engine.core.models import ModelColumn, ModelTable
from dbwarden.engine.offline import diff_model_states, model_state_to_dict
from dbwarden.engine.snapshot import (
    MigrationStatement,
    StatementOrder,
    diff_models_against_snapshot,
    snapshot_diff_to_sql,
)
from dbwarden.engine.snapshot.sql_gen import collect_rollback_warnings
from dbwarden.logging import get_logger, reset_logger

DATA_LOSS_MESSAGE = (
    "drop_column users.phone_number: rollback restores the column with NULL "
    "values; row data is not recoverable"
)


def _pg_col(name, type="integer", nullable=False, pk=False, default=None):
    return ModelColumn(name, type, nullable, pk, False, default, None)


def _pg_table(name, columns=None, comment=None, ch_opts=None, pg_table=None, object_type="table"):
    return ModelTable(
        name=name,
        columns=columns or [_pg_col("id")],
        clickhouse_options=ch_opts or {},
        comment=comment,
        pg_table=pg_table or {},
        object_type=object_type,
    )


def _sqlite_col(name, type_="TEXT", *, nullable=True, pk=False, default=None, sq_meta=None):
    return ModelColumn(
        name=name, type=type_, nullable=nullable, primary_key=pk, unique=False,
        default=default, foreign_key=None, sq_meta=sq_meta or {},
        autoincrement=True if pk else None,
    )


def _sqlite_snapshot_with_email():
    return {
        "tables": {
            "users": {
                "columns": {
                    "id": {"type": "INTEGER", "nullable": False, "primary_key": True, "autoincrement": True},
                    "email": {"type": "VARCHAR(255)", "nullable": True, "primary_key": False},
                    "age": {"type": "INTEGER", "nullable": True, "primary_key": False},
                },
                "primary_key": ["id"],
                "comment": None,
            },
        },
        "indexes": {
            "ix_users_email": {
                "table": "users", "name": "ix_users_email",
                "columns": ["email"], "unique": False,
            },
        },
        "constraints": {},
        "enums": {},
    }


def _sqlite_users_model():
    # Drops indexed column email, which SQLite can only express as a table
    # rebuild (an indexed column cannot be dropped by ALTER TABLE).
    return ModelTable(
        name="users",
        columns=[
            _sqlite_col("id", "INTEGER", nullable=False, pk=True),
            _sqlite_col("age", "INTEGER"),
        ],
    )


def _use_sqlite_config(monkeypatch):
    config = SimpleNamespace(database_type="sqlite", model_paths=None, model_tables=None)
    monkeypatch.setattr(
        "dbwarden.engine.model_discovery.type_mapping.get_database",
        lambda db_name=None: config,
    )
    monkeypatch.setattr("dbwarden.config.get_database", lambda db_name=None: config)


def test_drop_column_rollback_data_loss_warns_at_default_verbosity(caplog):
    reset_logger()
    get_logger(verbose=False)
    try:
        prev = model_state_to_dict([_pg_table("users", columns=[
            _pg_col("id"), _pg_col("phone_number", "varchar", nullable=True),
        ])])
        curr = model_state_to_dict([_pg_table("users")])
        up_ops, down_ops = diff_model_states(prev, curr)
        assert [op["type"] for op in up_ops] == ["drop_column"]

        with caplog.at_level(logging.WARNING):
            snapshot_diff_to_sql(up_ops, down_ops, db_name="primary")

        assert DATA_LOSS_MESSAGE in caplog.text
    finally:
        reset_logger()


def test_sqlite_rebuild_data_loss_reason_warns_at_default_verbosity(monkeypatch, caplog):
    _use_sqlite_config(monkeypatch)
    reset_logger()
    get_logger(verbose=False)
    try:
        upgrade_ops, rollback_ops = diff_models_against_snapshot(
            [_sqlite_users_model()], _sqlite_snapshot_with_email(), db_name=None,
        )
        assert [op["type"] for op in upgrade_ops] == ["recreate_sq_table"]

        with caplog.at_level(logging.WARNING):
            snapshot_diff_to_sql(upgrade_ops, rollback_ops, db_name=None)

        assert (
            "recreate_sq_table users: rebuild of 'users' drops column(s) email; "
            "the rollback restores the columns but not their data"
        ) in caplog.text
        # The generic conditional-rollback diagnostic stays verbose-only.
        assert "Conditional rollback for" not in caplog.text
    finally:
        reset_logger()


def test_collect_rollback_warnings_mirrors_drop_column_message():
    stmt = MigrationStatement(
        order=StatementOrder.DROP_COLUMN,
        upgrade_sql="ALTER TABLE users DROP COLUMN phone_number",
        rollback_sql="ALTER TABLE users ADD COLUMN phone_number varchar",
    )

    warnings = collect_rollback_warnings([
        ({"type": "drop_column", "table": "users", "column": "phone_number"}, stmt),
    ])

    assert warnings == [DATA_LOSS_MESSAGE]


def test_collect_rollback_warnings_skips_rollbacks_that_restore_data():
    conditional_no_data_loss = MigrationStatement(
        order=StatementOrder.ALTER_TABLE_OPTIONS,
        upgrade_sql="RECREATE TABLE events",
        rollback_sql="RECREATE TABLE events",
        rollback_kind="conditional",
        rollback_reason=(
            "ClickHouse engine transition merge_tree -> log is known "
            "row-preserving; rollback still depends on successful recreate "
            "round-trip."
        ),
    )
    irreversible = MigrationStatement(
        order=StatementOrder.ALTER_TABLE_OPTIONS,
        upgrade_sql="REFRESH MATERIALIZED VIEW mv",
        rollback_sql="-- irreversible: cannot restore rows",
        rollback_kind="irreversible",
    )
    real_add_column = MigrationStatement(
        order=StatementOrder.ADD_COLUMN,
        upgrade_sql="ALTER TABLE users ADD COLUMN phone_number varchar",
        rollback_sql="ALTER TABLE users DROP COLUMN phone_number",
    )

    assert collect_rollback_warnings([
        ({"type": "recreate_ch_table", "table": "events"}, conditional_no_data_loss),
        ({"type": "refresh_matview", "table": "mv"}, irreversible),
        ({"type": "add_column", "table": "users", "column": "phone_number"}, real_add_column),
    ]) == []
