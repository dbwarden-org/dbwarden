from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import OperationalError

from dbwarden.data.execution import (
    DataExecutionError,
    execute_data_plan,
    reconcile_data_plan,
    verify_identity_edges,
)


class MySQLConnection:
    dialect = SimpleNamespace(name="mysql")

    def __init__(self, connection):
        self.connection = connection
        self.fail_after_effect = False
        self.fail_before_effect = False
        self.target = None
        self.statements = []

    def execute(self, statement, parameters=None):
        sql = str(statement)
        self.statements.append(sql)
        if self.fail_before_effect and self.target in sql:
            self.fail_before_effect = False
            raise OperationalError(sql, parameters, OSError("statement rejected"))
        if self.fail_after_effect and self.target in sql:
            self.fail_after_effect = False
            self.connection.execute(statement, parameters or {})
            self.connection.commit()
            raise OperationalError(sql, parameters, OSError("connection lost"))
        return self.connection.execute(statement, parameters or {})

    def commit(self):
        self.connection.commit()

    def rollback(self):
        self.connection.rollback()

    def in_transaction(self):
        return self.connection.in_transaction()

    def _dbwarden_has_table(self, table_name):
        return (
            self.connection.execute(
                text(
                    "SELECT count(*) FROM sqlite_master WHERE type = 'table' AND name = :name"
                ),
                {"name": table_name},
            ).scalar_one()
            == 1
        )


class Result:
    def __init__(self, rows=None, scalar=None):
        self.rows = rows or []
        self.value = scalar

    def mappings(self):
        return self

    def first(self):
        return self.rows[0] if self.rows else None

    def all(self):
        return self.rows

    def scalar_one(self):
        return self.value


class ClickHouseConnection:
    dialect = SimpleNamespace(name="clickhouse")

    def __init__(self):
        self.runs = []
        self.checkpoints = []
        self.events = []
        self.values = set()
        self.statements = []
        self.target = None
        self.fail_after_effect = False
        self.mutation = {"is_done": 1, "latest_fail_reason": ""}

    def execute(self, statement, parameters=None):
        sql = str(statement)
        parameters = parameters or {}
        self.statements.append(sql)
        if sql.startswith("EXISTS TABLE _dbwarden_data_"):
            if sql.endswith("_runs"):
                exists = self.runs
            elif sql.endswith("_operations"):
                exists = self.checkpoints
            else:
                exists = self.events
            return Result(scalar=int(bool(exists)))
        if sql.startswith("CREATE TABLE IF NOT EXISTS _dbwarden_data_"):
            return Result()
        if sql.startswith("INSERT INTO _dbwarden_data_runs"):
            self.runs.append(dict(parameters))
            return Result()
        if sql.startswith("INSERT INTO _dbwarden_data_operations"):
            self.checkpoints.append(dict(parameters))
            return Result()
        if sql.startswith("INSERT INTO _dbwarden_data_events"):
            self.events.append(dict(parameters))
            return Result()
        if "FROM _dbwarden_data_runs FINAL" in sql:
            rows = self._final(self.runs, ("migration_id", "epoch"))
            rows = [
                row for row in rows if row["migration_id"] == parameters["migration_id"]
            ]
            rows.sort(key=lambda row: row["epoch"], reverse=True)
            return Result(rows[:1])
        if "FROM _dbwarden_data_operations FINAL" in sql:
            rows = self._final(
                self.checkpoints,
                (
                    "migration_id",
                    "epoch",
                    "direction",
                    "operation_id",
                    "statement_index",
                ),
            )
            rows = self._filter(rows, parameters)
            rows.sort(
                key=lambda row: (
                    row["direction"],
                    row["operation_id"],
                    row["statement_index"],
                )
            )
            return Result(rows)
        if "FROM _dbwarden_data_runs " in sql:
            rows = self._filter(self.runs, parameters)
            return Result(sorted(rows, key=lambda row: row["version"]))
        if "FROM _dbwarden_data_operations " in sql:
            rows = self._filter(self.checkpoints, parameters)
            return Result(
                sorted(
                    rows,
                    key=lambda row: (
                        row["direction"],
                        row["operation_id"],
                        row["statement_index"],
                        row["version"],
                    ),
                )
            )
        if "FROM system.mutations" in sql:
            return Result([dict(self.mutation)])
        if sql.lstrip().upper().startswith("SELECT") and "values_table" in sql:
            value = 1 if "value = 1" in sql else 2
            return Result(scalar=int(value in self.values))
        if self.fail_after_effect and self.target in sql:
            self.fail_after_effect = False
            self._apply(sql)
            raise OperationalError(sql, parameters, OSError("connection lost"))
        self._apply(sql)
        return Result()

    @staticmethod
    def _filter(rows, parameters):
        keys = {"migration_id", "epoch", "direction", "operation_id", "run_id"}
        return [
            dict(row)
            for row in rows
            if all(
                key not in keys or row.get(key) == value
                for key, value in parameters.items()
            )
        ]

    @staticmethod
    def _final(rows, keys):
        latest = {}
        for row in rows:
            key = tuple(row[item] for item in keys)
            if key not in latest or row["version"] > latest[key]["version"]:
                latest[key] = row
        return [dict(row) for row in latest.values()]

    def _apply(self, sql):
        if sql.startswith("INSERT INTO values_table"):
            self.values.add(1 if "VALUES (1)" in sql else 2)
        elif sql.startswith("ALTER TABLE values_table DELETE"):
            self.values.discard(1)


