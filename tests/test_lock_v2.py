"""Tests for dbwarden.lock — migration locking v2 infrastructure."""
from __future__ import annotations

import time
from types import SimpleNamespace
from unittest.mock import MagicMock, patch, PropertyMock

import pytest

from dbwarden.lock.state import LockState, validate_transition, compute_health, describe_holder
from dbwarden.lock.strategy import (
    StatusRow,
    AcquireResult,
    HolderInfo,
    _derive_lock_key,
    _generate_execution_id,
    _generate_owner_id,
)


class TestLockState:
    """Tests for the recovery state machine."""

    def test_valid_transitions(self):
        assert validate_transition(LockState.AVAILABLE, LockState.RUNNING)
        assert validate_transition(LockState.RUNNING, LockState.COMPLETE)
        assert validate_transition(LockState.RUNNING, LockState.FAILED)
        assert validate_transition(LockState.RUNNING, LockState.DEAD)
        assert validate_transition(LockState.DEAD, LockState.INSPECTING)
        assert validate_transition(LockState.INSPECTING, LockState.COMPLETE)
        assert validate_transition(LockState.INSPECTING, LockState.NEEDS_REVIEW)
        assert validate_transition(LockState.COMPLETE, LockState.AVAILABLE)
        assert validate_transition(LockState.FAILED, LockState.AVAILABLE)
        assert validate_transition(LockState.NEEDS_REVIEW, LockState.AVAILABLE)

    def test_invalid_transitions(self):
        assert not validate_transition(LockState.AVAILABLE, LockState.COMPLETE)
        assert not validate_transition(LockState.AVAILABLE, LockState.DEAD)
        assert not validate_transition(LockState.COMPLETE, LockState.DEAD)
        assert not validate_transition(LockState.DEAD, LockState.AVAILABLE)
        assert not validate_transition(LockState.DEAD, LockState.COMPLETE)

    def test_compute_health_available(self):
        assert compute_health(LockState.AVAILABLE, None) == "AVAILABLE"

    def test_compute_health_complete(self):
        assert compute_health(LockState.COMPLETE, None) == "COMPLETE"

    def test_compute_health_failed(self):
        assert compute_health(LockState.FAILED, None) == "FAILED"

    def test_compute_health_dead(self):
        assert compute_health(LockState.DEAD, None) == "DEAD"

    def test_compute_health_needs_review(self):
        assert compute_health(LockState.NEEDS_REVIEW, None) == "NEEDS_REVIEW"

    def test_compute_health_inspecting(self):
        assert compute_health(LockState.INSPECTING, None) == "INSPECTING"

    def test_compute_health_running_no_heartbeat(self):
        # RUNNING without heartbeat timestamp is still considered HEALTHY
        # (heartbeat staleness is checked at the database level, not here)
        assert compute_health(LockState.RUNNING, None) == "HEALTHY"

    def test_compute_health_running_with_heartbeat(self):
        import time
        from datetime import datetime, timezone
        # Use a recent timestamp (within TTL)
        now = time.time()
        recent = datetime.fromtimestamp(now - 10, tz=timezone.utc).isoformat()
        assert compute_health(LockState.RUNNING, recent) == "HEALTHY"

    def test_compute_health_running_stale_heartbeat(self):
        import time
        from datetime import datetime, timezone
        # Use an old timestamp (beyond TTL)
        now = time.time()
        old = datetime.fromtimestamp(now - 100, tz=timezone.utc).isoformat()
        assert compute_health(LockState.RUNNING, old, heartbeat_ttl_seconds=45) == "STUCK"

    def test_describe_holder_available(self):
        result = describe_holder(LockState.AVAILABLE, None, None, None, None, None, None, None)
        assert result == "No lock held."

    def test_describe_holder_running(self):
        result = describe_holder(
            LockState.RUNNING,
            execution_id="abc123",
            owner_id="def456",
            migration_version="V042",
            host="deploy-7",
            pid=1234,
            acquired_at="2026-08-22 12:00:00",
            last_heartbeat_at="2026-08-22 12:00:15",
        )
        assert "RUNNING" in result
        assert "abc123" in result
        assert "def456" in result
        assert "V042" in result
        assert "deploy-7" in result
        assert "1234" in result


