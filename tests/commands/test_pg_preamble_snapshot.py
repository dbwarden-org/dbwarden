"""Preamble objects must be diffed against the database, not against nothing.

Roles, domains, sequences, functions, triggers and friends are declared in
configuration rather than in models, so they are diffed separately from the
model pass. That pass used a hardcoded empty snapshot, which made every
declared object read as new on every run: the second `migrate` failed with
"role ... already exists", and changing an attribute emitted CREATE instead of
ALTER.

Found by the harness's plugin suite, which walks a role through create, alter,
and undeclare against a live PostgreSQL server.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from dbwarden.commands.make_migrations import pipeline
from dbwarden.engine.core import Op, RunPhase


class _RecordingRoleHandler:
    """Minimal preamble handler that records the snapshot it was given."""

    object_type = "role"
    op_types = ("create_role", "alter_role")
    run_phase = RunPhase.PREAMBLE

    def __init__(self) -> None:
        self.seen_snapshots: list[dict[str, Any]] = []

    def extract(self, snapshot: dict[str, Any]) -> dict[str, Any]:
        self.seen_snapshots.append(snapshot)
        return dict(snapshot.get("roles") or {})

    def model_spec_from_config(self, config: Any) -> dict[str, Any]:
        return {entry["name"]: entry for entry in getattr(config, "pg_roles", []) or []}

    def model_spec_from_tables(self, model_tables: list[Any]) -> dict[str, Any]:
        return {}

    def canonicalize(self, spec: dict[str, Any]) -> dict[str, Any]:
        return dict(spec or {})

    def diff(self, snap_spec, model_spec):
        upgrade: list[Op] = []
        for name, info in (model_spec or {}).items():
            kind = "create_role" if name not in (snap_spec or {}) else "alter_role"
            upgrade.append(
                Op(
                    object_type=kind,
                    upgrade_attrs={"role_name": name, "role_info": info},
                    rollback_attrs={"role_name": name},
                )
            )
        return upgrade, []

    def emit(self, op: Op, db_name: str | None = None, **kwargs: Any):
        from dbwarden.engine.core import MigrationStatement, StatementOrder

        name = op.upgrade_attrs["role_name"]
        verb = "CREATE ROLE" if op.object_type == "create_role" else "ALTER ROLE"
        return [
            MigrationStatement(
                order=StatementOrder.CREATE_EXTENSION,
                upgrade_sql=f"{verb} {name};",
                rollback_sql=f"DROP ROLE IF EXISTS {name};",
            )
        ]


@pytest.fixture
def pg_config(monkeypatch):
    config = SimpleNamespace(
        database_type="postgresql",
        pg_roles=[{"name": "app_reader", "login": True}],
    )
    monkeypatch.setattr(pipeline, "get_database", lambda db_name=None: config)
    monkeypatch.setattr(
        pipeline, "get_multi_db_config", lambda: SimpleNamespace(default="primary"),
    )
    return config


@pytest.fixture
def handler(monkeypatch):
    recording = _RecordingRoleHandler()

    def _register(self):
        self.register(recording)

    monkeypatch.setattr(
        "dbwarden.engine.core.registry.RegistryDriver._register_plugin_handlers",
        _register,
    )
    return recording


def _preamble(snapshot):
    return pipeline._prepend_pg_preamble("", "", [], "primary", snapshot)


class TestPreambleUsesTheSnapshot:
    def test_an_existing_role_is_altered_not_created(self, pg_config, handler):
        upgrade, _, _ = _preamble({"roles": {"app_reader": {"login": True}}})

        assert "ALTER ROLE app_reader" in upgrade
        assert "CREATE ROLE" not in upgrade

    def test_a_new_role_is_still_created(self, pg_config, handler):
        upgrade, _, _ = _preamble({"roles": {}})

        assert "CREATE ROLE app_reader" in upgrade

    def test_the_handler_receives_the_snapshot_it_was_given(self, pg_config, handler):
        _preamble({"roles": {"app_reader": {"login": True}}, "tables": {"reports": {}}})

        seen = handler.seen_snapshots[-1]
        assert seen["roles"] == {"app_reader": {"login": True}}
        assert "reports" in seen["tables"]

    def test_every_preamble_object_kind_is_present_even_for_a_thin_snapshot(
        self, pg_config, handler,
    ):
        """A handler must not KeyError on a snapshot from an older version."""
        _preamble({"roles": {}})

        seen = handler.seen_snapshots[-1]
        for kind in ("domains", "sequences", "functions", "event_triggers"):
            assert kind in seen

    def test_without_a_snapshot_the_object_is_created(self, pg_config, handler):
        """Offline generation cannot see the server, so creating is the only
        honest instruction."""
        upgrade, _, _ = _preamble(None)

        assert "CREATE ROLE app_reader" in upgrade