class SettingsConnection:
    dialect = SimpleNamespace(name="postgresql")

    def __init__(self):
        self.statements = []
        self.settings = {
            "SHOW server_version": "17.6",
            "SHOW TimeZone": "UTC",
            "SHOW search_path": "public",
            "SHOW server_encoding": "UTF8",
        }

    def get_isolation_level(self):
        return "READ COMMITTED"

    def get_execution_options(self):
        return {}

    def execute(self, statement, parameters=None):
        sql = str(statement)
        self.statements.append(sql)
        if sql in self.settings:
            return Result(scalar=self.settings[sql])
        if sql.startswith("SELECT datcollate"):
            return Result(scalar="C.UTF-8")
        raise AssertionError(f"unexpected statement: {sql}")


@pytest.fixture
def mysql_connection():
    engine = create_engine("sqlite://")
    with engine.connect() as connection:
        connection.execute(
            text("CREATE TABLE values_table (value INTEGER NOT NULL UNIQUE)")
        )
        connection.commit()
        yield MySQLConnection(connection)


@pytest.fixture
def clickhouse_connection():
    return ClickHouseConnection()


def _plan(*, conflict=None):
    spec = {"rollback_policy": "restore_preserved_source"}
    if conflict is not None:
        spec["conflict"] = {"policy": conflict}
    return {
        "data_spec": spec,
        "data_bundle": {"manifest_version": 1},
        "data_execution": {
            "upgrade": [
                {
                    "operation_id": "write",
                    "sql": ["INSERT INTO values_table (value) VALUES (1)"],
                    "guards": [
                        {
                            "probe_id": "written",
                            "query": "SELECT count(*) FROM values_table WHERE value = 1",
                            "minimum": 1,
                            "maximum": 1,
                            "timing": "after",
                        }
                    ],
                }
            ],
            "rollback": [
                {
                    "operation_id": "clear",
                    "sql": ["DELETE FROM values_table WHERE value = 1"],
                    "guards": [
                        {
                            "probe_id": "cleared",
                            "query": "SELECT count(*) FROM values_table WHERE value = 1",
                            "minimum": 0,
                            "maximum": 0,
                            "timing": "after",
                        }
                    ],
                }
            ],
        },
    }


def _clickhouse_plan():
    plan = _plan()
    plan["data_execution"]["rollback"][0]["sql"] = [
        ("ALTER TABLE values_table DELETE WHERE value = 1 SETTINGS mutations_sync = 2")
    ]
    return plan


def _count(connection):
    return connection.execute(text("SELECT count(*) FROM values_table")).scalar_one()


