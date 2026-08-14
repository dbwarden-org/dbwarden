from __future__ import annotations

import importlib
import sys

import pytest

from dbwarden.config_registry import reset_registry


@pytest.fixture(autouse=True)
def clean_registry():
    reset_registry()
    yield
    reset_registry()


def test_declarative_database_inherits_and_registers(monkeypatch):
    module_name = "_dbwarden_declarative_test"
    module = type(sys)(module_name)
    module.__dict__.update(
        {
            "DbwardenDatabase": importlib.import_module("dbwarden").DbwardenDatabase,
            "DATABASE_URL": "sqlite:///primary.db",
        }
    )
    exec(
        """
class Production(DbwardenDatabase):
    __abstract__ = True
    database_type = 'sqlite'
    model_paths = ['models']
    skip_if_missing = True

class Primary(Production):
    database_name = 'primary'
    database_url_sync = DATABASE_URL
    default = True
""",
        module.__dict__,
    )
    assert module.Primary.handle._name == "primary"

    from dbwarden.config_registry import registered_entries

    entry = registered_entries()[0]
    assert entry.database_name == "primary"
    assert entry.model_paths == ["models"]
    assert entry.skip_if_missing is True


def test_concrete_declarative_database_requires_url():
    from dbwarden import DbwardenDatabase

    with pytest.raises(ValueError, match="database_name and at least one database URL"):
        type(
            "MissingUrl",
            (DbwardenDatabase,),
            {"database_name": "missing", "default": True},
        )


def test_declarative_mutable_values_are_copied():
    from dbwarden import DbwardenDatabase
    from dbwarden.config_registry import registered_entries

    class Base(DbwardenDatabase):
        __abstract__ = True
        database_type = "sqlite"
        model_paths = ["models"]

    class First(Base):
        database_name = "first"
        database_url_sync = "sqlite:///first.db"
        default = True

    class Second(Base):
        database_name = "second"
        database_url_sync = "sqlite:///second.db"
        model_paths = ["other"]

    entries = registered_entries()
    assert entries[-2].model_paths == ["models"]
    assert entries[-1].model_paths == ["other"]


def test_declarative_plugin_config_is_inherited_and_overridden(monkeypatch):
    from dbwarden import DbwardenDatabase
    from dbwarden.config_registry import registered_entries
    from dbwarden.plugin import ConfigKeyRegistry

    monkeypatch.setitem(ConfigKeyRegistry._keys, "custom_objects", "test-plugin")

    class Base(DbwardenDatabase):
        __abstract__ = True
        plugin_config = {"custom_objects": ["base"]}

    class Primary(Base):
        database_name = "primary"
        database_url_sync = "sqlite:///primary.db"
        custom_objects = ["primary"]

    assert registered_entries()[-1].plugin_config == {"custom_objects": ["primary"]}


def test_declarative_plugin_config_mapping_is_copied(monkeypatch):
    from dbwarden import DbwardenDatabase
    from dbwarden.config_registry import registered_entries
    from dbwarden.plugin import ConfigKeyRegistry

    monkeypatch.setitem(ConfigKeyRegistry._keys, "custom_objects", "test-plugin")

    class Base(DbwardenDatabase):
        __abstract__ = True
        plugin_config = {"custom_objects": ["base"]}

    class First(Base):
        database_name = "first"
        database_url_sync = "sqlite:///first.db"

    class Second(Base):
        database_name = "second"
        database_url_sync = "sqlite:///second.db"
        plugin_config = {"custom_objects": ["second"]}

    entries = registered_entries()
    assert entries[-2].plugin_config == {"custom_objects": ["base"]}
    assert entries[-1].plugin_config == {"custom_objects": ["second"]}
