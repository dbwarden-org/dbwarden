from __future__ import annotations

import base64
import json
import sys
import urllib.error
from contextlib import contextmanager
from types import SimpleNamespace
from pathlib import Path

import pytest
from typer.testing import CliRunner

from dbwarden._official import OFFICIAL_PLUGINS, OfficialSpec, classify
from dbwarden.cli.main import app
from dbwarden.engine.core import (
    Anchor,
    MigrationStatement,
    OrderingConstraint,
    OrderingError,
    RegistryDriver,
    RunPhase,
)
from dbwarden.engine.core.statement_order import StatementOrder
from dbwarden.engine.snapshot.sql_gen import snapshot_diff_to_sql
from dbwarden.plugin import (
    HookConflictError,
    HookNotRegisteredError,
    HookRegistry,
    ObjectHandlerConflictError,
    ObjectPluginRegistry,
    PluginInstallError,
    PluginLockEntry,
    PluginRegistrar,
    ProvenanceResult,
    add_plugin,
    verified_allows,
    consent_allows,
    load_plugins,
    load_plugin_lock,
    plugin_reports,
    record_consent,
    remove_plugin,
    remove_plugin_lock_entry,
    revoke_consent,
    verify_official_provenance,
    write_plugin_lock_entry,
)
from dbwarden.plugin import _resolve_target_version


class FakeDist:
    def __init__(self, name: str, version: str) -> None:
        self.name = name
        self.version = version


class FakeEntryPoint:
    def __init__(self, dist_name: str, version: str, setup) -> None:
        self.name = "plugin"
        self.value = "fake:setup"
        self.dist = FakeDist(dist_name, version)
        self._setup = setup

    def load(self):
        return self._setup


@pytest.fixture(autouse=True)
def clear_plugin_state(monkeypatch, tmp_path):
    HookRegistry.clear()
    ObjectPluginRegistry.clear()
    monkeypatch.setattr("dbwarden.plugin.CONSENT_PATH", tmp_path / "consent.toml")
    monkeypatch.setattr("dbwarden.plugin.LOCK_PATH", tmp_path / "plugins.lock")
    monkeypatch.setattr("dbwarden.plugin._LOAD_STATES", {})
    monkeypatch.setattr("dbwarden.plugin._LOAD_ERRORS", {})
    monkeypatch.setattr("dbwarden.plugin._DISCOVERED", {})


def test_classify_marks_known_plugins_official() -> None:
    expected = {
        "dbwarden-ch-rbac",
        "dbwarden-fastapi",
        "dbwarden-pgsql-extensions",
        "dbwarden-pgsql-rbac",
        "dbwarden-pgsql-types",
        "dbwarden-sandbox",
        "dbwarden-seeds",
    }
    assert set(OFFICIAL_PLUGINS) == expected
    for dist_name in expected:
        assert classify(dist_name) == "official"
    assert classify("dbwarden-acme") == "community"


def test_official_plugin_workflows_match_their_publishers() -> None:
    assert {
        name: spec.workflow for name, spec in OFFICIAL_PLUGINS.items()
    } == {
        "dbwarden-ch-rbac": "publishing.yml",
        "dbwarden-fastapi": "publishing.yml",
        "dbwarden-pgsql-extensions": "publishing.yml",
        "dbwarden-pgsql-rbac": "publishing.yml",
        "dbwarden-pgsql-types": "publishing.yml",
        "dbwarden-sandbox": "publishing.yml",
        "dbwarden-seeds": "publishing.yml",
    }


def test_classify_marks_verified_plugins_after_official(monkeypatch) -> None:
    from dbwarden._verified import VERIFIED_PLUGINS

    VERIFIED_PLUGINS["dbwarden-verified"] = "0.2.0"
    VERIFIED_PLUGINS["dbwarden-pgsql-extensions"] = "999.0.0"
    try:
        assert classify("dbwarden-verified") == "verified"
        assert classify("dbwarden-pgsql-extensions") == "official"
    finally:
        VERIFIED_PLUGINS.pop("dbwarden-verified", None)
        VERIFIED_PLUGINS.pop("dbwarden-pgsql-extensions", None)


def test_verified_allows_uses_version_floor() -> None:
    from dbwarden._verified import VERIFIED_PLUGINS

    VERIFIED_PLUGINS["dbwarden-verified"] = "0.2.0"
    try:
        assert verified_allows("dbwarden-verified", "0.2.0") is True
        assert verified_allows("dbwarden-verified", "0.3.0") is True
        assert verified_allows("dbwarden-verified", "0.1.9") is False
        assert verified_allows("dbwarden-verified", "not-a-version") is False
        assert verified_allows("dbwarden-community", "1.0.0") is False
    finally:
        VERIFIED_PLUGINS.pop("dbwarden-verified", None)


def test_registrar_registers_value_hooks() -> None:
    registrar = PluginRegistrar("plugin-a")
    registrar.register("load_model_module", lambda: "models")

    assert HookRegistry.is_registered("load_model_module") is True
    assert HookRegistry.providers("load_model_module") == ["plugin-a"]
    assert HookRegistry.execute_single("load_model_module") == "models"


def test_single_hook_reports_missing_and_conflicts() -> None:
    with pytest.raises(HookNotRegisteredError):
        HookRegistry.execute_single("load_model_module")

    HookRegistry.register("load_model_module", lambda: None, plugin="plugin-a")
    HookRegistry.register("load_model_module", lambda: None, plugin="plugin-b")

    with pytest.raises(HookConflictError):
        HookRegistry.execute_single("load_model_module")


def test_registrar_rejects_unknown_value_hooks() -> None:
    with pytest.raises(ValueError, match="Unknown hook"):
        PluginRegistrar("plugin-a").register("not_a_hook", lambda: None)


def test_registrar_registers_object_handlers() -> None:
    class Handler:
        object_type = "policy"

    PluginRegistrar("plugin-a").register_object_handler(Handler())

    handlers = ObjectPluginRegistry.handlers()
    assert handlers["policy"].plugin == "plugin-a"
    assert isinstance(handlers["policy"].handler, Handler)


def test_object_handler_conflict() -> None:
    class Handler:
        object_type = "policy"

    PluginRegistrar("plugin-a").register_object_handler(Handler())

    with pytest.raises(ObjectHandlerConflictError):
        PluginRegistrar("plugin-b").register_object_handler(Handler())


def test_consent_round_trip(tmp_path) -> None:
    consent_path = tmp_path / "consent.toml"

    assert consent_allows("dbwarden-acme", "1.0.0", path=consent_path) is False

    record_consent("dbwarden-acme", "1.0.0", path=consent_path)
    assert consent_allows("dbwarden-acme", "1.0.0", path=consent_path) is True
    assert consent_allows("dbwarden-acme", "2.0.0", path=consent_path) is False

    assert revoke_consent("dbwarden-acme", path=consent_path) is True
    assert consent_allows("dbwarden-acme", "1.0.0", path=consent_path) is False


def test_load_plugins_loads_official_without_consent(monkeypatch) -> None:
    def setup(registrar):
        registrar.register("load_model_module", lambda: "models")

    ep = FakeEntryPoint("dbwarden-pgsql-extensions", "1.0.0", setup)
    monkeypatch.setattr("dbwarden.plugin.iter_plugin_entry_points", lambda: [ep])

    load_plugins()

    assert HookRegistry.execute_single("load_model_module") == "models"


