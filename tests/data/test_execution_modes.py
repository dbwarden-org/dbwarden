import json
import os
import uuid

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL

from dbwarden.data.execution import (
    _ch_append_run,
    _ch_latest_run,
    _prepare_identity_edges,
    execute_data_plan,
)
from tests.data.test_nontransactional import (
    ClickHouseConnection,
    MySQLConnection,
    _clickhouse_plan,
    _plan,
)


def test_baseline_event_is_structured_on_mysql_and_clickhouse():
    engine = create_engine("sqlite://")
    with engine.connect() as raw:
        raw.execute(text("CREATE TABLE values_table (value INTEGER UNIQUE)"))
        raw.commit()
        mysql = MySQLConnection(raw)
        execute_data_plan(mysql, _plan(), migration_id="mysql__baseline", baseline=True)
        details = raw.execute(
            text(
                "SELECT details FROM _dbwarden_data_events WHERE event_type = 'BASELINE'"
            )
        ).scalar_one()
        assert json.loads(details) == {
            "acknowledged": True,
            "reason": "explicit baseline requested",
            "skipped_checks": ["guard:upgrade:write:after:written"],
        }

    clickhouse = ClickHouseConnection()
    execute_data_plan(
        clickhouse,
        _clickhouse_plan(),
        migration_id="clickhouse__baseline",
        baseline=True,
    )
    event = next(item for item in clickhouse.events if item["event_type"] == "BASELINE")
    assert json.loads(event["details"])["acknowledged"] is True
    assert json.loads(event["details"])["skipped_checks"] == [
        "guard:upgrade:write:after:written"
    ]


@pytest.mark.parametrize("status", ["ABANDONED", "FAILED_FINAL"])
def test_nontransactional_terminal_statuses_cannot_restart(status):
    engine = create_engine("sqlite://")
    with engine.connect() as raw:
        raw.execute(text("CREATE TABLE values_table (value INTEGER UNIQUE)"))
        raw.commit()
        connection = MySQLConnection(raw)
        execute_data_plan(connection, _plan(), migration_id="primary__0001")
        raw.execute(
            text(
                "UPDATE _dbwarden_data_runs SET status = :status "
                "WHERE migration_id = 'primary__0001'"
            ),
            {"status": status},
        )
        raw.commit()
        with pytest.raises(RuntimeError, match=f"terminally {status}"):
            execute_data_plan(
                connection, _plan(), migration_id="primary__0001", reapply=True
            )

    clickhouse = ClickHouseConnection()
    plan = _clickhouse_plan()
    execute_data_plan(clickhouse, plan, migration_id="primary__0001")
    _ch_append_run(
        clickhouse, _ch_latest_run(clickhouse, "primary__0001"), status=status
    )
    with pytest.raises(RuntimeError, match=f"terminally {status}"):
        execute_data_plan(clickhouse, plan, migration_id="primary__0001", reapply=True)


def _mysql_edge_ddl():
    engine = create_engine("sqlite://")
    with engine.connect() as raw:
        raw.execute(
            text(
                "CREATE TABLE transition_edges (source_id TEXT, target_id INTEGER, "
                "owned_write INTEGER)"
            )
        )
        raw.commit()
        connection = MySQLConnection(raw)
        step = {
            "identity_edges": {
                "definition_id": "definition-" + "d" * 200,
                "ledger_table": "transition_edges",
                "source_columns": ["source_id"],
                "target_columns": ["target_id"],
                "owned_column": "owned_write",
            }
        }
        _prepare_identity_edges(connection, [step], clickhouse=False)
    return next(
        statement
        for statement in connection.statements
        if statement.startswith("CREATE TABLE IF NOT EXISTS _dbwarden_data_edges")
    )


def test_mysql_identity_edge_primary_key_fits_innodb_limit():
    ddl = _mysql_edge_ddl()
    assert "edge_id VARCHAR(64) PRIMARY KEY" in ddl
    assert "PRIMARY KEY (migration_id" not in ddl


@pytest.mark.parametrize(
    ("prefix", "driver"),
    [("MYSQL", "mysql+pymysql"), ("MARIADB", "mariadb+pymysql")],
)
def test_native_mysql_identity_edge_ddl(prefix, driver):
    required = ["HOST", "PORT", "USER", "PASSWORD", "DATABASE"]
    values = {name: os.environ.get(f"{prefix}_{name}") for name in required}
    if not all(values.values()):
        pytest.skip(f"Set {prefix}_* variables for native DDL validation")
    table = "_dbw_edge_ddl_" + uuid.uuid4().hex
    ddl = _mysql_edge_ddl().replace("_dbwarden_data_edges", table, 1)
    engine = create_engine(
        URL.create(
            driver,
            username=values["USER"],
            password=values["PASSWORD"],
            host=values["HOST"],
            port=int(values["PORT"]),
            database=values["DATABASE"],
        )
    )
    with engine.connect() as connection:
        try:
            connection.exec_driver_sql(ddl)
            created = connection.exec_driver_sql(f"SHOW CREATE TABLE `{table}`").one()[
                1
            ]
            assert "PRIMARY KEY (`edge_id`)" in created
        finally:
            connection.exec_driver_sql(f"DROP TABLE IF EXISTS `{table}`")
