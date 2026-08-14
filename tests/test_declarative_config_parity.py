from __future__ import annotations

import inspect

from dbwarden.config_registry import _DECLARATIVE_DEFAULTS, _DECLARATIVE_FIELDS, database_config


def test_declarative_surface_matches_database_config_surface():
    signature = inspect.signature(database_config)
    function_fields = {
        name
        for name, parameter in signature.parameters.items()
        if name != "plugin_config" and parameter.kind is not inspect.Parameter.VAR_KEYWORD
    }
    assert _DECLARATIVE_FIELDS == function_fields


def test_declarative_defaults_match_database_config_defaults():
    signature = inspect.signature(database_config)
    function_defaults = {
        name: parameter.default
        for name, parameter in signature.parameters.items()
        if name in _DECLARATIVE_DEFAULTS
    }

    assert _DECLARATIVE_DEFAULTS == function_defaults