def test_load_plugins_skips_untrusted_community(monkeypatch) -> None:
    def setup(registrar):
        registrar.register("load_model_module", lambda: "models")

    ep = FakeEntryPoint("dbwarden-acme", "1.0.0", setup)
    monkeypatch.setattr("dbwarden.plugin.iter_plugin_entry_points", lambda: [ep])

    load_plugins()

    assert HookRegistry.is_registered("load_model_module") is False


def test_load_plugins_loads_trusted_community(monkeypatch, tmp_path) -> None:
    def setup(registrar):
        registrar.register("load_model_module", lambda: "models")

    ep = FakeEntryPoint("dbwarden-acme", "1.0.0", setup)
    monkeypatch.setattr("dbwarden.plugin.iter_plugin_entry_points", lambda: [ep])
    monkeypatch.setattr("dbwarden.plugin.CONSENT_PATH", tmp_path / "consent.toml")
    record_consent("dbwarden-acme", "1.0.0")

    load_plugins()

    assert HookRegistry.execute_single("load_model_module") == "models"


def test_load_plugins_loads_verified_without_consent(monkeypatch) -> None:
    from dbwarden._verified import VERIFIED_PLUGINS

    def setup(registrar):
        registrar.register("load_model_module", lambda: "models")

    VERIFIED_PLUGINS["dbwarden-verified"] = "0.2.0"
    ep = FakeEntryPoint("dbwarden-verified", "0.2.0", setup)
    monkeypatch.setattr("dbwarden.plugin.iter_plugin_entry_points", lambda: [ep])
    try:
        load_plugins()
        assert HookRegistry.execute_single("load_model_module") == "models"
    finally:
        VERIFIED_PLUGINS.pop("dbwarden-verified", None)


def test_load_plugins_treats_outdated_verified_as_community(monkeypatch) -> None:
    from dbwarden._verified import VERIFIED_PLUGINS

    def setup(registrar):
        registrar.register("load_model_module", lambda: "models")

    VERIFIED_PLUGINS["dbwarden-verified"] = "0.2.0"
    ep = FakeEntryPoint("dbwarden-verified", "0.1.0", setup)
    monkeypatch.setattr("dbwarden.plugin.iter_plugin_entry_points", lambda: [ep])
    try:
        load_plugins()
        assert HookRegistry.is_registered("load_model_module") is False
        record_consent("dbwarden-verified", "0.1.0")
        load_plugins()
        assert HookRegistry.execute_single("load_model_module") == "models"
    finally:
        VERIFIED_PLUGINS.pop("dbwarden-verified", None)


def test_plugin_list_cli(monkeypatch) -> None:
    ep = FakeEntryPoint("dbwarden-acme", "1.0.0", lambda registrar: None)
    monkeypatch.setattr("dbwarden.plugin.iter_plugin_entry_points", lambda: [ep])
    monkeypatch.setattr("dbwarden.commands.plugin_cmd.iter_plugin_entry_points", lambda: [ep])

    result = CliRunner().invoke(app, ["plugin", "list"])

    assert result.exit_code == 0
    assert "dbwarden-acme" in result.output
    assert "community" in result.output


def test_plugin_list_cli_displays_verified_tier(monkeypatch) -> None:
    from dbwarden._verified import VERIFIED_PLUGINS

    VERIFIED_PLUGINS["dbwarden-verified"] = "0.2.0"
    ep = FakeEntryPoint("dbwarden-verified", "0.2.0", lambda registrar: None)
    monkeypatch.setattr("dbwarden.plugin.iter_plugin_entry_points", lambda: [ep])
    try:
        result = CliRunner().invoke(app, ["plugin", "list"])
        assert result.exit_code == 0
        assert "dbwarden-verified" in result.output
        assert "verified" in result.output
    finally:
        VERIFIED_PLUGINS.pop("dbwarden-verified", None)


def test_plugin_info_cli_displays_verified_minimum(monkeypatch) -> None:
    from dbwarden._verified import VERIFIED_PLUGINS

    VERIFIED_PLUGINS["dbwarden-verified"] = "0.2.0"
    ep = FakeEntryPoint("dbwarden-verified", "0.2.0", lambda registrar: None)
    monkeypatch.setattr("dbwarden.plugin.iter_plugin_entry_points", lambda: [ep])
    try:
        result = CliRunner().invoke(app, ["plugin", "info", "dbwarden-verified"])
        assert result.exit_code == 0
        assert "verified" in result.output
        assert "0.2.0" in result.output
    finally:
        VERIFIED_PLUGINS.pop("dbwarden-verified", None)


def test_plugin_object_handler_participates_in_registry_run() -> None:
    class Handler:
        object_type = "pg_extension"
        op_types = ("create_pg_extension",)
        run_phase = RunPhase.PREAMBLE
        ordering = OrderingConstraint(after=(Anchor.PREAMBLE,), before=(Anchor.BEFORE_TABLES,))

        def extract(self, snapshot):
            return snapshot.get("pg_extensions", {})

        def model_spec_from_config(self, config):
            return {name: {} for name in config.pg_extensions}

        def model_spec_from_tables(self, model_tables):
            return {}

        def canonicalize(self, spec):
            return spec

        def diff(self, snap_spec, model_spec):
            from dbwarden.engine.core import Op

            ops = [
                Op("create_pg_extension", {"name": name}, {"name": name})
                for name in sorted(set(model_spec) - set(snap_spec))
            ]
            return ops, []

        def emit(self, op, db_name=None, **kwargs):
            return [
                MigrationStatement(
                    order=self.statement_order,
                    upgrade_sql=f'CREATE EXTENSION IF NOT EXISTS "{op.upgrade_attrs["name"]}";',
                    rollback_sql=f'DROP EXTENSION IF EXISTS "{op.upgrade_attrs["name"]}";',
                )
            ]

    class Config:
        pg_extensions = ["btree_gist"]

    PluginRegistrar("plugin-a").register_object_handler(Handler())

    up_ops, rb_ops = RegistryDriver().run({"pg_extensions": {}}, [], Config())

    assert rb_ops == []
    assert len(up_ops) == 1
    assert up_ops[0].object_type == "create_pg_extension"


def test_plugin_object_handler_public_anchor_sets_statement_order() -> None:
    class Handler:
        object_type = "late_object"
        run_phase = RunPhase.DIFF
        ordering = OrderingConstraint(after=(Anchor.AFTER_INDEXES,), before=(Anchor.POSTAMBLE,))

        def extract(self, snapshot):
            return {}

        def model_spec_from_config(self, config):
            return {}

        def model_spec_from_tables(self, model_tables):
            return {"late": {}}

        def canonicalize(self, spec):
            return spec

        def diff(self, snap_spec, model_spec):
            return [], []

        def emit(self, op, db_name=None, **kwargs):
            return []

    handler = Handler()
    PluginRegistrar("plugin-a").register_object_handler(handler)

    RegistryDriver()

    assert handler.statement_order == StatementOrder.ALTER_INDEX