def test_nontransactional_success_is_checkpointed_and_at_most_once(mysql_connection):
    calls = []
    statements = []
    completed = []
    result = execute_data_plan(
        mysql_connection,
        _plan(),
        migration_id="primary__0001",
        record_success=lambda: calls.append("success"),
        before_statement=statements.append,
        after_statement=completed.append,
    )

    assert result["status"] == "APPLIED_SUCCESS"
    assert _count(mysql_connection) == 1
    assert calls == ["success"]
    state = reconcile_data_plan(mysql_connection, "primary__0001")
    assert state["status"] == "APPLIED_SUCCESS"
    assert [row["status"] for row in state["checkpoints"]] == ["DONE"]
    assert (
        execute_data_plan(
            mysql_connection,
            _plan(),
            migration_id="primary__0001",
            before_statement=statements.append,
            after_statement=completed.append,
        )["status"]
        == "already_applied"
    )
    assert _count(mysql_connection) == 1
    assert (
        sum(
            statement.startswith("INSERT INTO values_table")
            for statement in mysql_connection.statements
        )
        == 1
    )
    assert statements == ["INSERT INTO values_table (value) VALUES (1)"]
    assert completed == statements
    events = mysql_connection.execute(
        text(
            "SELECT event_type, status, operation_id FROM _dbwarden_data_events "
            "ORDER BY created_at, event_id"
        )
    ).all()
    assert ("RUN_STARTED", "APPLYING", "") in events
    assert ("OPERATION_STARTED", "APPLYING", "write") in events
    assert ("RUN_STATUS", "APPLIED_SUCCESS", "") in events


def test_ambiguous_effect_requires_verified_postcondition(mysql_connection):
    plan = _plan()
    mysql_connection.target = "INSERT INTO values_table"
    mysql_connection.fail_after_effect = True

    with pytest.raises(DataExecutionError, match="operation 'write'") as error:
        execute_data_plan(mysql_connection, plan, migration_id="primary__0001")
    assert "connection lost" not in str(error.value)

    assert _count(mysql_connection) == 1
    state = reconcile_data_plan(mysql_connection, "primary__0001")
    assert state["status"] == "UNKNOWN_REQUIRES_RECONCILIATION"
    assert state["retry_allowed"] is False
    assert state["checkpoints"][0]["status"] == "PENDING"
    with pytest.raises(RuntimeError, match="requires reconciliation"):
        execute_data_plan(mysql_connection, plan, migration_id="primary__0001")

    reconciled = reconcile_data_plan(
        mysql_connection,
        "primary__0001",
        plan=plan,
        apply=True,
        decision="verified_retry",
    )
    assert reconciled["status"] == "FAILED_RETRYABLE"
    assert reconciled["checkpoints"][0]["status"] == "DONE"
    assert (
        execute_data_plan(mysql_connection, plan, migration_id="primary__0001")[
            "status"
        ]
        == "APPLIED_SUCCESS"
    )
    assert _count(mysql_connection) == 1


def test_before_statement_failure_creates_no_ambiguous_checkpoint(mysql_connection):
    def reject(_statement):
        raise RuntimeError("fence expired")

    with pytest.raises(RuntimeError, match="fence expired"):
        execute_data_plan(
            mysql_connection,
            _plan(),
            migration_id="primary__0001",
            before_statement=reject,
        )

    state = reconcile_data_plan(mysql_connection, "primary__0001")
    assert state["status"] == "FAILED_RETRYABLE"
    assert state["retry_allowed"] is True
    assert state["checkpoints"] == []
    assert _count(mysql_connection) == 0


def test_reconciliation_rejects_failed_postcondition(mysql_connection):
    plan = _plan()
    mysql_connection.target = "INSERT INTO values_table"
    mysql_connection.fail_after_effect = True
    with pytest.raises(DataExecutionError):
        execute_data_plan(mysql_connection, plan, migration_id="primary__0001")
    mysql_connection.execute(text("DELETE FROM values_table"))
    mysql_connection.commit()

    with pytest.raises(ValueError, match="written"):
        reconcile_data_plan(
            mysql_connection,
            "primary__0001",
            plan=plan,
            apply=True,
            decision="verified_retry",
        )

    assert (
        reconcile_data_plan(mysql_connection, "primary__0001")["status"]
        == "UNKNOWN_REQUIRES_RECONCILIATION"
    )