class TestStrategyHelpers:
    """Tests for strategy helper functions."""

    def test_derive_lock_key_deterministic(self):
        key1 = _derive_lock_key("default", "primary")
        key2 = _derive_lock_key("default", "primary")
        assert key1 == key2

    def test_derive_lock_key_different_namespaces(self):
        key1 = _derive_lock_key("default", "primary")
        key2 = _derive_lock_key("staging", "primary")
        assert key1 != key2

    def test_derive_lock_key_64bit(self):
        key = _derive_lock_key("default", "primary")
        assert 0 <= key < 2**64

    def test_generate_execution_id_unique(self):
        id1 = _generate_execution_id()
        id2 = _generate_execution_id()
        assert id1 != id2
        assert len(id1) == 32  # UUID hex

    def test_generate_owner_id_unique(self):
        id1 = _generate_owner_id()
        id2 = _generate_owner_id()
        assert id1 != id2
        assert len(id1) == 32  # UUID hex


class TestStatusRow:
    """Tests for the StatusRow dataclass."""

    def test_defaults(self):
        row = StatusRow()
        assert row.namespace == "default"
        assert row.execution_id == ""
        assert row.owner_id == ""
        assert row.migration_version is None
        assert row.migration_checksum is None
        assert row.fencing_token == 0
        assert row.state == "AVAILABLE"

    def test_custom_values(self):
        row = StatusRow(
            namespace="staging",
            execution_id="abc123",
            owner_id="def456",
            migration_version="V042",
            state="RUNNING",
            fencing_token=5,
        )
        assert row.namespace == "staging"
        assert row.execution_id == "abc123"
        assert row.fencing_token == 5


class TestAcquireResult:
    """Tests for the AcquireResult dataclass."""

    def test_success(self):
        row = StatusRow(execution_id="abc")
        result = AcquireResult(success=True, status_row=row)
        assert result.success is True
        assert result.holder_description == ""
        assert result.error is None

    def test_failure(self):
        row = StatusRow(execution_id="abc")
        result = AcquireResult(
            success=False,
            status_row=row,
            holder_description="Lock held by pid 1234",
            error="timeout",
        )
        assert result.success is False
        assert result.holder_description == "Lock held by pid 1234"
        assert result.error == "timeout"


class TestHolderInfo:
    """Tests for the HolderInfo dataclass."""

    def test_basic(self):
        info = HolderInfo(
            execution_id="abc",
            owner_id="def",
            host="localhost",
            pid=1234,
            migration_version="V042",
            state="RUNNING",
            acquired_at="2026-08-22 12:00:00",
            last_heartbeat_at="2026-08-22 12:00:15",
            is_alive=True,
        )
        assert info.is_alive is True
        assert info.pid == 1234


class TestSQLiteStrategy:
    """Tests for the SQLite lock strategy."""

    def test_ensure_table(self):
        from dbwarden.lock.sqlite import SQLiteStrategy
        strategy = SQLiteStrategy()
        mock_conn = MagicMock()
        strategy.ensure_table(mock_conn)
        mock_conn.execute.assert_called()

    def test_acquire_when_free(self):
        from dbwarden.lock.sqlite import SQLiteStrategy
        strategy = SQLiteStrategy()
        mock_conn = MagicMock()

        # Mock read_status_row to return None (no existing row)
        with patch("dbwarden.lock.sqlite.read_status_row", return_value=None):
            with patch("dbwarden.lock.sqlite.upsert_status_row"):
                row = StatusRow(execution_id="test-exec", owner_id="test-owner")
                result = strategy.acquire(mock_conn, row)
                assert result.success is True

    def test_acquire_when_held_by_alive_process(self):
        from dbwarden.lock.sqlite import SQLiteStrategy
        strategy = SQLiteStrategy()
        mock_conn = MagicMock()

        existing = {
            "state": "RUNNING",
            "pid": 99999,  # Non-existent PID
            "host": "other-host",
            "execution_id": "other-exec",
            "migration_version": "V042",
        }
        with patch("dbwarden.lock.sqlite.read_status_row", return_value=existing):
            with patch("dbwarden.lock.sqlite._is_process_alive", return_value=False):
                row = StatusRow(execution_id="test-exec", owner_id="test-owner")
                result = strategy.acquire(mock_conn, row)
                # Should succeed because holder is dead
                assert result.success is True

    def test_release(self):
        from dbwarden.lock.sqlite import SQLiteStrategy
        strategy = SQLiteStrategy()
        mock_conn = MagicMock()

        with patch("dbwarden.lock.sqlite.update_state"):
            result = strategy.release(mock_conn, "default")
            assert result is True

    def test_describe_holder_none(self):
        from dbwarden.lock.sqlite import SQLiteStrategy
        strategy = SQLiteStrategy()
        mock_conn = MagicMock()

        with patch("dbwarden.lock.sqlite.read_status_row", return_value=None):
            result = strategy.describe_holder(mock_conn)
            assert result is None

    def test_is_alive_false_when_no_pid(self):
        from dbwarden.lock.sqlite import SQLiteStrategy
        strategy = SQLiteStrategy()
        mock_conn = MagicMock()

        with patch("dbwarden.lock.sqlite.read_status_row", return_value={"pid": None}):
            result = strategy.is_alive(mock_conn)
            assert result is False