def test_plugin_object_handler_emits_from_dict_sql_path() -> None:
    class Handler:
        object_type = "pg_extension"
        op_types = ("create_pg_extension",)
        run_phase = RunPhase.PREAMBLE
        ordering = OrderingConstraint(after=(Anchor.PREAMBLE,), before=(Anchor.BEFORE_TABLES,))

        def emit(self, op, db_name=None, **kwargs):
            return [
                MigrationStatement(
                    order=self.statement_order,
                    upgrade_sql=f'CREATE EXTENSION IF NOT EXISTS "{op.upgrade_attrs["name"]}";',
                    rollback_sql=f'DROP EXTENSION IF EXISTS "{op.upgrade_attrs["name"]}";',
                )
            ]

    handler = Handler()
    PluginRegistrar("plugin-a").register_object_handler(handler)

    upgrade_sql, rollback_sql, changes = snapshot_diff_to_sql(
        [{"type": "create_pg_extension", "name": "btree_gist"}],
        [],
    )

    assert 'CREATE EXTENSION IF NOT EXISTS "btree_gist";' in upgrade_sql
    assert 'DROP EXTENSION IF EXISTS "btree_gist";' in rollback_sql
    assert changes[0].operation == "create_pg_extension"


def test_plugin_object_to_object_ordering_controls_run_order() -> None:
    seen: list[str] = []

    class FirstHandler:
        object_type = "first"
        run_phase = RunPhase.DIFF

        def extract(self, snapshot):
            return {}

        def model_spec_from_config(self, config):
            return {}

        def model_spec_from_tables(self, model_tables):
            return {}

        def canonicalize(self, spec):
            return spec

        def diff(self, snap_spec, model_spec):
            seen.append("first")
            return [], []

        def emit(self, op, db_name=None, **kwargs):
            return []

    class SecondHandler(FirstHandler):
        object_type = "second"
        ordering = OrderingConstraint(after_object=("first",))

        def diff(self, snap_spec, model_spec):
            seen.append("second")
            return [], []

    PluginRegistrar("plugin-a").register_object_handler(SecondHandler())
    PluginRegistrar("plugin-b").register_object_handler(FirstHandler())

    RegistryDriver().run({}, [], None)

    assert seen == ["first", "second"]


def test_plugin_object_ordering_rejects_unknown_object_reference() -> None:
    class Handler:
        object_type = "second"
        run_phase = RunPhase.DIFF
        ordering = OrderingConstraint(after_object=("missing",))

        def extract(self, snapshot):
            return {}

        def model_spec_from_config(self, config):
            return {}

        def model_spec_from_tables(self, model_tables):
            return {}

        def canonicalize(self, spec):
            return spec

        def diff(self, snap_spec, model_spec):
            return [], []

        def emit(self, op, db_name=None, **kwargs):
            return []

    PluginRegistrar("plugin-a").register_object_handler(Handler())

    with pytest.raises(OrderingError, match="Unknown object ordering reference"):
        RegistryDriver().run({}, [], None)


def test_plugin_object_ordering_rejects_cycles() -> None:
    class FirstHandler:
        object_type = "first"
        run_phase = RunPhase.DIFF
        ordering = OrderingConstraint(after_object=("second",))

        def extract(self, snapshot):
            return {}

        def model_spec_from_config(self, config):
            return {}

        def model_spec_from_tables(self, model_tables):
            return {}

        def canonicalize(self, spec):
            return spec

        def diff(self, snap_spec, model_spec):
            return [], []

        def emit(self, op, db_name=None, **kwargs):
            return []

    class SecondHandler(FirstHandler):
        object_type = "second"
        ordering = OrderingConstraint(after_object=("first",))

    PluginRegistrar("plugin-a").register_object_handler(FirstHandler())
    PluginRegistrar("plugin-b").register_object_handler(SecondHandler())

    with pytest.raises(OrderingError, match="cycle detected"):
        RegistryDriver().run({}, [], None)


def test_plugin_anchor_ordering_rejects_impossible_pair() -> None:
    class Handler:
        object_type = "bad"
        run_phase = RunPhase.DIFF
        ordering = OrderingConstraint(after=(Anchor.POSTAMBLE,), before=(Anchor.PREAMBLE,))

        def extract(self, snapshot):
            return {}

        def model_spec_from_config(self, config):
            return {}

        def model_spec_from_tables(self, model_tables):
            return {}

        def canonicalize(self, spec):
            return spec

        def diff(self, snap_spec, model_spec):
            return [], []

        def emit(self, op, db_name=None, **kwargs):
            return []

    with pytest.raises(OrderingError, match="Impossible ordering"):
        PluginRegistrar("plugin-a").register_object_handler(Handler())


def test_plugin_lockfile_round_trip(tmp_path) -> None:
    lock_path = tmp_path / "plugins.lock"
    entry = PluginLockEntry(
        distribution="dbwarden-pgsql-extensions",
        version="1.0.0",
        filename="dbwarden_plugin_extensions-1.0.0-py3-none-any.whl",
        sha256="abc123",
        tier="official",
        verified="provenance",
        identity="https://github.com/dbwarden/dbwarden-pgsql-extensions",
        installed_at="2026-07-13T10:30:00Z",
    )

    write_plugin_lock_entry("extensions", entry, path=lock_path)

    assert load_plugin_lock(lock_path)["extensions"] == entry
    assert remove_plugin_lock_entry("extensions", path=lock_path) is True
    assert load_plugin_lock(lock_path) == {}


def _write_verified_lock(dist_name: str) -> PluginLockEntry:
    entry = PluginLockEntry(
        distribution=dist_name,
        version="0.1.0",
        filename=f"{dist_name.replace('-', '_')}-0.1.0-py3-none-any.whl",
        sha256="abc123",
        tier="official",
        verified="provenance",
        identity=f"https://github.com/dbwarden/{dist_name}",
        installed_at="2026-07-23T10:00:00Z",
    )
    write_plugin_lock_entry(dist_name.removeprefix("dbwarden-").replace("-", "_"), entry)
    return entry


def test_plugin_report_surfaces_verified_lock_status(monkeypatch) -> None:
    ep = FakeEntryPoint("dbwarden-fastapi", "0.1.0", lambda registrar: None)
    monkeypatch.setattr("dbwarden.plugin.iter_plugin_entry_points", lambda: [ep])
    _write_verified_lock("dbwarden-fastapi")

    reports = [r for r in plugin_reports() if r.distribution == "dbwarden-fastapi"]

    assert len(reports) == 1
    report = reports[0]
    assert report.tier == "official"
    assert report.trusted is True
    assert report.lock is not None
    assert report.lock.verified == "provenance"
    assert report.lock.identity == "https://github.com/dbwarden/dbwarden-fastapi"


def test_plugin_report_verified_absent_without_lock(monkeypatch) -> None:
    ep = FakeEntryPoint("dbwarden-fastapi", "0.1.0", lambda registrar: None)
    monkeypatch.setattr("dbwarden.plugin.iter_plugin_entry_points", lambda: [ep])

    report = next(r for r in plugin_reports() if r.distribution == "dbwarden-fastapi")

    assert report.lock is None


def test_plugin_info_cli_shows_verified_status(monkeypatch) -> None:
    ep = FakeEntryPoint("dbwarden-fastapi", "0.1.0", lambda registrar: None)
    monkeypatch.setattr("dbwarden.plugin.iter_plugin_entry_points", lambda: [ep])
    _write_verified_lock("dbwarden-fastapi")

    result = CliRunner().invoke(app, ["plugin", "info", "dbwarden-fastapi"])

    assert result.exit_code == 0
    assert "provenance" in result.output


def test_plugin_list_json_reports_verified_lock(monkeypatch) -> None:
    import json as _json

    ep = FakeEntryPoint("dbwarden-fastapi", "0.1.0", lambda registrar: None)
    monkeypatch.setattr("dbwarden.plugin.iter_plugin_entry_points", lambda: [ep])
    _write_verified_lock("dbwarden-fastapi")

    result = CliRunner().invoke(app, ["plugin", "list", "--format", "json"])

    assert result.exit_code == 0
    payload = _json.loads(result.output)
    entry = next(p for p in payload if p["distribution"] == "dbwarden-fastapi")
    assert entry["lock"]["verified"] == "provenance"


