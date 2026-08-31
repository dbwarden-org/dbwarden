import logging

import pytest

from dbwarden.logging import (
    ComponentFilter,
    clear_component_levels,
    get_component_level,
    get_component_logger,
    parse_log_level_spec,
    set_component_level,
)
from dbwarden.logging import _component_levels


@pytest.fixture(autouse=True)
def _clean_component_levels():
    """Reset component levels before and after each test."""
    clear_component_levels()
    yield
    clear_component_levels()


class TestComponentFilter:
    def test_passes_when_no_overrides_registered(self):
        f = ComponentFilter()
        record = logging.LogRecord(
            name="dbwarden.snapshot",
            level=logging.DEBUG,
            pathname="test.py",
            lineno=1,
            msg="test",
            args=(),
            exc_info=None,
        )
        assert f.filter(record) is True

    def test_passes_message_above_component_level(self):
        set_component_level("snapshot", logging.WARNING)
        f = ComponentFilter()
        record = logging.LogRecord(
            name="dbwarden.snapshot",
            level=logging.WARNING,
            pathname="test.py",
            lineno=1,
            msg="test",
            args=(),
            exc_info=None,
        )
        assert f.filter(record) is True

    def test_rejects_message_below_component_level(self):
        set_component_level("snapshot", logging.WARNING)
        f = ComponentFilter()
        record = logging.LogRecord(
            name="dbwarden.snapshot",
            level=logging.INFO,
            pathname="test.py",
            lineno=1,
            msg="test",
            args=(),
            exc_info=None,
        )
        assert f.filter(record) is False

    def test_ignores_unknown_component(self):
        set_component_level("snapshot", logging.WARNING)
        f = ComponentFilter()
        record = logging.LogRecord(
            name="dbwarden.unknown",
            level=logging.DEBUG,
            pathname="test.py",
            lineno=1,
            msg="test",
            args=(),
            exc_info=None,
        )
        assert f.filter(record) is True

    def test_ignores_non_dbwarden_logger(self):
        set_component_level("snapshot", logging.WARNING)
        f = ComponentFilter()
        record = logging.LogRecord(
            name="some.other.library",
            level=logging.DEBUG,
            pathname="test.py",
            lineno=1,
            msg="test",
            args=(),
            exc_info=None,
        )
        assert f.filter(record) is True

    def test_multiple_components_independent(self):
        set_component_level("snapshot", logging.WARNING)
        set_component_level("plugin", logging.DEBUG)
        f = ComponentFilter()

        snapshot_debug = logging.LogRecord(
            name="dbwarden.snapshot",
            level=logging.DEBUG,
            pathname="test.py", lineno=1, msg="test", args=(), exc_info=None,
        )
        plugin_debug = logging.LogRecord(
            name="dbwarden.plugin",
            level=logging.DEBUG,
            pathname="test.py", lineno=1, msg="test", args=(), exc_info=None,
        )

        assert f.filter(snapshot_debug) is False
        assert f.filter(plugin_debug) is True


class TestSetGetComponentLevel:
    def test_set_and_get(self):
        set_component_level("snapshot", logging.DEBUG)
        assert get_component_level("snapshot") == logging.DEBUG

    def test_get_unknown_returns_none(self):
        assert get_component_level("nonexistent") is None

    def test_set_replaces_existing(self):
        set_component_level("snapshot", logging.WARNING)
        set_component_level("snapshot", logging.ERROR)
        assert get_component_level("snapshot") == logging.ERROR

    def test_rejects_non_int_level(self):
        with pytest.raises(TypeError, match="level must be an int"):
            set_component_level("snapshot", "debug")


class TestParseLogLevelSpec:
    def test_valid_spec(self):
        component, level = parse_log_level_spec("snapshot:debug")
        assert component == "snapshot"
        assert level == logging.DEBUG

    def test_warning_spec(self):
        component, level = parse_log_level_spec("plugin:warning")
        assert component == "plugin"
        assert level == logging.WARNING

    def test_numeric_level(self):
        component, level = parse_log_level_spec("lock:20")
        assert component == "lock"
        assert level == 20

    def test_missing_colon(self):
        with pytest.raises(ValueError, match="Expected format"):
            parse_log_level_spec("snapshotdebug")

    def test_empty_component(self):
        with pytest.raises(ValueError, match="Empty component name"):
            parse_log_level_spec(":debug")

    def test_invalid_level(self):
        with pytest.raises(ValueError, match="Invalid level"):
            parse_log_level_spec("snapshot:banana")

    def test_strips_whitespace(self):
        component, level = parse_log_level_spec("  snapshot : debug  ")
        assert component == "snapshot"
        assert level == logging.DEBUG


class TestGetComponentLogger:
    def test_returns_stdlib_child_logger(self):
        logger = get_component_logger("snapshot")
        assert logger.name == "dbwarden.snapshot"
        assert isinstance(logger, logging.Logger)

    def test_sets_child_logger_to_notset(self):
        logger = get_component_logger("snapshot")
        assert logger.level == logging.NOTSET or logger.level == 0


class TestClearComponentLevels:
    def test_clears_all(self):
        set_component_level("snapshot", logging.DEBUG)
        set_component_level("plugin", logging.WARNING)
        clear_component_levels()
        assert get_component_level("snapshot") is None
        assert get_component_level("plugin") is None

    def test_resets_child_loggers_to_notset(self):
        set_component_level("snapshot", logging.DEBUG)
        clear_component_levels()
        logger = logging.getLogger("dbwarden.snapshot")
        assert logger.level == logging.NOTSET or logger.level == 0