class TestPostgreSQLStrategy:
    """Tests for the PostgreSQL lock strategy."""

    def test_check_primary_true(self):
        from dbwarden.lock.postgresql import PostgreSQLStrategy
        strategy = PostgreSQLStrategy()
        mock_conn = MagicMock()
        mock_conn.execute.return_value.scalar.return_value = False
        assert strategy._check_primary(mock_conn) is True

    def test_check_primary_false_on_replica(self):
        from dbwarden.lock.postgresql import PostgreSQLStrategy
        strategy = PostgreSQLStrategy()
        mock_conn = MagicMock()
        mock_conn.execute.return_value.scalar.return_value = True
        assert strategy._check_primary(mock_conn) is False

    def test_check_primary_on_exception(self):
        from dbwarden.lock.postgresql import PostgreSQLStrategy
        strategy = PostgreSQLStrategy()
        mock_conn = MagicMock()
        mock_conn.execute.side_effect = Exception("function not found")
        assert strategy._check_primary(mock_conn) is True


class TestMySQLStrategy:
    """Tests for the MySQL lock strategy."""

    def test_check_writable_true(self):
        from dbwarden.lock.mysql import MySQLStrategy
        strategy = MySQLStrategy()
        mock_conn = MagicMock()
        # Mock two separate execute calls
        mock_results = [MagicMock(), MagicMock()]
        mock_results[0].scalar.return_value = 0  # read_only = 0
        mock_results[1].scalar.return_value = 0  # super_read_only = 0
        mock_conn.execute.side_effect = mock_results
        assert strategy._check_writable(mock_conn) is True

    def test_check_writable_false_when_read_only(self):
        from dbwarden.lock.mysql import MySQLStrategy
        strategy = MySQLStrategy()
        mock_conn = MagicMock()
        mock_results = [MagicMock(), MagicMock()]
        mock_results[0].scalar.return_value = 1  # read_only = 1
        mock_results[1].scalar.return_value = 0
        mock_conn.execute.side_effect = mock_results
        assert strategy._check_writable(mock_conn) is False

    def test_mysql_lock_name(self):
        from dbwarden.lock.mysql import _mysql_lock_name
        name = _mysql_lock_name("default", "primary")
        assert name == "dbwarden.default.primary"
        assert len(name) <= 64