EXT = "dbwarden-pgsql-extensions"
EXT_SPEC = OFFICIAL_PLUGINS[EXT]


def _make_project(project: str, version: str, sha256: str) -> dict:
    return {
        "releases": {
            version: [
                {
                    "filename": f"{project.replace('-', '_')}-{version}-py3-none-any.whl",
                    "packagetype": "bdist_wheel",
                    "digests": {"sha256": sha256},
                }
            ]
        }
    }


def _make_provenance(repository: str, workflow: str, sha256: str, *, kind: str = "GitHub") -> dict:
    statement = base64.b64encode(
        json.dumps(
            {
                "_type": "https://in-toto.io/Statement/v1",
                "subject": [{"name": "dist.whl", "digest": {"sha256": sha256}}],
            }
        ).encode("utf-8")
    ).decode("ascii")
    return {
        "attestation_bundles": [
            {
                "publisher": {"kind": kind, "repository": repository, "workflow": workflow},
                "attestations": [{"envelope": {"statement": statement, "signature": "sig"}}],
            }
        ]
    }


def _patch_pypi(monkeypatch, project_json, provenance_json) -> None:
    monkeypatch.setattr("dbwarden.plugin._pypi_project_json", lambda project: project_json)
    monkeypatch.setattr(
        "dbwarden.plugin._pypi_provenance", lambda project, version, filename: provenance_json
    )


def test_verify_provenance_succeeds_for_matching_publisher(monkeypatch) -> None:
    sha = "a" * 64
    _patch_pypi(
        monkeypatch,
        _make_project(EXT, "0.3.0", sha),
        _make_provenance(EXT_SPEC.repo_slug, EXT_SPEC.workflow, sha),
    )

    result = verify_official_provenance(EXT, EXT_SPEC)

    assert result.verified is True
    assert result.version == "0.3.0"
    assert result.sha256 == sha
    assert result.filename == "dbwarden_pgsql_extensions-0.3.0-py3-none-any.whl"
    assert result.identity == EXT_SPEC.repository


def test_verify_provenance_fails_without_attestation(monkeypatch) -> None:
    sha = "b" * 64
    _patch_pypi(monkeypatch, _make_project(EXT, "0.3.0", sha), None)

    result = verify_official_provenance(EXT, EXT_SPEC)

    assert result.verified is False
    assert "no PyPI attestation" in result.reason


def test_verify_provenance_fails_on_wrong_repository(monkeypatch) -> None:
    sha = "c" * 64
    _patch_pypi(
        monkeypatch,
        _make_project(EXT, "0.3.0", sha),
        _make_provenance("attacker/evil", "publish.yml", sha),
    )

    result = verify_official_provenance(EXT, EXT_SPEC)

    assert result.verified is False
    assert "expected publisher" in result.reason


def test_verify_provenance_fails_on_digest_mismatch(monkeypatch) -> None:
    file_sha = "d" * 64
    attested_sha = "e" * 64
    _patch_pypi(
        monkeypatch,
        _make_project(EXT, "0.3.0", file_sha),
        _make_provenance(EXT_SPEC.repo_slug, EXT_SPEC.workflow, attested_sha),
    )

    result = verify_official_provenance(EXT, EXT_SPEC)

    assert result.verified is False
    assert "does not cover the distribution digest" in result.reason


def test_verify_provenance_fails_on_workflow_mismatch(monkeypatch) -> None:
    sha = "f" * 64
    _patch_pypi(
        monkeypatch,
        _make_project(EXT, "0.3.0", sha),
        _make_provenance(EXT_SPEC.repo_slug, "release.yml", sha),
    )

    result = verify_official_provenance(EXT, EXT_SPEC)

    assert result.verified is False
    assert f"workflow {EXT_SPEC.workflow}" in result.reason


def test_verify_provenance_verifies_requested_version(monkeypatch) -> None:
    sha = "1" * 64
    project = {
        "releases": {
            "0.2.0": [{"filename": "dbwarden_pgsql_extensions-0.2.0-py3-none-any.whl",
                       "packagetype": "bdist_wheel", "digests": {"sha256": "0" * 64}}],
            "0.3.0": [{"filename": "dbwarden_pgsql_extensions-0.3.0-py3-none-any.whl",
                       "packagetype": "bdist_wheel", "digests": {"sha256": sha}}],
        }
    }
    monkeypatch.setattr("dbwarden.plugin._pypi_project_json", lambda p: project)
    monkeypatch.setattr(
        "dbwarden.plugin._pypi_provenance",
        lambda p, version, filename: _make_provenance(EXT_SPEC.repo_slug, EXT_SPEC.workflow, sha)
        if version == "0.3.0"
        else None,
    )

    result = verify_official_provenance(EXT, EXT_SPEC, version="0.3.0")

    assert result.verified is True
    assert result.version == "0.3.0"


def test_resolve_target_version_picks_highest_stable() -> None:
    spec = OfficialSpec(pypi="dbwarden-x", repo_slug="dbwarden/x", min_version="0.2.0")
    project = {
        "releases": {
            "0.1.0": [{"filename": "a", "digests": {"sha256": "x"}}],
            "0.2.0": [{"filename": "b", "digests": {"sha256": "x"}}],
            "0.4.0rc1": [{"filename": "c", "digests": {"sha256": "x"}}],
            "0.3.0": [{"filename": "d", "digests": {"sha256": "x"}}],
            "9.9.9": [{"filename": "e", "yanked": True, "digests": {"sha256": "x"}}],
        }
    }

    version, reason = _resolve_target_version(spec, project, None)

    assert version == "0.3.0"
    assert reason is None


def test_official_plugin_add_fails_closed_without_provenance(monkeypatch) -> None:
    called = False

    def install_distribution(dist_name, **kwargs):
        nonlocal called
        called = True

    monkeypatch.setattr("dbwarden.plugin.install_distribution", install_distribution)
    # PyPI publishes no attestation for this release -> fail closed, no install.
    monkeypatch.setattr(
        "dbwarden.plugin._pypi_project_json",
        lambda project: {"releases": {"1.0.0": [
            {"filename": f"{project.replace('-', '_')}-1.0.0-py3-none-any.whl",
             "packagetype": "bdist_wheel", "digests": {"sha256": "deadbeef"}}
        ]}},
    )
    monkeypatch.setattr("dbwarden.plugin._pypi_provenance", lambda project, version, filename: None)

    with pytest.raises(PluginInstallError, match="no PyPI attestation"):
        add_plugin("dbwarden-pgsql-extensions")

    assert called is False


def test_official_plugin_add_fails_closed_on_network_error(monkeypatch) -> None:
    called = False

    def install_distribution(dist_name, **kwargs):
        nonlocal called
        called = True

    def boom(project):
        raise urllib.error.URLError("offline")

    monkeypatch.setattr("dbwarden.plugin.install_distribution", install_distribution)
    monkeypatch.setattr("dbwarden.plugin._pypi_project_json", boom)

    with pytest.raises(PluginInstallError, match="Refusing to install official plugin"):
        add_plugin("dbwarden-pgsql-extensions")

    assert called is False