def test_known_failure_after_done_work_remains_unknown(mysql_connection):
    plan = _plan()
    plan["data_execution"]["upgrade"].append(
        {
            "operation_id": "write_second",
            "sql": ["INSERT INTO values_table (value) VALUES (2)"],
            "guards": [
                {
                    "probe_id": "second_written",
                    "query": "SELECT count(*) FROM values_table WHERE value = 2",
                    "minimum": 1,
                    "maximum": 1,
                    "timing": "after",
                }
            ],
        }
    )
    mysql_connection.target = "VALUES (2)"
    mysql_connection.fail_before_effect = True

    with pytest.raises(DataExecutionError, match="operation 'write_second'"):
        execute_data_plan(mysql_connection, plan, migration_id="primary__0001")

    state = reconcile_data_plan(mysql_connection, "primary__0001")
    assert state["status"] == "UNKNOWN_REQUIRES_RECONCILIATION"
    assert [row["status"] for row in state["checkpoints"]] == ["DONE", "PENDING"]


def test_reconciliation_rejects_changed_plan(mysql_connection):
    plan = _plan()
    mysql_connection.target = "INSERT INTO values_table"
    mysql_connection.fail_after_effect = True
    with pytest.raises(DataExecutionError):
        execute_data_plan(mysql_connection, plan, migration_id="primary__0001")
    changed = _plan()
    changed["data_execution"]["upgrade"][0]["sql"][0] = (
        "INSERT INTO values_table (value) VALUES (2)"
    )

    with pytest.raises(ValueError, match="checksum"):
        reconcile_data_plan(
            mysql_connection,
            "primary__0001",
            plan=changed,
            apply=True,
            decision="verified_retry",
        )


def test_ambiguous_rollback_reconciles_only_current_attempt(mysql_connection):
    plan = _plan()
    execute_data_plan(mysql_connection, plan, migration_id="primary__0001")
    mysql_connection.target = "DELETE FROM values_table"
    mysql_connection.fail_after_effect = True

    with pytest.raises(DataExecutionError):
        execute_data_plan(
            mysql_connection,
            plan,
            migration_id="primary__0001",
            direction="rollback",
        )

    reconciled = reconcile_data_plan(
        mysql_connection,
        "primary__0001",
        plan=plan,
        apply=True,
        decision="verified_retry",
    )
    assert reconciled["status"] == "FAILED_RETRYABLE"
    assert (
        execute_data_plan(
            mysql_connection,
            plan,
            migration_id="primary__0001",
            direction="rollback",
        )["status"]
        == "ROLLED_BACK"
    )
    assert _count(mysql_connection) == 0


def test_nontransactional_baseline_and_rollback_are_at_most_once(mysql_connection):
    baseline = execute_data_plan(
        mysql_connection, _plan(), migration_id="primary__baseline", baseline=True
    )
    assert baseline["status"] == "APPLIED_SUCCESS"
    assert baseline["baseline"] is True
    assert (
        execute_data_plan(mysql_connection, _plan(), migration_id="primary__baseline")[
            "status"
        ]
        == "already_applied"
    )
    with pytest.raises(ValueError, match="Rollback requires"):
        execute_data_plan(
            mysql_connection,
            _plan(),
            migration_id="primary__baseline",
            direction="rollback",
        )

    execute_data_plan(mysql_connection, _plan(), migration_id="primary__rollback")
    assert (
        execute_data_plan(
            mysql_connection,
            _plan(),
            migration_id="primary__rollback",
            direction="rollback",
        )["status"]
        == "ROLLED_BACK"
    )
    assert _count(mysql_connection) == 0
    with pytest.raises(ValueError, match="Rollback requires"):
        execute_data_plan(
            mysql_connection,
            _plan(),
            migration_id="primary__rollback",
            direction="rollback",
        )


def test_plain_conflict_ignore_is_rejected(mysql_connection):
    with pytest.raises(ValueError, match="Plain target-conflict ignore"):
        execute_data_plan(
            mysql_connection,
            _plan(conflict="ignore"),
            migration_id="primary__0001",
        )