class TestClickHouseIdempotency:
    """Tests for ClickHouse CH-1 idempotency enforcement."""

    def test_idempotent_create_table(self):
        from dbwarden.lock.clickhouse import check_statement_idempotency
        is_idempotent, reason = check_statement_idempotency(
            "CREATE TABLE IF NOT EXISTS test (id Int32) ENGINE = MergeTree() ORDER BY id"
        )
        assert is_idempotent is True
        assert reason is None

    def test_idempotent_drop_table(self):
        from dbwarden.lock.clickhouse import check_statement_idempotency
        is_idempotent, reason = check_statement_idempotency("DROP TABLE IF EXISTS test")
        assert is_idempotent is True

    def test_idempotent_add_column(self):
        from dbwarden.lock.clickhouse import check_statement_idempotency
        is_idempotent, reason = check_statement_idempotency(
            "ALTER TABLE test ADD COLUMN IF NOT EXISTS name String"
        )
        assert is_idempotent is True

    def test_idempotent_drop_column(self):
        from dbwarden.lock.clickhouse import check_statement_idempotency
        is_idempotent, reason = check_statement_idempotency(
            "ALTER TABLE test DROP COLUMN IF EXISTS name"
        )
        assert is_idempotent is True

    def test_non_idempotent_rename(self):
        from dbwarden.lock.clickhouse import check_statement_idempotency
        is_idempotent, reason = check_statement_idempotency(
            "ALTER TABLE test RENAME TO new_test"
        )
        assert is_idempotent is False
        assert "Non-idempotent" in reason

    def test_non_idempotent_modify_column(self):
        from dbwarden.lock.clickhouse import check_statement_idempotency
        is_idempotent, reason = check_statement_idempotency(
            "ALTER TABLE test MODIFY COLUMN name String DEFAULT 'unknown'"
        )
        assert is_idempotent is False

    def test_non_idempotent_update(self):
        from dbwarden.lock.clickhouse import check_statement_idempotency
        is_idempotent, reason = check_statement_idempotency(
            "UPDATE test SET name = 'test' WHERE id = 1"
        )
        assert is_idempotent is False

    def test_non_idempotent_delete(self):
        from dbwarden.lock.clickhouse import check_statement_idempotency
        is_idempotent, reason = check_statement_idempotency("DELETE FROM test WHERE id = 1")
        assert is_idempotent is False

    def test_comment_is_idempotent(self):
        from dbwarden.lock.clickhouse import check_statement_idempotency
        is_idempotent, reason = check_statement_idempotency("-- this is a comment")
        assert is_idempotent is True

    def test_empty_is_idempotent(self):
        from dbwarden.lock.clickhouse import check_statement_idempotency
        is_idempotent, reason = check_statement_idempotency("")
        assert is_idempotent is True

    def test_unknown_statement_non_idempotent(self):
        from dbwarden.lock.clickhouse import check_statement_idempotency
        is_idempotent, reason = check_statement_idempotency("SOME UNKNOWN STATEMENT")
        assert is_idempotent is False
        assert "Unclassified" in reason


class _FakeResult:
    """Minimal result facade mimicking SQLAlchemy's Result API surface."""

    def __init__(self, scalar=None, first=None, rows=None):
        self._scalar = scalar
        self._first = first
        self._rows = rows or []

    def scalar(self):
        return self._scalar

    def mappings(self):
        return self

    def first(self):
        return self._first

    def fetchall(self):
        return self._rows


class FakeCHConnection:
    """Fake ClickHouse connection for SQL-generation/logic tests.

    Records every executed statement and returns canned results for
    engine detection, now(), and status-row reads.
    """

    def __init__(
        self,
        engine="ReplacingMergeTree(fencing_token)",
        status_row=None,
        now="2026-10-09T12:00:00+00:00",
    ):
        self.dialect = SimpleNamespace(name="clickhouse")
        self._engine = engine
        self._status_row = status_row
        self._now = now
        self.statements = []
        self.fail_on_rename = False

    def execute(self, stmt, params=None):
        sql = str(stmt)
        self.statements.append(sql)
        if "SELECT engine FROM system.tables" in sql:
            return _FakeResult(scalar=self._engine)
        if sql.strip().startswith("SELECT now()"):
            return _FakeResult(scalar=self._now)
        if self.fail_on_rename and "RENAME TABLE" in sql:
            raise RuntimeError("simulated RENAME failure")
        # Status-row reads (FINAL or argMax flavor) and INSERTs.
        return _FakeResult(first=self._status_row)

    def sql_text(self):
        return "\n".join(self.statements)


