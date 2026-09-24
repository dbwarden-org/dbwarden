import json
from copy import deepcopy
from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

from dbwarden.cli.main import app
from dbwarden.commands.make_migrations.generation import generate_files
from dbwarden.engine.core import MigrationStatement, Op, RunPhase, StatementOrder
from dbwarden.engine.core.protocol import op_to_dict
from dbwarden.engine.generation_state import apply_changes, schema_state, state_checksum
from dbwarden.engine.safety.classifiers import Safety, classify_operation
from dbwarden.engine.safety.partition import group_operations
from dbwarden.engine.safety.plans import read_trusted_plan
from dbwarden.plugin import ObjectPluginRegistry, PluginRegistrar


@pytest.fixture
def plugin(monkeypatch):
    monkeypatch.setattr(ObjectPluginRegistry, "_handlers", {})
    monkeypatch.setattr(ObjectPluginRegistry, "_categories", {})
    monkeypatch.setattr(
        "dbwarden.config.get_database",
        lambda _: SimpleNamespace(database_type="sqlite"),
    )
    registrar = PluginRegistrar("test-plugin")
    registrar.register_migration_category("cleanup", order=200)

    class Handler:
        object_type = "custom"
        run_phase = RunPhase.PREAMBLE
        statement_order = StatementOrder.CREATE_TABLE

        def emit(self, op, **kwargs):
            attrs = op.upgrade_attrs
            return [
                MigrationStatement(
                    self.statement_order,
                    f"CREATE TABLE {attrs['name']} (id INTEGER);",
                    f"DROP TABLE {attrs['name']};",
                    category=attrs.get("statement_category"),
                    safety=attrs.get("statement_safety"),
                )
            ]

    registrar.register_object_handler(Handler())
    return registrar


def test_plugin_statement_categories_generate_composable_trusted_sql(plugin, tmp_path):
    ops = [
        op_to_dict(
            Op(
                "custom",
                {
                    "name": name,
                    "handler": "custom",
                    "statement_category": category,
                    "statement_safety": severity,
                },
            )
        )
        for name, category, severity in [
            ("one", "base", "INFO"),
            ("two", "deferred", "WARN"),
            ("three", "cleanup", "INFO"),
        ]
    ]
    before = schema_state(
        {"format_version": 2, "tables": {}, "configured_objects": {"custom": {}}}
    )
    after = deepcopy(before)
    after["configured_objects"]["custom"] = {
        name: True for name in ("one", "two", "three")
    }
    artifacts = generate_files(
        ops,
        [],
        database="primary",
        db_name="primary",
        migrations_dir=str(tmp_path),
        baseline=before,
        target=after,
        threshold="WARN",
    )
    assert [a["plan"]["category"]["name"] for a in artifacts] == [
        "base",
        "deferred",
        "cleanup",
    ]
    assert [a["version"] for a in artifacts] == ["0001", "0002", "0003"]
    assert artifacts[-1]["filename"].endswith("__cleanup.sql")
    state = deepcopy(before)
    for artifact, name in zip(artifacts, ("one", "two", "three")):
        plan, reason = read_trusted_plan(tmp_path / artifact["filename"])
        assert plan, reason
        assert plan["base_checksum"] == state_checksum(state)
        for op in plan["upgrade_ops"]:
            apply_changes(state, op["state_changes"])
        assert plan["target_checksum"] == state_checksum(state)
        assert f"CREATE TABLE {name}" in artifact["content"]
        assert (
            sum(
                f"CREATE TABLE {n}" in artifact["content"]
                for n in ("one", "two", "three")
            )
            == 1
        )
    assert state == after
    assert artifacts[1]["plan"]["required_flags"] == ["--force"]
    assert artifacts[2]["plan"]["required_flags"] == []
    ObjectPluginRegistry.clear()
    assert read_trusted_plan(tmp_path / artifacts[-1]["filename"])[0]


def test_category_dependency_promotion_and_safety_floor(plugin):
    ops = [
        {"id": "a", "type": "select", "category": "cleanup"},
        {"id": "b", "type": "select", "category": "base", "depends_on": ["a"]},
        {"id": "c", "type": "drop_table", "table": "old", "category": "base"},
    ]
    groups = group_operations(ops, "WARN", "sqlite")
    assert [(name, [op["id"] for op in group]) for name, group in groups] == [
        ("deferred", ["c"]),
        ("cleanup", ["a", "b"]),
    ]
    assert (
        classify_operation(
            {
                "type": "drop_table",
                "plugin_safety": {"plugin": "test-plugin", "severity": "INFO"},
            }
        )
        == Safety.CRITICAL
    )