def test_official_plugin_add_writes_lockfile_after_provenance(monkeypatch, tmp_path) -> None:
    installed: list[str] = []
    monkeypatch.setattr("dbwarden.plugin.LOCK_PATH", tmp_path / "plugins.lock")
    monkeypatch.setattr("dbwarden.plugin.install_distribution", lambda name, **kw: installed.append(name))
    monkeypatch.setattr("dbwarden.plugin.installed_distribution_version", lambda name: "1.0.0")
    monkeypatch.setattr(
        "dbwarden.plugin.verify_official_provenance",
        lambda name, spec, version=None: ProvenanceResult(
            verified=True,
            version="1.0.0",
            identity=spec.repository,
            filename="dbwarden_pgsql_extensions-1.0.0-py3-none-any.whl",
            sha256="abc123",
        ),
    )

    entry = add_plugin("dbwarden-pgsql-extensions")

    assert installed == ["dbwarden-pgsql-extensions"]
    assert entry is not None
    assert entry.verified == "provenance"
    assert load_plugin_lock(tmp_path / "plugins.lock")["pgsql_extensions"].sha256 == "abc123"


def test_community_plugin_add_installs_without_trusting(monkeypatch, tmp_path) -> None:
    installed: list[str] = []
    monkeypatch.setattr("dbwarden.plugin.CONSENT_PATH", tmp_path / "consent.toml")
    monkeypatch.setattr("dbwarden.plugin.install_distribution", lambda name, **kw: installed.append(name))

    entry = add_plugin("dbwarden-acme")

    assert entry is None
    assert installed == ["dbwarden-acme"]
    assert consent_allows("dbwarden-acme", "1.0.0") is False


def test_plugin_remove_uninstalls_and_cleans_state(monkeypatch, tmp_path) -> None:
    uninstalled: list[str] = []
    monkeypatch.setattr("dbwarden.plugin.CONSENT_PATH", tmp_path / "consent.toml")
    monkeypatch.setattr("dbwarden.plugin.LOCK_PATH", tmp_path / "plugins.lock")
    monkeypatch.setattr("dbwarden.plugin.uninstall_distribution", lambda name, **kw: uninstalled.append(name))
    record_consent("dbwarden-acme", "1.0.0")
    write_plugin_lock_entry(
        "acme",
        PluginLockEntry(
            distribution="dbwarden-acme",
            version="1.0.0",
            filename="acme.whl",
            sha256="abc123",
            tier="official",
            verified="provenance",
            identity="https://example.invalid/acme",
            installed_at="2026-07-13T10:30:00Z",
        ),
    )

    remove_plugin("dbwarden-acme")

    assert uninstalled == ["dbwarden-acme"]
    assert consent_allows("dbwarden-acme", "1.0.0") is False
    assert load_plugin_lock(tmp_path / "plugins.lock") == {}


def test_plugin_add_cli_uses_installer(monkeypatch) -> None:
    monkeypatch.setattr("dbwarden.plugin.add_plugin", lambda name, **kw: None)
    monkeypatch.setattr("dbwarden.commands.plugin_cmd.add_plugin", lambda name, **kw: None)

    result = CliRunner().invoke(app, ["plugin", "add", "dbwarden-acme"])

    assert result.exit_code == 0
    assert "Installed community plugin" in result.output


def test_installer_command_builds_uv_and_pip_variants() -> None:
    import sys

    from dbwarden.plugin import installer_command

    assert installer_command("dbwarden-acme") == [
        sys.executable, "-m", "pip", "install", "dbwarden-acme",
    ]
    assert installer_command("dbwarden-acme", version="1.2.0") == [
        sys.executable, "-m", "pip", "install", "dbwarden-acme==1.2.0",
    ]
    assert installer_command("dbwarden-acme", use_uv=True, version="1.2.0") == [
        "uv", "add", "dbwarden-acme==1.2.0",
    ]
    assert installer_command("dbwarden-acme", use_uv=True, action="uninstall") == [
        "uv", "remove", "dbwarden-acme",
    ]


def test_add_plugin_forwards_uv_and_version(monkeypatch) -> None:
    captured: dict[str, object] = {}
    monkeypatch.setattr(
        "dbwarden.plugin.install_distribution",
        lambda name, **kw: captured.update({"name": name, **kw}),
    )

    add_plugin("dbwarden-acme", use_uv=True, version="2.0.0")

    assert captured == {"name": "dbwarden-acme", "use_uv": True, "version": "2.0.0"}


def test_plugin_add_dry_run_does_not_install(monkeypatch) -> None:
    called = False

    def add_plugin_stub(name, **kw):
        nonlocal called
        called = True

    monkeypatch.setattr("dbwarden.commands.plugin_cmd.add_plugin", add_plugin_stub)

    result = CliRunner().invoke(app, ["plugin", "add", "dbwarden-fastapi", "--dry-run"])

    assert result.exit_code == 0
    assert called is False
    assert "dry run" in result.output.lower()
    assert "official" in result.output


def test_plugin_list_json_format(monkeypatch) -> None:
    import json as _json

    ep = FakeEntryPoint("dbwarden-acme", "1.0.0", lambda registrar: None)
    monkeypatch.setattr("dbwarden.plugin.iter_plugin_entry_points", lambda: [ep])

    result = CliRunner().invoke(app, ["plugin", "list", "--format", "json"])

    assert result.exit_code == 0
    payload = _json.loads(result.output)
    assert payload[0]["distribution"] == "dbwarden-acme"
    assert payload[0]["tier"] == "community"


def test_plugin_remove_cli_uses_uninstaller(monkeypatch) -> None:
    removed: list[str] = []
    monkeypatch.setattr("dbwarden.plugin.remove_plugin", lambda name, **kw: removed.append(name))
    monkeypatch.setattr("dbwarden.commands.plugin_cmd.remove_plugin", lambda name, **kw: removed.append(name))

    result = CliRunner().invoke(app, ["plugin", "remove", "dbwarden-acme"])

    assert result.exit_code == 0
    assert removed == ["dbwarden-acme"]


def test_pg_extension_requires_plugin(monkeypatch) -> None:
    from dbwarden.commands.make_migrations import pipeline

    config = SimpleNamespace(database_type="postgresql", pg_extensions=["btree_gist"])
    monkeypatch.setattr(pipeline, "get_multi_db_config", lambda: SimpleNamespace(default="default"))
    monkeypatch.setattr(pipeline, "get_database", lambda db_name: config)

    upgrade_sql, rollback_sql, changes = pipeline._prepend_pg_preamble("", "", [], None)

    assert upgrade_sql == ""
    assert rollback_sql == ""
    assert changes == []


