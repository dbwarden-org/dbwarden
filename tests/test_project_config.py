from __future__ import annotations

from pathlib import Path

import pytest

from dbwarden import DbwardenConfig
from dbwarden.config import get_config, get_project_config
from dbwarden.config.resolve import _file_has_database_config_call
from dbwarden.config_registry import registered_project_config, reset_registry
from dbwarden.exceptions import ConfigurationError


@pytest.fixture(autouse=True)
def clean_registry():
    reset_registry()
    yield
    reset_registry()


def test_project_config_loads_without_changing_get_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    (tmp_path / "dbwarden.py").write_text(
        "from dbwarden import DbwardenConfig, database_config\n"
        "\n"
        "class Project(DbwardenConfig):\n"
        "    pre_migrate_safety = 'warn'\n"
        "    pre_migrate_impact = 'block'\n"
        "    missing_plan = 'block'\n"
        "    impact_paths = ['app', 'workers']\n"
        "\n"
        "database_config(\n"
        "    database_name='primary',\n"
        "    default=True,\n"
        "    database_url_sync='sqlite:///./test.db',\n"
        ")\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)

    project = get_project_config()

    assert project.pre_migrate_safety == "warn"
    assert project.pre_migrate_impact == "block"
    assert project.missing_plan == "block"
    assert project.impact_paths == ["app", "workers"]
    assert get_project_config() is project

    reset_registry()

    reloaded = get_project_config()
    assert reloaded is not project
    assert reloaded.impact_paths == ["app", "workers"]
    assert get_config().sqlalchemy_url == "sqlite:///./test.db"


def test_project_config_defaults_preserve_existing_behavior(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    (tmp_path / "dbwarden.py").write_text(
        "from dbwarden import database_config\n"
        "database_config(database_name='primary', default=True, database_url_sync='sqlite:///./test.db')\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)

    project = get_project_config()

    assert project.pre_migrate_safety == "off"
    assert project.pre_migrate_impact == "off"
    assert project.missing_plan == "off"
    assert project.impact_paths == ["."]


def test_project_config_requires_blocking_missing_plan_for_blocking_impact():
    with pytest.raises(ConfigurationError, match="missing_plan"):

        class InvalidProject(DbwardenConfig):
            pre_migrate_impact = "block"


def test_project_config_rejects_invalid_policy_values():
    with pytest.raises(ConfigurationError, match="pre_migrate_safety"):

        class InvalidProject(DbwardenConfig):
            pre_migrate_safety = "invalid"


def test_project_config_is_singleton_and_registry_reset_clears_it():
    class FirstProject(DbwardenConfig):
        impact_paths = ["app"]

    assert registered_project_config() is not None
    with pytest.raises(ConfigurationError, match="Only one concrete DbwardenConfig"):

        class SecondProject(DbwardenConfig):
            pass

    reset_registry()

    class ReplacementProject(DbwardenConfig):
        pre_migrate_safety = "warn"

    entry = registered_project_config()
    assert entry is not None
    assert entry.pre_migrate_safety == "warn"


def test_source_discovery_recognizes_declarative_project_config(tmp_path: Path):
    path = tmp_path / "config.py"
    path.write_text(
        "from dbwarden import DbwardenConfig as Config\n"
        "class Project(Config):\n"
        "    pre_migrate_safety = 'warn'\n",
        encoding="utf-8",
    )

    assert _file_has_database_config_call(path)


def test_project_config_all_off_preserves_behavior(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    (tmp_path / "dbwarden.py").write_text(
        "from dbwarden import DbwardenConfig, database_config\n"
        "\n"
        "class AllOff(DbwardenConfig):\n"
        "    pre_migrate_safety = 'off'\n"
        "    pre_migrate_impact = 'off'\n"
        "    missing_plan = 'off'\n"
        "    impact_paths = []\n"
        "\n"
        "database_config(\n"
        "    database_name='primary',\n"
        "    default=True,\n"
        "    database_url_sync='sqlite:///./test.db',\n"
        ")\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)

    config = get_project_config()
    assert config.pre_migrate_safety == "off"
    assert config.pre_migrate_impact == "off"
    assert config.missing_plan == "off"
    assert config.impact_paths == []


def test_project_config_warn_policy_values(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    (tmp_path / "dbwarden.py").write_text(
        "from dbwarden import DbwardenConfig, database_config\n"
        "\n"
        "class WarnPolicy(DbwardenConfig):\n"
        "    pre_migrate_safety = 'warn'\n"
        "    pre_migrate_impact = 'warn'\n"
        "    missing_plan = 'warn'\n"
        "\n"
        "database_config(\n"
        "    database_name='primary',\n"
        "    default=True,\n"
        "    database_url_sync='sqlite:///./test.db',\n"
        ")\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)

    config = get_project_config()
    assert config.pre_migrate_safety == "warn"
    assert config.pre_migrate_impact == "warn"
    assert config.missing_plan == "warn"


def test_project_config_block_policy_values(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    (tmp_path / "dbwarden.py").write_text(
        "from dbwarden import DbwardenConfig, database_config\n"
        "\n"
        "class BlockPolicy(DbwardenConfig):\n"
        "    pre_migrate_safety = 'block'\n"
        "    pre_migrate_impact = 'block'\n"
        "    missing_plan = 'block'\n"
        "    impact_paths = ['app']\n"
        "\n"
        "database_config(\n"
        "    database_name='primary',\n"
        "    default=True,\n"
        "    database_url_sync='sqlite:///./test.db',\n"
        ")\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)

    config = get_project_config()
    assert config.pre_migrate_safety == "block"
    assert config.pre_migrate_impact == "block"
    assert config.missing_plan == "block"
    assert config.impact_paths == ["app"]


def test_project_config_rejects_invalid_impact_policy():
    with pytest.raises(ConfigurationError, match="pre_migrate_impact"):
        class InvalidImpact(DbwardenConfig):
            pre_migrate_impact = "invalid"


def test_project_config_rejects_invalid_missing_plan_policy():
    with pytest.raises(ConfigurationError, match="missing_plan"):
        class InvalidMissingPlan(DbwardenConfig):
            missing_plan = "invalid"


def test_project_config_impact_block_requires_missing_plan_block():
    with pytest.raises(ConfigurationError, match="missing_plan"):
        class BadCombo(DbwardenConfig):
            pre_migrate_impact = "block"
            missing_plan = "warn"


def test_project_config_impact_warn_allows_missing_plan_off(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    (tmp_path / "dbwarden.py").write_text(
        "from dbwarden import DbwardenConfig, database_config\n"
        "\n"
        "class GoodCombo(DbwardenConfig):\n"
        "    pre_migrate_impact = 'warn'\n"
        "    missing_plan = 'off'\n"
        "\n"
        "database_config(\n"
        "    database_name='primary',\n"
        "    default=True,\n"
        "    database_url_sync='sqlite:///./test.db',\n"
        ")\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)

    config = get_project_config()
    assert config.pre_migrate_impact == "warn"
    assert config.missing_plan == "off"


def test_project_config_impact_off_allows_missing_plan_warn(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    (tmp_path / "dbwarden.py").write_text(
        "from dbwarden import DbwardenConfig, database_config\n"
        "\n"
        "class GoodCombo(DbwardenConfig):\n"
        "    pre_migrate_impact = 'off'\n"
        "    missing_plan = 'warn'\n"
        "\n"
        "database_config(\n"
        "    database_name='primary',\n"
        "    default=True,\n"
        "    database_url_sync='sqlite:///./test.db',\n"
        ")\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)

    config = get_project_config()
    assert config.pre_migrate_impact == "off"
    assert config.missing_plan == "warn"


def test_project_config_rejects_absolute_impact_paths():
    with pytest.raises(ConfigurationError, match="impact_paths"):
        class BadPaths(DbwardenConfig):
            impact_paths = ["/absolute/path"]


def test_project_config_rejects_traversal_impact_paths():
    with pytest.raises(ConfigurationError, match="impact_paths"):
        class BadPaths(DbwardenConfig):
            impact_paths = ["../traversal"]


def test_project_config_accepts_relative_impact_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    (tmp_path / "dbwarden.py").write_text(
        "from dbwarden import DbwardenConfig, database_config\n"
        "\n"
        "class GoodPaths(DbwardenConfig):\n"
        "    impact_paths = ['app', 'workers', 'src/models']\n"
        "\n"
        "database_config(\n"
        "    database_name='primary',\n"
        "    default=True,\n"
        "    database_url_sync='sqlite:///./test.db',\n"
        ")\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)

    config = get_project_config()
    assert config.impact_paths == ["app", "workers", "src/models"]


def test_project_config_rejects_empty_policy_value():
    with pytest.raises(ConfigurationError, match="pre_migrate_safety"):
        class EmptyPolicy(DbwardenConfig):
            pre_migrate_safety = ""


def test_project_config_rejects_case_sensitive_policy():
    with pytest.raises(ConfigurationError, match="pre_migrate_safety"):
        class CasePolicy(DbwardenConfig):
            pre_migrate_safety = "Block"