class TestClickHouseLockEngine:
    """dbwarden_lock must be ReplacingMergeTree; plain-MergeTree tables
    in the wild must be migrated and read with argMax semantics."""

    def test_ddl_uses_replacing_merge_tree(self):
        from dbwarden.lock.table import _LOCK_TABLE_V2_DDL

        ddl = _LOCK_TABLE_V2_DDL["clickhouse"]
        assert "ENGINE = ReplacingMergeTree(fencing_token)" in ddl
        assert "MergeTree()" not in ddl.replace("ReplacingMergeTree(fencing_token)", "")

    def test_ensure_skips_migration_when_already_replacing(self):
        from dbwarden.lock.table import ensure_lock_table

        conn = FakeCHConnection(engine="ReplacingMergeTree(fencing_token)")
        ensure_lock_table(conn, "clickhouse")
        assert "RENAME TABLE" not in conn.sql_text()
        assert "CREATE TABLE IF NOT EXISTS dbwarden_lock" in conn.sql_text()

    def test_ensure_migrates_plain_mergine_tree(self):
        from dbwarden.lock.table import ensure_lock_table

        conn = FakeCHConnection(engine="MergeTree")
        ensure_lock_table(conn, "clickhouse")

        sql = conn.sql_text()
        # Migration table created with the ReplacingMergeTree DDL
        assert "CREATE TABLE IF NOT EXISTS dbwarden_lock_migration" in sql
        assert "ENGINE = ReplacingMergeTree(fencing_token)" in sql
        # Authoritative rows preserved via argMax dedup by fencing_token
        insert_idx = sql.index("INSERT INTO dbwarden_lock_migration")
        assert "argMax(execution_id, fencing_token)" in sql[insert_idx:]
        assert "max(fencing_token)" in sql[insert_idx:]
        # Atomic swap and cleanup of the legacy table
        assert "RENAME TABLE dbwarden_lock TO dbwarden_lock_legacy" in sql
        assert "DROP TABLE dbwarden_lock_legacy" in sql
        # Engine cache updated: a second ensure does not re-migrate
        conn.statements.clear()
        ensure_lock_table(conn, "clickhouse")
        assert "RENAME TABLE" not in conn.sql_text()

    def test_ensure_survives_migration_failure_and_reads_stay_correct(self):
        from dbwarden.lock.table import ensure_lock_table, read_status_row

        conn = FakeCHConnection(
            engine="MergeTree",
            status_row={"namespace": "default", "fencing_token": 3},
        )
        conn.fail_on_rename = True
        # Migration failure must not break ensure (retried next time)
        ensure_lock_table(conn, "clickhouse")
        # Reads fall back to the engine-agnostic argMax query
        conn.statements.clear()
        row = read_status_row(conn, namespace="default", db_type="clickhouse")
        sql = conn.sql_text()
        assert "argMax" in sql
        assert " FINAL" not in sql
        assert row["fencing_token"] == 3

    def test_read_uses_final_only_for_replacing_engine(self):
        from dbwarden.lock.table import read_status_row

        replacing = FakeCHConnection(
            engine="ReplacingMergeTree(fencing_token)",
            status_row={"namespace": "default", "fencing_token": 7},
        )
        read_status_row(replacing, namespace="default", db_type="clickhouse")
        assert " FINAL" in replacing.sql_text()
        assert "argMax" not in replacing.sql_text()

        plain = FakeCHConnection(
            engine="MergeTree",
            status_row={"namespace": "default", "fencing_token": 7},
        )
        read_status_row(plain, namespace="default", db_type="clickhouse")
        assert " FINAL" not in plain.sql_text()
        assert "argMax(execution_id, fencing_token)" in plain.sql_text()

    def test_read_unknown_engine_uses_argmax_not_final(self):
        from dbwarden.lock.table import read_status_row

        conn = FakeCHConnection(
            engine=None, status_row={"namespace": "default", "fencing_token": 1}
        )
        read_status_row(conn, namespace="default", db_type="clickhouse")
        assert " FINAL" not in conn.sql_text()
        assert "argMax" in conn.sql_text()