def test_pg_extension_plugin_handles_preamble(monkeypatch) -> None:
    from dbwarden.commands.make_migrations import pipeline

    class Handler:
        object_type = "pg_extension"
        op_types = ("create_pg_extension",)
        run_phase = RunPhase.PREAMBLE
        ordering = OrderingConstraint(after=(Anchor.PREAMBLE,), before=(Anchor.BEFORE_TABLES,))

        def extract(self, snapshot):
            return snapshot.get("pg_extensions", {})

        def model_spec_from_config(self, config):
            return {name: {} for name in config.pg_extensions}

        def model_spec_from_tables(self, model_tables):
            return {}

        def canonicalize(self, spec):
            return spec

        def diff(self, snap_spec, model_spec):
            from dbwarden.engine.core import Op

            return [Op("create_pg_extension", {"name": "btree_gist"}, {"name": "btree_gist"})], []

        def emit(self, op, db_name=None, **kwargs):
            return [
                MigrationStatement(
                    order=self.statement_order,
                    upgrade_sql='PLUGIN CREATE EXTENSION "btree_gist";',
                    rollback_sql='PLUGIN DROP EXTENSION "btree_gist";',
                )
            ]

    config = SimpleNamespace(database_type="postgresql", pg_extensions=["btree_gist"])
    monkeypatch.setattr(pipeline, "get_multi_db_config", lambda: SimpleNamespace(default="default"))
    monkeypatch.setattr(pipeline, "get_database", lambda db_name: config)
    PluginRegistrar("dbwarden-pgsql-extensions").register_object_handler(Handler())

    upgrade_sql, rollback_sql, changes = pipeline._prepend_pg_preamble("", "", [], None)

    assert upgrade_sql == 'PLUGIN CREATE EXTENSION "btree_gist";'
    assert rollback_sql == 'PLUGIN DROP EXTENSION "btree_gist";'
    assert 'CREATE EXTENSION IF NOT EXISTS "btree_gist";' not in upgrade_sql
    assert [(change.operation, change.table) for change in changes] == [
        ("create_extension", "btree_gist")
    ]


def test_model_loading_uses_plugin_hook_when_registered(monkeypatch, tmp_path) -> None:
    from dbwarden.engine.model_discovery import path_discovery

    model_path = tmp_path / "models.py"
    model_path.write_text("VALUE = 'core'\n", encoding="utf-8")
    calls: list[tuple[Path, Path]] = []

    def load_model_module(path: Path, base_dir: Path):
        calls.append((path, base_dir))
        return SimpleNamespace(VALUE="plugin")

    PluginRegistrar("dbwarden-sandbox").register("load_model_module", load_model_module)

    module = path_discovery.load_model_from_path(str(model_path))

    assert module.VALUE == "plugin"
    assert calls == [(model_path, Path.cwd().resolve())]


def test_model_loading_fallback_to_importlib(tmp_path) -> None:
    from dbwarden.engine.model_discovery import path_discovery
    from dbwarden.plugin import HookRegistry

    HookRegistry.clear()
    model_path = tmp_path / "models.py"
    model_path.write_text("VALUE = 'core'\n", encoding="utf-8")

    module = path_discovery.load_model_from_path(str(model_path))
    assert module is not None
    assert module.VALUE == "core"


def test_config_loading_uses_plugin_hook_when_registered(tmp_path) -> None:
    from dbwarden.config.resolve import _import_source
    from dbwarden.config.state import _ResolvedSource

    config_path = tmp_path / "dbwarden.py"
    config_path.write_text("SHOULD_NOT_RUN = True\n", encoding="utf-8")
    calls: list[tuple[Path, Path]] = []

    def load_config_module(path: Path, base_dir: Path) -> None:
        calls.append((path, base_dir))

    PluginRegistrar("dbwarden-sandbox").register("load_config_module", load_config_module)

    base_dir = _import_source(
        _ResolvedSource(
            kind="file",
            value=str(config_path),
            classification="isolated_file",
        )
    )

    assert base_dir == tmp_path.resolve()
    assert calls == [(config_path, tmp_path.resolve())]


def test_config_loading_fallback_to_importlib(tmp_path) -> None:
    from dbwarden.config.resolve import _import_source
    from dbwarden.config.state import _ResolvedSource
    from dbwarden.plugin import HookRegistry

    HookRegistry.clear()
    config_path = tmp_path / "dbwarden.py"
    config_path.write_text("from dbwarden.config_registry import database_config\n", encoding="utf-8")

    result = _import_source(
        _ResolvedSource(
            kind="file",
            value=str(config_path),
            classification="isolated_file",
        )
    )
    assert result == tmp_path.resolve()


def test_fastapi_session_factory_hook_works() -> None:
    calls: list[tuple[str | None, bool]] = []

    def session_factory(database: str | None = None, *, dev: bool = False):
        calls.append((database, dev))
        return "dependency"

    from dbwarden.plugin import HookRegistry
    HookRegistry.clear()
    PluginRegistrar("dbwarden-fastapi").register("session_factory", session_factory)

    from dbwarden.plugin import HookRegistry
    result = HookRegistry.execute_single("session_factory", "primary", dev=True)
    assert result == "dependency"
    assert calls == [("primary", True)]


def test_fastapi_session_factory_raises_without_hook() -> None:
    from dbwarden.plugin import HookRegistry, HookNotRegisteredError
    HookRegistry.clear()

    with pytest.raises(HookNotRegisteredError):
        HookRegistry.execute_single("session_factory", "primary")


def test_fastapi_engine_dependency_factories_use_plugin_hooks() -> None:
    async def async_sql_dep():
        yield "async-sql"

    def sync_sql_dep():
        yield "sync-sql"

    async def async_ch_dep():
        yield "async-ch"

    def sync_ch_dep():
        yield "sync-ch"

    from dbwarden.plugin import HookRegistry
    HookRegistry.clear()
    PluginRegistrar("dbwarden-fastapi").register(
        "session_factory", lambda name, *, dev=False: async_sql_dep
    )
    PluginRegistrar("dbwarden-fastapi").register(
        "sync_session_factory", lambda name, *, dev=False: sync_sql_dep
    )
    PluginRegistrar("dbwarden-fastapi").register(
        "clickhouse_session_factory", lambda name, *, dev=False: async_ch_dep
    )
    PluginRegistrar("dbwarden-fastapi").register(
        "clickhouse_sync_session_factory", lambda name, *, dev=False: sync_ch_dep
    )

    assert HookRegistry.execute_single("session_factory", "primary") is async_sql_dep
    assert HookRegistry.execute_single("sync_session_factory", "primary") is sync_sql_dep
    assert HookRegistry.execute_single("clickhouse_session_factory", "analytics") is async_ch_dep
    assert HookRegistry.execute_single("clickhouse_sync_session_factory", "analytics") is sync_ch_dep


def test_fastapi_health_router_uses_plugin_hook() -> None:
    pytest.importorskip("fastapi")
    from fastapi import APIRouter

    router = APIRouter()
    calls: list[tuple[str, str | None]] = []

    def health_routes(*, auth_mode: str = "open", api_key: str | None = None):
        calls.append((auth_mode, api_key))
        return router

    from dbwarden.plugin import HookRegistry
    HookRegistry.clear()
    PluginRegistrar("dbwarden-fastapi").register("health_routes", health_routes)

    result = HookRegistry.execute_single("health_routes", auth_mode="authenticated", api_key="secret")
    assert result is router
    assert calls == [("authenticated", "secret")]


def test_fastapi_migration_router_uses_plugin_hook() -> None:
    pytest.importorskip("fastapi")
    from fastapi import APIRouter

    router = APIRouter()

    from dbwarden.plugin import HookRegistry
    HookRegistry.clear()
    PluginRegistrar("dbwarden-fastapi").register(
        "migration_routes",
        lambda *, auth_mode="open", api_key=None: router,
    )

    result = HookRegistry.execute_single("migration_routes")
    assert result is router


def test_fastapi_lifespan_uses_plugin_hook() -> None:
    from contextlib import asynccontextmanager

    events: list[str] = []

    @asynccontextmanager
    async def lifespan(app=None, **kwargs):
        events.append(f"enter:{kwargs['mode']}")
        yield
        events.append("exit")

    from dbwarden.plugin import HookRegistry
    HookRegistry.clear()
    PluginRegistrar("dbwarden-fastapi").register("lifespan", lifespan)

    async def run_lifespan() -> None:
        async with HookRegistry.execute_single("lifespan", mode="none"):
            events.append("body")

    import asyncio
    asyncio.run(run_lifespan())

    assert events == ["enter:none", "body", "exit"]