@pytest.mark.parametrize(
    "name,order",
    [
        ("base", 1),
        ("../escape", 1),
        ("Bad", 1),
        ("valid", 0),
        ("valid", 100),
        ("valid", True),
    ],
)
def test_invalid_category_rejected(plugin, name, order):
    with pytest.raises(ValueError):
        plugin.register_migration_category(name, order=order)


def test_category_conflicts_and_unknown_fail_before_writes(plugin, tmp_path):
    plugin.register_migration_category("cleanup", order=200)
    with pytest.raises(ValueError, match="ownership"):
        PluginRegistrar("other").register_migration_category("cleanup", order=200)
    with pytest.raises(ValueError, match="already registered"):
        plugin.register_migration_category("another", order=200)
    for category, kind, message in [
        ("missing", "versioned", "Unknown migration category"),
        ("cleanup", "roc", "Repeatable"),
    ]:
        with pytest.raises(ValueError, match=message):
            generate_files(
                [
                    op_to_dict(
                        Op("custom", {"name": "one"}, category=category, safety="INFO")
                    )
                ],
                [],
                database="primary",
                db_name="primary",
                migrations_dir=str(tmp_path),
                migration_type=kind,
            )
        assert not list(tmp_path.iterdir())


def test_cli_lists_custom_categories(plugin, monkeypatch):
    monkeypatch.setattr(
        "dbwarden.commands.plugin_cmd.load_plugins", lambda **kwargs: None
    )
    result = CliRunner().invoke(app, ["plugin", "categories", "--format", "json"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.output) == list(ObjectPluginRegistry.categories().values())


def test_atomic_operations_cannot_split_and_unknown_safety_fails(plugin, tmp_path):
    handler = ObjectPluginRegistry.handlers()["custom"].handler
    handler.emit = lambda *args, **kwargs: [
        MigrationStatement(
            StatementOrder.CREATE_TABLE,
            "SELECT 1;",
            "SELECT 1;",
            category=category,
            safety="SAFE",
        )
        for category in ("base", "deferred")
    ]
    with pytest.raises(ValueError, match="atomic operation"):
        generate_files(
            [{"type": "custom", "name": "one"}],
            [],
            database="primary",
            db_name="primary",
            migrations_dir=str(tmp_path),
        )
    handler.emit = lambda *args, **kwargs: [
        MigrationStatement(StatementOrder.CREATE_TABLE, "SELECT 1;", "SELECT 1;")
    ]
    with pytest.raises(ValueError, match="Unclassified"):
        generate_files(
            [{"type": "custom", "name": "one"}],
            [],
            database="primary",
            db_name="primary",
            migrations_dir=str(tmp_path),
        )
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("migration_type,prefix", [("ra", "RA"), ("roc", "ROC")])
def test_plugin_generates_unsplit_repeatable_plans(
    plugin, tmp_path, migration_type, prefix
):
    artifacts = generate_files(
        [op_to_dict(Op("custom", {"name": "one"}, safety="INFO"))],
        [],
        database="primary",
        db_name="primary",
        migrations_dir=str(tmp_path),
        migration_type=migration_type,
    )
    assert len(artifacts) == 1
    path = tmp_path / artifacts[0]["filename"]
    assert f"__{prefix}__" in path.name
    assert read_trusted_plan(path)[0]["severity"]["file"] == "INFO"


def test_category_metadata_tampering_is_untrusted(plugin, tmp_path):
    artifact = generate_files(
        [op_to_dict(Op("custom", {"name": "one"}, category="cleanup", safety="INFO"))],
        [],
        database="primary",
        db_name="primary",
        migrations_dir=str(tmp_path),
    )[0]
    path = tmp_path / artifact["filename"]
    plan = artifact["plan"]
    plan["category"]["order"] = 0
    path.with_suffix(".plan.json").write_text(json.dumps(plan), encoding="utf-8")
    assert read_trusted_plan(path) == (None, "invalid custom migration category")
