"""Data-losing rollbacks must be visible to operators at default verbosity.

Covers the "downgrade ran clean, data was gone" failure class: dropping a
column (natively or via a SQLite table rebuild) restores the schema on
rollback but not the row data. That consequence must surface as a warning at
generation time without requiring verbose mode — previously it was only a
CRITICAL severity signal at apply time.
"""

import logging
import re
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


def test_build_migration_plan_records_rollback_warnings():
    from dbwarden.commands.make_migrations.migrate_plan import build_migration_plan

    plan = build_migration_plan(
        migration_id="primary__0001_test",
        changes=[],
        upgrade_sql="-- upgrade\nALTER TABLE users DROP COLUMN phone_number;\n",
        rollback_warnings=[DATA_LOSS_MESSAGE],
    )

    assert plan["rollback_warnings"] == [DATA_LOSS_MESSAGE]


def test_build_migration_plan_omits_rollback_warnings_when_empty():
    from dbwarden.commands.make_migrations.migrate_plan import build_migration_plan

    without_carrier = build_migration_plan(
        migration_id="primary__0001_test",
        changes=[],
        upgrade_sql="-- upgrade\nCREATE TABLE users (id INTEGER);\n",
    )
    assert "rollback_warnings" not in without_carrier

    with_empty_carrier = build_migration_plan(
        migration_id="primary__0001_test",
        changes=[],
        upgrade_sql="-- upgrade\nCREATE TABLE users (id INTEGER);\n",
        rollback_warnings=[],
    )
    assert "rollback_warnings" not in with_empty_carrier


def test_generate_files_records_rollback_warnings_in_plan(monkeypatch, tmp_path):
    from dbwarden.commands.make_migrations.generation import generate_files

    _use_sqlite_config(monkeypatch)
    migrations_dir = tmp_path / "migrations"
    migrations_dir.mkdir()
    upgrade_ops, rollback_ops = diff_models_against_snapshot(
        [_sqlite_users_model()], _sqlite_snapshot_with_email(), db_name=None,
    )

    artifacts = generate_files(
        upgrade_ops,
        rollback_ops,
        migrations_dir=str(migrations_dir),
        database=None,
        db_name=None,
        write=False,
    )

    plan = artifacts[0]["plan"]
    assert plan["rollback_warnings"] == [
        "recreate_sq_table users: rebuild of 'users' drops column(s) email; "
        "the rollback restores the columns but not their data"
    ]


def test_generate_files_records_native_drop_column_warning_in_plan(monkeypatch, tmp_path):
    from dbwarden.commands.make_migrations.generation import generate_files

    _use_sqlite_config(monkeypatch)
    migrations_dir = tmp_path / "migrations"
    migrations_dir.mkdir()
    # Dropping a non-indexed column is expressed as a native DROP COLUMN.
    upgrade_ops, rollback_ops = diff_models_against_snapshot(
        [
            ModelTable(
                name="users",
                columns=[
                    _sqlite_col("id", "INTEGER", nullable=False, pk=True),
                    _sqlite_col("email", "VARCHAR(255)"),
                ],
                indexes=[{"name": "ix_users_email", "columns": ["email"], "unique": False}],
            )
        ],
        _sqlite_snapshot_with_email(),
        db_name=None,
    )
    assert [op["type"] for op in upgrade_ops] == ["drop_column"]

    artifacts = generate_files(
        upgrade_ops,
        rollback_ops,
        migrations_dir=str(migrations_dir),
        database=None,
        db_name=None,
        write=False,
    )

    plan = artifacts[0]["plan"]
    assert plan["rollback_warnings"] == [
        "drop_column users.age: rollback restores the column with NULL values; "
        "row data is not recoverable"
    ]


def test_migrate_prints_rollback_warnings_from_trusted_plan(tmp_path, monkeypatch, capsys):
    import json as _json

    from dbwarden.commands.migrate import migrate_cmd
    from dbwarden.config import set_dev_mode
    from dbwarden.engine.safety.plans import bind_plan

    set_dev_mode(False)
    monkeypatch.chdir(tmp_path)
    db = tmp_path / "app.db"
    (tmp_path / "dbwarden.py").write_text(
        "from dbwarden import database_config\n"
        f"database_config(database_name='primary', default=True, database_type='sqlite', database_url_sync={('sqlite:///' + db.as_posix())!r})\n",
        encoding="utf-8",
    )
    directory = tmp_path / "migrations" / "primary"
    directory.mkdir(parents=True)
    path = directory / "primary__0001_base.sql"
    content = "-- upgrade\nCREATE TABLE items (id INTEGER PRIMARY KEY);\n-- rollback\nDROP TABLE items;\n"
    path.write_text(content, encoding="utf-8")
    plan = bind_plan(
        {}, content, [{"type": "create_table"}], "sqlite", project_root=tmp_path
    )
    plan["rollback_warnings"] = [DATA_LOSS_MESSAGE]
    path.with_suffix(".plan.json").write_text(_json.dumps(plan), encoding="utf-8")

    try:
        migrate_cmd(database="primary", dry_run=True)

        clean = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", capsys.readouterr().out)
        assert f"0001: {DATA_LOSS_MESSAGE}" in clean
    finally:
        from dbwarden.connection.connection import dispose_engine
        dispose_engine("sqlite:///" + db.as_posix(), "sqlite")