def test_backend_pinned_settings_mismatch_fails_before_writes():
    connection = SettingsConnection()
    plan = _plan()
    plan["data_spec"]["expression"] = {
        "determinism_class": "backend_pinned",
        "backend_settings": {
            "backend_version": "17",
            "timezone": "America/Montevideo",
            "collation": "C.UTF-8",
            "search_path": "public",
            "encoding": "UTF8",
        },
    }

    with pytest.raises(ValueError, match="timezone"):
        execute_data_plan(connection, plan, migration_id="primary__0001")

    assert not any(
        statement.startswith("INSERT INTO values_table")
        for statement in connection.statements
    )


def test_identity_edges_use_retained_hmac_key_without_raw_identities(mysql_connection):
    mysql_connection.execute(
        text(
            "CREATE TABLE transition_edges (source_id TEXT NOT NULL, "
            "target_id INTEGER NOT NULL, mapped_value INTEGER NOT NULL, "
            "owned_write INTEGER NOT NULL)"
        )
    )
    mysql_connection.execute(
        text("INSERT INTO transition_edges VALUES (:source, 1, 1, 1)"),
        {"source": "customer@example.invalid"},
    )
    mysql_connection.commit()
    plan = _plan()
    plan["data_execution"]["upgrade"][0]["identity_edges"] = {
        "definition_id": "split.people",
        "ledger_table": "transition_edges",
        "source_columns": ["source_id"],
        "target_identity_columns": ["target_id"],
        "value_columns": ["mapped_value"],
        "target_table": {"schema": None, "table": "values_table"},
        "target_columns": ["value"],
        "target_key_columns": ["value"],
        "owned_column": "owned_write",
    }

    execute_data_plan(mysql_connection, plan, migration_id="primary__0001")

    edge = (
        mysql_connection.execute(
            text(
                "SELECT source_identity_hash, target_identity_hash, value_hash, key_id, "
                "encoding_version, status, verification_checksum "
                "FROM _dbwarden_data_edges"
            )
        )
        .mappings()
        .first()
    )
    key = (
        mysql_connection.execute(
            text("SELECT key_id, key_material FROM _dbwarden_data_keys")
        )
        .mappings()
        .first()
    )
    assert edge["source_identity_hash"].startswith("hmac-sha256:")
    assert edge["target_identity_hash"].startswith("hmac-sha256:")
    assert edge["value_hash"].startswith("hmac-sha256:")
    assert edge["verification_checksum"].startswith("hmac-sha256:")
    assert edge["key_id"] == key["key_id"]
    assert edge["encoding_version"] == 1
    assert edge["status"] == "VERIFIED"
    assert "customer@example.invalid" not in repr(edge)
    assert len(key["key_material"]) == 64
    assert verify_identity_edges(
        mysql_connection,
        plan["data_execution"]["upgrade"][0],
        "primary__0001",
        1,
    ) == {"verified": True, "count": 1, "definition_id": "split.people"}


def test_conflicting_identity_edges_fail_without_raw_identity_leak(mysql_connection):
    mysql_connection.execute(
        text(
            "CREATE TABLE transition_edges (source_id TEXT NOT NULL, "
            "target_id INTEGER NOT NULL, owned_write INTEGER NOT NULL)"
        )
    )
    mysql_connection.execute(
        text(
            "INSERT INTO transition_edges VALUES ('sensitive-source', 1, 1), "
            "('sensitive-source', 2, 1)"
        )
    )
    mysql_connection.commit()
    plan = _plan()
    plan["data_execution"]["upgrade"][0]["identity_edges"] = {
        "definition_id": "split.people",
        "ledger_table": "transition_edges",
        "source_columns": ["source_id"],
        "target_columns": ["target_id"],
        "owned_column": "owned_write",
    }

    with pytest.raises(ValueError, match="Conflicting identity edges") as error:
        execute_data_plan(mysql_connection, plan, migration_id="primary__0001")

    assert "sensitive-source" not in str(error.value)