def test_seed_commands_use_plugin_hooks(tmp_path) -> None:
    from dbwarden.commands.export_seeds import export_seeds_cmd
    from dbwarden.commands.seeds import (
        seed_apply_cmd,
        seed_create_cmd,
        seed_list_cmd,
        seed_rollback_cmd,
    )

    calls: list[tuple[str, dict[str, object]]] = []

    PluginRegistrar("dbwarden-seeds").register(
        "seed_create",
        lambda description, *, seed_type="sql", database=None, verbose=False: calls.append(
            (
                "create",
                {
                    "description": description,
                    "seed_type": seed_type,
                    "database": database,
                    "verbose": verbose,
                },
            )
        ),
    )
    PluginRegistrar("dbwarden-seeds").register(
        "seed_apply",
        lambda *, version=None, dry_run=False, database=None, all_databases=False, verbose=False: calls.append(
            (
                "apply",
                {
                    "version": version,
                    "dry_run": dry_run,
                    "database": database,
                    "all_databases": all_databases,
                    "verbose": verbose,
                },
            )
        ),
    )
    PluginRegistrar("dbwarden-seeds").register(
        "seed_list",
        lambda *, database=None, all_databases=False, verbose=False, prune=False: calls.append(
            (
                "list",
                {
                    "database": database,
                    "all_databases": all_databases,
                    "verbose": verbose,
                    "prune": prune,
                },
            )
        ),
    )
    PluginRegistrar("dbwarden-seeds").register(
        "seed_rollback",
        lambda *, count=None, to_version=None, database=None, all_databases=False, verbose=False: calls.append(
            (
                "rollback",
                {
                    "count": count,
                    "to_version": to_version,
                    "database": database,
                    "all_databases": all_databases,
                    "verbose": verbose,
                },
            )
        ),
    )
    PluginRegistrar("dbwarden-seeds").register(
        "seed_export",
        lambda *, database=None, all_databases=False, output_dir="seeds": calls.append(
            (
                "export",
                {
                    "database": database,
                    "all_databases": all_databases,
                    "output_dir": output_dir,
                },
            )
        ),
    )

    seed_create_cmd("load countries", seed_type="python", database="primary", verbose=True)
    seed_apply_cmd(version="0001", dry_run=True, database="primary", all_databases=False, verbose=True)
    seed_list_cmd(database="primary", all_databases=False, verbose=True, prune=True)
    seed_rollback_cmd(count=2, to_version=None, database="primary", all_databases=False, verbose=True)
    export_seeds_cmd(database="primary", all_databases=False, output_dir=str(tmp_path))

    assert calls == [
        (
            "create",
            {
                "description": "load countries",
                "seed_type": "python",
                "database": "primary",
                "verbose": True,
            },
        ),
        (
            "apply",
            {
                "version": "0001",
                "dry_run": True,
                "database": "primary",
                "all_databases": False,
                "verbose": True,
            },
        ),
        (
            "list",
            {
                "database": "primary",
                "all_databases": False,
                "verbose": True,
                "prune": True,
            },
        ),
        (
            "rollback",
            {
                "count": 2,
                "to_version": None,
                "database": "primary",
                "all_databases": False,
                "verbose": True,
            },
        ),
        (
            "export",
            {
                "database": "primary",
                "all_databases": False,
                "output_dir": str(tmp_path),
            },
        ),
    ]


def test_plugin_object_handler_overrides_core_handler_with_same_type() -> None:
    class PluginHandler:
        object_type = "role"
        run_phase = RunPhase.PREAMBLE
        ordering = OrderingConstraint(after=(Anchor.PREAMBLE,), before=(Anchor.BEFORE_TABLES,))

        def extract(self, snapshot):
            return {}

        def model_spec_from_config(self, config):
            return {"plugin_role": {}}

        def model_spec_from_tables(self, model_tables):
            return {}

        def canonicalize(self, spec):
            return spec

        def diff(self, snap_spec, model_spec):
            from dbwarden.engine.core import Op

            return [Op("create_role", {"role_name": "plugin_role"})], []

        def emit(self, op, db_name=None, **kwargs):
            return []

    class CoreHandler(PluginHandler):
        def model_spec_from_config(self, config):
            return {"core_role": {}}

        def diff(self, snap_spec, model_spec):
            from dbwarden.engine.core import Op

            return [Op("create_role", {"role_name": "core_role"})], []

    PluginRegistrar("dbwarden-ch-rbac").register_object_handler(PluginHandler())
    driver = RegistryDriver()
    driver.register(CoreHandler())

    upgrade_ops, rollback_ops = driver.run({}, [], SimpleNamespace())

    assert rollback_ops == []
    assert [op.upgrade_attrs["role_name"] for op in upgrade_ops] == ["plugin_role"]


def _override_handler(object_type: str, emitted_role: str):
    class Handler:
        run_phase = RunPhase.PREAMBLE
        ordering = OrderingConstraint(after=(Anchor.PREAMBLE,), before=(Anchor.BEFORE_TABLES,))

        def extract(self, snapshot):
            return {}

        def model_spec_from_config(self, config):
            return {emitted_role: {}}

        def model_spec_from_tables(self, model_tables):
            return {}

        def canonicalize(self, spec):
            return spec

        def diff(self, snap_spec, model_spec):
            from dbwarden.engine.core import Op

            return [Op("create_role", {"role_name": emitted_role})], []

        def emit(self, op, db_name=None, **kwargs):
            return []

    Handler.object_type = object_type
    return Handler