class TestClickHouseAcquisition:
    """CH-0 lease acquisition: no FINAL in generated SQL, competing
    holders rejected, stale fencing tokens refused."""

    def _row(self, **overrides):
        row = {
            "namespace": "default",
            "execution_id": "exec-1",
            "owner_id": "owner-1",
            "fencing_token": 5,
            "state": "RUNNING",
            "expires_at": "2026-10-09T12:05:00+00:00",
        }
        row.update(overrides)
        return row

    def test_acquire_sql_contains_no_final(self):
        from dbwarden.lock.clickhouse import ClickHouseStrategy

        conn = FakeCHConnection(status_row=self._row())
        strategy = ClickHouseStrategy(ttl_seconds=120)
        status_row = StatusRow(namespace="default", execution_id="exec-1")
        result = strategy.acquire(conn, status_row)
        assert result.success
        # The acquisition INSERT itself must not use FINAL; the post-insert
        # verify read may (engine is ReplacingMergeTree here).
        insert_sql = next(s for s in conn.statements if s.lstrip().startswith("INSERT"))
        assert " FINAL" not in insert_sql
        # ensure_table ran: DDL issued, engine detected
        assert "CREATE TABLE IF NOT EXISTS dbwarden_lock" in conn.sql_text()
        assert status_row.fencing_token == 5

    def test_acquire_rejects_competing_holder(self):
        from dbwarden.lock.clickhouse import ClickHouseStrategy

        conn = FakeCHConnection(
            status_row=self._row(execution_id="other-exec", owner_id="other-owner")
        )
        strategy = ClickHouseStrategy(ttl_seconds=120)
        status_row = StatusRow(namespace="default", execution_id="exec-1")
        result = strategy.acquire(conn, status_row)
        assert not result.success
        assert "other-exec"[:12] in result.holder_description or "host" in result.holder_description

    def test_acquire_rejects_expired_lease_holder(self):
        from dbwarden.lock.clickhouse import ClickHouseStrategy

        conn = FakeCHConnection(
            status_row=self._row(
                execution_id="other-exec",
                expires_at="2026-10-09T11:00:00+00:00",  # before now()
            )
        )
        strategy = ClickHouseStrategy(ttl_seconds=120)
        status_row = StatusRow(namespace="default", execution_id="exec-1")
        result = strategy.acquire(conn, status_row)
        assert not result.success

    def test_fence_check_rejects_stale_token(self):
        from dbwarden.lock.clickhouse import ClickHouseStrategy

        strategy = ClickHouseStrategy()
        conn = FakeCHConnection(status_row=self._row(fencing_token=9))
        assert strategy.fence_check(conn, "default", 9) is True
        assert strategy.fence_check(conn, "default", 8) is False

    def test_fence_check_rejects_expired_lease(self):
        from dbwarden.lock.clickhouse import ClickHouseStrategy

        conn = FakeCHConnection(
            status_row=self._row(expires_at="2026-10-09T11:00:00+00:00")
        )
        strategy = ClickHouseStrategy()
        assert strategy.fence_check(conn, "default", 5) is False

    def test_acquire_migrates_legacy_plain_mergine_tree(self):
        from dbwarden.lock.clickhouse import ClickHouseStrategy

        conn = FakeCHConnection(engine="MergeTree", status_row=self._row())
        strategy = ClickHouseStrategy(ttl_seconds=120)
        result = strategy.acquire(conn, StatusRow(namespace="default", execution_id="exec-1"))
        assert result.success
        sql = conn.sql_text()
        assert "RENAME TABLE dbwarden_lock TO dbwarden_lock_legacy" in sql
        insert_sql = next(s for s in conn.statements if s.lstrip().startswith("INSERT INTO dbwarden_lock\n"))
        assert " FINAL" not in insert_sql
        # Migration dedup copy used argMax semantics
        copy_sql = next(s for s in conn.statements if "INSERT INTO dbwarden_lock_migration" in s)
        assert "argMax(execution_id, fencing_token)" in copy_sql

    def test_release_and_termination_paths_engine_agnostic(self):
        from dbwarden.lock.clickhouse import ClickHouseStrategy

        conn = FakeCHConnection(status_row=self._row())
        strategy = ClickHouseStrategy()
        assert strategy.release(conn, "default") is True
        assert "ALTER TABLE dbwarden_lock UPDATE" in conn.sql_text()