def test_clickhouse_append_journal_success_and_at_most_once(clickhouse_connection):
    plan = _clickhouse_plan()
    result = execute_data_plan(
        clickhouse_connection, plan, migration_id="primary__0001"
    )

    assert result["status"] == "APPLIED_SUCCESS"
    assert clickhouse_connection.values == {1}
    state = reconcile_data_plan(clickhouse_connection, "primary__0001")
    assert state["status"] == "APPLIED_SUCCESS"
    assert [row["status"] for row in state["checkpoints"]] == ["DONE"]
    assert (
        execute_data_plan(clickhouse_connection, plan, migration_id="primary__0001")[
            "status"
        ]
        == "already_applied"
    )
    assert (
        sum(
            statement.startswith("INSERT INTO values_table")
            for statement in clickhouse_connection.statements
        )
        == 1
    )
    metadata_sql = "\n".join(
        statement
        for statement in clickhouse_connection.statements
        if "_dbwarden_data_" in statement
    )
    assert "ReplacingMergeTree(version)" in metadata_sql
    assert "UPDATE _dbwarden_data_" not in metadata_sql
    assert [event["event_type"] for event in clickhouse_connection.events] == [
        "RUN_STARTED",
        "OPERATION_STARTED",
        "RUN_STATUS",
    ]


def test_clickhouse_ambiguous_effect_needs_verified_retry(clickhouse_connection):
    plan = _clickhouse_plan()
    clickhouse_connection.target = "INSERT INTO values_table"
    clickhouse_connection.fail_after_effect = True

    with pytest.raises(DataExecutionError):
        execute_data_plan(clickhouse_connection, plan, migration_id="primary__0001")

    state = reconcile_data_plan(clickhouse_connection, "primary__0001")
    assert state["status"] == "UNKNOWN_REQUIRES_RECONCILIATION"
    reconciled = reconcile_data_plan(
        clickhouse_connection,
        "primary__0001",
        plan=plan,
        apply=True,
        decision="verified_retry",
    )
    assert reconciled["status"] == "FAILED_RETRYABLE"
    assert (
        execute_data_plan(clickhouse_connection, plan, migration_id="primary__0001")[
            "status"
        ]
        == "APPLIED_SUCCESS"
    )
    assert clickhouse_connection.values == {1}


def test_clickhouse_mutation_must_finish_before_done(clickhouse_connection):
    plan = _clickhouse_plan()
    execute_data_plan(clickhouse_connection, plan, migration_id="primary__0001")
    clickhouse_connection.mutation["is_done"] = 0

    with pytest.raises(RuntimeError, match="outstanding"):
        execute_data_plan(
            clickhouse_connection,
            plan,
            migration_id="primary__0001",
            direction="rollback",
        )

    state = reconcile_data_plan(clickhouse_connection, "primary__0001")
    assert state["status"] == "UNKNOWN_REQUIRES_RECONCILIATION"
    assert (
        next(row for row in state["checkpoints"] if row["direction"] == "rollback")[
            "status"
        ]
        == "PENDING"
    )
    clickhouse_connection.mutation["is_done"] = 1
    assert (
        reconcile_data_plan(
            clickhouse_connection,
            "primary__0001",
            plan=plan,
            apply=True,
            decision="verified_retry",
        )["status"]
        == "FAILED_RETRYABLE"
    )
    assert (
        execute_data_plan(
            clickhouse_connection,
            plan,
            migration_id="primary__0001",
            direction="rollback",
        )["status"]
        == "ROLLED_BACK"
    )


def test_clickhouse_reconciliation_rejects_tampered_chain(clickhouse_connection):
    plan = _clickhouse_plan()
    execute_data_plan(clickhouse_connection, plan, migration_id="primary__0001")
    clickhouse_connection.runs[-1]["status"] = "ABANDONED"

    with pytest.raises(RuntimeError, match="checksum"):
        reconcile_data_plan(clickhouse_connection, "primary__0001")


def test_transactional_sqlite_rollback_then_reapply_uses_new_epoch():
    engine = create_engine("sqlite://")
    with engine.connect() as connection:
        connection.execute(
            text("CREATE TABLE values_table (value INTEGER NOT NULL UNIQUE)")
        )
        connection.commit()
        plan = _plan()
        first = execute_data_plan(connection, plan, migration_id="primary__0001")
        rolled_back = execute_data_plan(
            connection, plan, migration_id="primary__0001", direction="rollback"
        )
        reapplied = execute_data_plan(
            connection, plan, migration_id="primary__0001", reapply=True
        )

        assert first["epoch"] == rolled_back["epoch"]
        assert reapplied["epoch"] == first["epoch"] + 1
        assert _count(connection) == 1