@contextmanager
def capture_registry_warnings():
    """Collect records straight off the registry logger.

    Deliberately not caplog: other suites disable propagation on the `dbwarden`
    logger, so records never reach the root handler caplog installs. Attaching
    here makes the assertion independent of whatever global logging state the
    rest of the run left behind.
    """
    import logging

    records: list[logging.LogRecord] = []

    class Collector(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            records.append(record)

    logger = logging.getLogger("dbwarden.registry")
    handler = Collector(level=logging.WARNING)
    logger.addHandler(handler)
    try:
        yield records
    finally:
        logger.removeHandler(handler)


def test_plugin_override_of_core_handler_is_logged() -> None:
    PluginRegistrar("dbwarden-pgsql-rbac").register_object_handler(
        _override_handler("role", "plugin_role")()
    )
    driver = RegistryDriver()

    with capture_registry_warnings() as records:
        driver.register(_override_handler("role", "core_role")())

    assert len(records) == 1
    message = records[0].getMessage()
    assert "dbwarden-pgsql-rbac" in message
    assert "'role'" in message


def test_plugin_override_is_logged_only_once_per_plugin_and_type() -> None:
    PluginRegistrar("dbwarden-pgsql-rbac").register_object_handler(
        _override_handler("role", "plugin_role")()
    )

    with capture_registry_warnings() as records:
        # Drivers get built repeatedly during one diff; the user should not read
        # the same warning ten times.
        for _ in range(3):
            RegistryDriver().register(_override_handler("role", "core_role")())

    assert len(records) == 1


def test_plugin_override_holds_when_driver_excludes_plugins() -> None:
    """A core-only driver must still step aside for a plugin-claimed type.

    The per-driver plugin exclusion exists so plugin ops are not emitted once per
    driver. If it also dropped the override, core and the plugin would both emit
    for the same object type.
    """
    PluginRegistrar("dbwarden-pgsql-rbac").register_object_handler(
        _override_handler("role", "plugin_role")()
    )
    driver = RegistryDriver(include_plugins=False)
    driver.register(_override_handler("role", "core_role")())

    upgrade_ops, _rollback_ops = driver.run({}, [], SimpleNamespace())

    assert upgrade_ops == []


def test_plugin_declaring_no_api_version_loads() -> None:
    """Plugins written before the contract was versioned keep working."""
    from dbwarden.plugin import check_api_compatibility

    def setup(registrar):
        pass

    check_api_compatibility(setup, "dbwarden-acme")


def test_plugin_declaring_the_current_api_version_loads(monkeypatch) -> None:
    from dbwarden.plugin import PLUGIN_API_ATTR, PLUGIN_API_VERSION, check_api_compatibility

    def setup(registrar):
        pass

    module = sys.modules[setup.__module__]
    monkeypatch.setattr(module, PLUGIN_API_ATTR, PLUGIN_API_VERSION, raising=False)

    check_api_compatibility(setup, "dbwarden-acme")


def test_plugin_declaring_a_different_api_version_is_refused(monkeypatch) -> None:
    from dbwarden.plugin import (
        PLUGIN_API_ATTR,
        PLUGIN_API_VERSION,
        PluginApiMismatchError,
        check_api_compatibility,
    )

    def setup(registrar):
        pass

    module = sys.modules[setup.__module__]
    monkeypatch.setattr(module, PLUGIN_API_ATTR, PLUGIN_API_VERSION + 1, raising=False)

    with pytest.raises(PluginApiMismatchError) as excinfo:
        check_api_compatibility(setup, "dbwarden-acme")

    message = str(excinfo.value)
    assert "dbwarden-acme" in message
    assert str(PLUGIN_API_VERSION) in message


def test_incompatible_plugin_is_reported_as_incompatible_not_failed(monkeypatch) -> None:
    """The state distinguishes a wrong pairing from a broken plugin."""
    from dbwarden.plugin import PLUGIN_API_ATTR, PLUGIN_API_VERSION, _load_plugin_entry_point

    registered: list[str] = []

    def setup(registrar):
        registered.append("ran")

    module = sys.modules[setup.__module__]
    monkeypatch.setattr(module, PLUGIN_API_ATTR, PLUGIN_API_VERSION + 1, raising=False)

    _load_plugin_entry_point(FakeEntryPoint("dbwarden-acme", "1.0.0", setup), "dbwarden-acme")

    from dbwarden.plugin import _LOAD_ERRORS, _LOAD_STATES

    assert _LOAD_STATES["dbwarden-acme"] == "incompatible"
    assert "plugin API version" in _LOAD_ERRORS["dbwarden-acme"]
    assert registered == [], "setup() must not run for an incompatible plugin"


def _pipeline_override_handler():
    """A plugin handler claiming `index`, a type core really owns.

    Colliding with a real core type is the whole point: a handler for a type no
    core handler owns is simply an addition, and would pass an override test
    without overriding anything.
    """
    from dbwarden.engine.core import Op

    class Overriding:
        object_type = "index"
        run_phase = RunPhase.DIFF
        ordering = OrderingConstraint(after=(Anchor.AFTER_TABLES,))

        def extract(self, snapshot):
            return {}

        def model_spec_from_config(self, config):
            return {}

        def model_spec_from_tables(self, model_tables):
            return {"marker": {}}

        def canonicalize(self, spec):
            return spec

        def diff(self, snap_spec, model_spec):
            return [Op("add_index", {"table": "FROM_PLUGIN"}, {})], []

        def emit(self, op, db_name=None, **kwargs):
            return []

    return Overriding()


def test_override_replaces_core_handler_through_the_snapshot_diff() -> None:
    """End to end: the plugin emits, core does not, and neither is doubled.

    RegistryDriver-level tests do not cover this. `diff_models_against_snapshot`
    builds around ten drivers and runs plugin handlers in a separate pass, so the
    override and the de-duplication interact only here.
    """
    from dbwarden.engine.snapshot import diff_models_against_snapshot

    PluginRegistrar("dbwarden-fake").register_object_handler(_pipeline_override_handler())

    upgrade_ops, _rollback_ops = diff_models_against_snapshot(
        [], {"tables": {}, "enums": {}, "indexes": {}, "constraints": {}}
    )

    index_ops = [op for op in upgrade_ops if op.get("type") == "add_index"]
    assert len(index_ops) == 1
    assert index_ops[0]["table"] == "FROM_PLUGIN"


def test_override_is_announced_during_a_real_diff() -> None:
    from dbwarden.engine.snapshot import diff_models_against_snapshot

    PluginRegistrar("dbwarden-fake").register_object_handler(_pipeline_override_handler())

    with capture_registry_warnings() as records:
        diff_models_against_snapshot(
            [], {"tables": {}, "enums": {}, "indexes": {}, "constraints": {}}
        )

    assert len(records) == 1
    assert "index" in records[0].getMessage()


def test_override_replaces_core_handler_through_the_offline_diff() -> None:
    """The offline (state-based) path has the same structure and the same risk."""
    from dbwarden.engine.offline import diff_model_states

    PluginRegistrar("dbwarden-fake").register_object_handler(_pipeline_override_handler())

    upgrade_ops, _rollback_ops = diff_model_states({"tables": {}}, {"tables": {}})

    index_ops = [op for op in upgrade_ops if op.get("type") == "add_index"]
    assert len(index_ops) == 1
    assert index_ops[0]["table"] == "FROM_PLUGIN"


def test_plugin_handler_for_a_new_type_does_not_displace_core() -> None:
    """A type core does not own is an addition, and core keeps its own handlers."""
    from dbwarden.engine.snapshot import diff_models_against_snapshot

    from dbwarden.engine.core import Op

    class NewType:
        object_type = "widget"
        run_phase = RunPhase.DIFF
        ordering = OrderingConstraint(after=(Anchor.AFTER_TABLES,))

        def extract(self, snapshot):
            return {}

        def model_spec_from_config(self, config):
            return {}

        def model_spec_from_tables(self, model_tables):
            return {"w": {}}

        def canonicalize(self, spec):
            return spec

        def diff(self, snap_spec, model_spec):
            return [Op("create_widget", {"name": "w"}, {})], []

        def emit(self, op, db_name=None, **kwargs):
            return []

    PluginRegistrar("dbwarden-fake").register_object_handler(NewType())

    with capture_registry_warnings() as records:
        upgrade_ops, _ = diff_models_against_snapshot(
            [], {"tables": {}, "enums": {}, "indexes": {}, "constraints": {}}
        )

    assert [op for op in upgrade_ops if op.get("type") == "create_widget"]
    assert records == []


def test_core_duplicate_object_handlers_still_raise() -> None:
    class Handler:
        object_type = "role"
        run_phase = RunPhase.PREAMBLE

        def extract(self, snapshot):
            return {}

        def model_spec_from_config(self, config):
            return {}

        def model_spec_from_tables(self, model_tables):
            return {}

        def canonicalize(self, spec):
            return spec

        def diff(self, snap_spec, model_spec):
            return [], []

        def emit(self, op, db_name=None, **kwargs):
            return []

    driver = RegistryDriver()
    driver.register(Handler())

    with pytest.raises(ValueError, match="Duplicate object handler"):
        driver.register(Handler())
