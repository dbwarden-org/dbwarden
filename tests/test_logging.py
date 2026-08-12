import json
import logging
import sys
from datetime import datetime, timezone

import pytest

from dbwarden.logging import DBWardenLogger, LogCandidate, Verbosity, get_logger


@pytest.fixture(autouse=True)
def restore_dbwarden_log_propagation():
    """Put `dbwarden.propagate` back after each test.

    Several tests here silence propagation to keep captured output clean, but the
    stdlib logger object outlives the test. Left unrestored it swallows records
    from every `dbwarden.*` child logger for the rest of the session, which makes
    unrelated log assertions elsewhere fail only in a full run.
    """
    logger = logging.getLogger("dbwarden")
    original = logger.propagate
    yield
    logger.propagate = original


class TestJSONLogging:
    """Tests for JSON logging via DBWARDEN_LOG_JSON."""

    def test_color_helpers_and_status_use_expected_ansi_sequences(self, monkeypatch):
        from dbwarden import logging as logging_module

        monkeypatch.setattr(logging_module, "supports_color", lambda: True)

        assert logging_module.colorize("hello", logging_module.ANSI_COLORS["red"]) == (
            f"{logging_module.ANSI_COLORS['red']}hello{logging_module.ANSI_COLORS['reset']}"
        )
        assert logging_module.colorize_status("applied") == (
            f"{logging_module.STATUS_COLORS['APPLIED']}[APPLIED]{logging_module.ANSI_COLORS['reset']}"
        )
        assert logging_module.colorize_status("unknown") == (
            f"{logging_module.ANSI_COLORS['reset']}[UNKNOWN]{logging_module.ANSI_COLORS['reset']}"
        )

    def test_colorize_sql_highlights_keywords_strings_and_comments(self, monkeypatch):
        from dbwarden import logging as logging_module

        monkeypatch.setattr(logging_module, "supports_color", lambda: True)

        output = logging_module.colorize_sql("SELECT 'value' -- comment")

        assert "SELECT" in output
        assert "'value'" in output
        assert "-- comment" in output
        assert logging_module.ANSI_COLORS["magenta"] in output
        assert logging_module.ANSI_COLORS["green"] in output
        assert (
            logging_module.ANSI_COLORS["dim"] + logging_module.ANSI_COLORS["cyan"]
        ) in output

    def test_colored_formatter_applies_level_color_when_supported(self, monkeypatch):
        from dbwarden import logging as logging_module

        monkeypatch.setattr(logging_module, "supports_color", lambda: True)

        formatter = logging_module.ColoredFormatter("%(message)s")
        record = logging.LogRecord(
            name="dbwarden",
            level=logging.ERROR,
            pathname="test.py",
            lineno=1,
            msg="boom",
            args=(),
            exc_info=None,
        )

        output = formatter.format(record)

        assert output == (
            f"{logging_module.LOG_COLORS[logging.ERROR]}boom"
            f"{logging_module.ANSI_COLORS['reset']}"
        )

    def test_json_formatter_outputs_valid_json(self):
        from dbwarden.logging import JSONFormatter

        fmt = JSONFormatter()
        record = logging.LogRecord(
            name="dbwarden",
            level=logging.INFO,
            pathname="test.py",
            lineno=1,
            msg="hello world",
            args=(),
            exc_info=None,
        )
        output = fmt.format(record)
        parsed = json.loads(output)
        assert parsed["message"] == "hello world"
        assert parsed["level"] == "INFO"
        assert parsed["logger"] == "dbwarden"
        assert "timestamp" in parsed

    def test_json_formatter_includes_db_context_when_present(self):
        from dbwarden.logging import JSONFormatter

        fmt = JSONFormatter()
        record = logging.LogRecord(
            name="dbwarden",
            level=logging.INFO,
            pathname="test.py",
            lineno=1,
            msg="migration applied",
            args=(),
            exc_info=None,
        )
        record.db_name = "primary"
        record.db_type = "sqlite"
        output = fmt.format(record)
        parsed = json.loads(output)
        assert parsed["db_name"] == "primary"
        assert parsed["db_type"] == "sqlite"

    def test_json_formatter_includes_exception(self):
        from dbwarden.logging import JSONFormatter

        fmt = JSONFormatter()
        try:
            raise ValueError("test error")
        except ValueError:
            record = logging.LogRecord(
                name="dbwarden",
                level=logging.ERROR,
                pathname="test.py",
                lineno=1,
                msg="something failed",
                args=(),
                exc_info=sys.exc_info(),
            )

        output = fmt.format(record)
        parsed = json.loads(output)
        assert "exception" in parsed
        assert "ValueError" in parsed["exception"]

    def test_json_formatter_handles_all_log_levels(self):
        from dbwarden.logging import JSONFormatter

        fmt = JSONFormatter()
        for level in (
            logging.DEBUG,
            logging.INFO,
            logging.WARNING,
            logging.ERROR,
            logging.CRITICAL,
        ):
            record = logging.LogRecord(
                name="dbwarden",
                level=level,
                pathname="test.py",
                lineno=1,
                msg="test",
                args=(),
                exc_info=None,
            )
            output = fmt.format(record)
            parsed = json.loads(output)
            assert parsed["level"] == logging.getLevelName(level)

    def test_json_formatter_timestamp_is_utc_and_uses_fractional_seconds(self):
        from dbwarden.logging import JSONFormatter

        fmt = JSONFormatter()
        record = logging.LogRecord(
            name="dbwarden",
            level=logging.INFO,
            pathname="test.py",
            lineno=1,
            msg="hello world",
            args=(),
            exc_info=None,
        )
        record.created = datetime(
            2026,
            1,
            2,
            3,
            4,
            5,
            123456,
            tzinfo=timezone.utc,
        ).timestamp()

        output = fmt.format(record)
        parsed = json.loads(output)

        assert parsed["timestamp"] == "2026-01-02T03:04:05.123456Z"

    def test_json_formatter_default_format_time_uses_millisecond_precision(self):
        from dbwarden.logging import JSONFormatter

        fmt = JSONFormatter()
        record = logging.LogRecord(
            name="dbwarden",
            level=logging.INFO,
            pathname="test.py",
            lineno=1,
            msg="hello world",
            args=(),
            exc_info=None,
        )
        record.created = datetime(
            2026,
            1,
            2,
            3,
            4,
            5,
            123456,
            tzinfo=timezone.utc,
        ).timestamp()

        assert fmt.formatTime(record) == "2026-01-02T03:04:05.123+00:00"

    def test_json_formatter_includes_non_reserved_extra_fields(self):
        from dbwarden.logging import JSONFormatter

        fmt = JSONFormatter()
        record = logging.LogRecord(
            name="dbwarden",
            level=logging.INFO,
            pathname="test.py",
            lineno=1,
            msg="hello world",
            args=(),
            exc_info=None,
        )
        record.request_id = "req-123"

        output = fmt.format(record)
        parsed = json.loads(output)

        assert parsed["request_id"] == "req-123"

    def test_json_formatter_prefers_exc_text_when_present(self):
        from dbwarden.logging import JSONFormatter

        fmt = JSONFormatter()
        record = logging.LogRecord(
            name="dbwarden",
            level=logging.ERROR,
            pathname="test.py",
            lineno=1,
            msg="boom",
            args=(),
            exc_info=None,
        )
        record.exc_text = "cached traceback text"

        output = fmt.format(record)
        parsed = json.loads(output)

        assert parsed["exception"] == "cached traceback text"

    def test_use_json_logging_env_var(self, monkeypatch):
        monkeypatch.setenv("DBWARDEN_LOG_JSON", "true")
        from dbwarden.logging import _use_json_logging

        assert _use_json_logging() is True

    def test_use_json_logging_default_false(self, monkeypatch):
        monkeypatch.delenv("DBWARDEN_LOG_JSON", raising=False)
        from dbwarden.logging import _use_json_logging

        assert _use_json_logging() is False

    def test_use_json_logging_accepts_1_and_yes(self, monkeypatch):
        from dbwarden.logging import _use_json_logging

        for val in ("1", "yes", "TRUE", "YES"):
            monkeypatch.setenv("DBWARDEN_LOG_JSON", val)
            assert _use_json_logging() is True

    def test_logger_uses_json_formatter_when_env_set(self, monkeypatch):
        monkeypatch.setenv("DBWARDEN_LOG_JSON", "true")
        from dbwarden.logging import JSONFormatter, reset_logger

        reset_logger()
        logger = get_logger()
        handler = logger.logger.handlers[0]
        assert isinstance(handler.formatter, JSONFormatter)
        reset_logger()

    def test_logger_uses_rich_handler_when_json_not_set(self, monkeypatch):
        monkeypatch.delenv("DBWARDEN_LOG_JSON", raising=False)
        from rich.logging import RichHandler
        from dbwarden.logging import reset_logger

        reset_logger()
        logger = get_logger()
        handler = logger.logger.handlers[0]
        assert isinstance(handler, RichHandler)
        reset_logger()

    def test_logger_json_output_round_trip(self, monkeypatch):
        import io

        monkeypatch.setenv("DBWARDEN_LOG_JSON", "true")
        from dbwarden.logging import reset_logger

        reset_logger()
        captured = io.StringIO()
        old_stdout = sys.stdout
        sys.stdout = captured

        try:
            logger = get_logger()
            handler = logger.logger.handlers[0]
            old_stream = handler.stream
            handler.stream = captured

            logger.info("test message", extra={"db_name": "primary"})

            handler.stream = old_stream
            output = captured.getvalue().strip()
            parsed = json.loads(output)
            assert parsed["message"] == "test message"
            assert parsed["level"] == "INFO"
            assert parsed["db_name"] == "primary"
        finally:
            sys.stdout = old_stdout
            reset_logger()


class TestLogger:
    def test_logger_default_level(self):
        logger = DBWardenLogger(debug_enabled=False)
        assert logger.debug_enabled is False
        assert logger.logger.level == 20

    def test_logger_rejects_invalid_constructor_verbosity(self):
        with pytest.raises(TypeError, match="verbosity must be a Verbosity member"):
            DBWardenLogger(debug_enabled=False, verbosity="verbose")

    def test_logger_debug_enabled_level(self):
        logger = DBWardenLogger(debug_enabled=True)
        assert logger.debug_enabled is True
        assert logger.logger.level == 10

    def test_logger_verbose_flag_sets_verbose_verbosity(self):
        logger = DBWardenLogger(debug_enabled=False, verbose=True)

        assert logger.verbosity == Verbosity.VERBOSE

    def test_logger_set_debug_enabled(self):
        logger = DBWardenLogger(debug_enabled=False)
        logger.set_debug_enabled(True)
        assert logger.debug_enabled is True
        assert logger.logger.level == 10

    def test_logger_set_verbosity_rejects_invalid_value(self):
        logger = DBWardenLogger(debug_enabled=False)

        with pytest.raises(TypeError, match="verbosity must be a Verbosity member"):
            logger.set_verbosity("quiet")

        with pytest.raises(TypeError, match="verbose must be a bool"):
            logger.set_verbose("yes")

    def test_logger_info(self):
        logger = DBWardenLogger(debug_enabled=False)
        logger.info("Test message")

    def test_logger_error_and_critical_apply_default_stacklevel(self, monkeypatch):
        logger = DBWardenLogger(debug_enabled=False)
        calls = []

        monkeypatch.setattr(
            logger.logger,
            "error",
            lambda msg, *args, **kwargs: calls.append(("error", msg, kwargs)),
        )
        monkeypatch.setattr(
            logger.logger,
            "critical",
            lambda msg, *args, **kwargs: calls.append(("critical", msg, kwargs)),
        )

        logger.error("error message")
        logger.critical("critical message")

        assert calls == [
            ("error", "error message", {"stacklevel": 2}),
            ("critical", "critical message", {"stacklevel": 2}),
        ]

    def test_logger_exception_only_logs_when_exception_is_active(self, monkeypatch):
        logger = DBWardenLogger(debug_enabled=False)
        calls = []

        monkeypatch.setattr(
            logger.logger,
            "exception",
            lambda msg, *args, **kwargs: calls.append((msg, kwargs)),
        )

        logger.exception("no exception")

        try:
            raise ValueError("boom")
        except ValueError:
            logger.exception("with exception")

        assert calls == [("with exception", {"stacklevel": 2})]

    def test_logger_debug(self):
        logger = DBWardenLogger(debug_enabled=True)
        logger.debug("Debug message")
        logger.log_sql_statement("SELECT * FROM users")

    def test_logger_log_connection_init(self):
        logger = DBWardenLogger(debug_enabled=False)
        logger.log_connection_init("postgresql")
        assert logger.db_type == "postgresql"

    def test_logger_log_connection_init_without_db_type_preserves_existing_type(self):
        logger = DBWardenLogger(debug_enabled=False, db_type="sqlite")

        logger.log_connection_init()

        assert logger.db_type == "sqlite"

    def test_logger_log_pending_migrations(self):
        logger = DBWardenLogger(debug_enabled=False)
        logger.log_pending_migrations(["V1__init.sql", "V2__add_users.sql"])

    def test_logger_log_pending_migrations_empty_list_is_noop(self, monkeypatch):
        logger = DBWardenLogger(debug_enabled=False)
        calls = []

        monkeypatch.setattr(
            logger,
            "_log_best_candidate",
            lambda *args, **kwargs: calls.append((args, kwargs)),
        )

        logger.log_pending_migrations([])

        assert calls == []

    def test_logger_log_migration_start(self):
        logger = DBWardenLogger(debug_enabled=False)
        logger.log_migration_start("0001", "V1__init.sql")

    def test_logger_log_migration_end(self):
        logger = DBWardenLogger(debug_enabled=False)
        logger.log_migration_end("0001", "V1__init.sql", 0.05)

    def test_logger_log_rollback_end(self):
        logger = DBWardenLogger(debug_enabled=False)
        logger.log_rollback_end("0001", "V1__init.sql", 0.03)

    def test_logger_log_rollback_start(self):
        logger = DBWardenLogger(debug_enabled=False)
        records = []

        class CaptureHandler(logging.Handler):
            def emit(self, record):
                records.append(record)

        logger.logger.handlers = [CaptureHandler()]
        logger.logger.propagate = False

        logger.log_rollback_start("0001", "V1__init.sql")

        assert len(records) == 1
        assert records[0].getMessage() == (
            "Rolling back migration: V1__init.sql (version: 0001)"
        )

    def test_logger_log_backup_created(self):
        logger = DBWardenLogger(debug_enabled=False)
        logger.log_backup_created("/path/to/backup.db")

    def test_logger_log_baseline_set(self):
        logger = DBWardenLogger(debug_enabled=False)
        logger.log_baseline_set("0001")

    def test_logger_log_seed_migration(self):
        logger = DBWardenLogger(debug_enabled=False)
        records = []

        class CaptureHandler(logging.Handler):
            def emit(self, record):
                records.append(record)

        logger.logger.handlers = [CaptureHandler()]
        logger.logger.propagate = False

        logger.log_seed_migration("V0001__seed.sql")

        assert len(records) == 1
        assert "Seed data applied: V0001__seed.sql" in records[0].getMessage()

    def test_get_logger_returns_same_instance(self):
        logger1 = get_logger()
        logger2 = get_logger()
        assert logger1 is logger2

    def test_get_logger_updates_existing_singleton_debug_enabled_and_verbosity(self):
        from dbwarden.logging import reset_logger

        reset_logger()
        try:
            logger1 = get_logger(
                debug_enabled=False,
                verbosity=Verbosity.NORMAL,
            )
            logger2 = get_logger(
                debug_enabled=True,
                verbosity=Verbosity.VERBOSE,
            )
            logger3 = get_logger(
                debug_enabled=True,
                verbosity=Verbosity.VERBOSE,
            )

            assert logger1 is logger2
            assert logger2 is logger3
            assert logger2.debug_enabled is True
            assert logger2.verbosity == Verbosity.VERBOSE
            assert logger2.logger.level == logging.DEBUG
        finally:
            reset_logger()

    def test_get_logger_promotes_existing_singleton_to_verbose_from_flag(self):
        from dbwarden.logging import reset_logger

        reset_logger()
        try:
            logger1 = get_logger(verbose=False, verbosity=Verbosity.NORMAL)
            logger2 = get_logger(verbose=True)

            assert logger1 is logger2
            assert logger2.verbosity == Verbosity.VERBOSE
        finally:
            reset_logger()

    def test_log_best_candidate_prefers_debug_candidate_when_debug_enabled(self, monkeypatch):
        logger = DBWardenLogger(debug_enabled=True, verbosity=Verbosity.QUIET)
        calls = []

        monkeypatch.setattr(
            logger,
            "debug",
            lambda msg, *args, **kwargs: calls.append(("debug", msg, kwargs)),
        )
        monkeypatch.setattr(
            logger,
            "info",
            lambda msg, *args, **kwargs: calls.append(("info", msg, kwargs)),
        )

        logger._log_best_candidate(
            [
                LogCandidate(
                    logging.INFO,
                    lambda: "quiet info",
                    Verbosity.QUIET,
                ),
                LogCandidate(
                    logging.DEBUG,
                    lambda: "debug message wins",
                ),
                LogCandidate(
                    logging.INFO,
                    lambda: "verbose info",
                    Verbosity.VERBOSE,
                ),
            ]
        )

        assert calls == [("debug", "debug message wins", {"stacklevel": 3})]

    def test_log_best_candidate_falls_back_to_info_when_debug_enabled_but_no_debug_candidate(
        self,
        monkeypatch,
    ):
        logger = DBWardenLogger(debug_enabled=True, verbosity=Verbosity.NORMAL)
        calls = []

        monkeypatch.setattr(
            logger,
            "debug",
            lambda msg, *args, **kwargs: calls.append(("debug", msg, kwargs)),
        )
        monkeypatch.setattr(
            logger,
            "info",
            lambda msg, *args, **kwargs: calls.append(("info", msg, kwargs)),
        )

        logger._log_best_candidate(
            [
                LogCandidate(
                    logging.INFO,
                    lambda: "normal info",
                    Verbosity.NORMAL,
                ),
            ]
        )

        assert calls == [("info", "normal info", {"stacklevel": 3})]

    @pytest.mark.parametrize(
        ("verbosity", "expected_message"),
        [
            (Verbosity.QUIET, "quiet info"),
            (Verbosity.NORMAL, "normal info"),
            (Verbosity.VERBOSE, "verbose info"),
        ],
    )
    def test_log_best_candidate_selects_highest_allowed_info_candidate_for_current_verbosity(
        self,
        monkeypatch,
        verbosity,
        expected_message,
    ):
        logger = DBWardenLogger(debug_enabled=False, verbosity=verbosity)
        calls = []

        monkeypatch.setattr(
            logger,
            "debug",
            lambda msg, *args, **kwargs: calls.append(("debug", msg, kwargs)),
        )
        monkeypatch.setattr(
            logger,
            "info",
            lambda msg, *args, **kwargs: calls.append(("info", msg, kwargs)),
        )

        logger._log_best_candidate(
            [
                LogCandidate(
                    logging.INFO,
                    lambda: "quiet info",
                    Verbosity.QUIET,
                ),
                LogCandidate(
                    logging.INFO,
                    lambda: "normal info",
                    Verbosity.NORMAL,
                ),
                LogCandidate(
                    logging.INFO,
                    lambda: "verbose info",
                    Verbosity.VERBOSE,
                ),
            ]
        )

        assert calls == [("info", expected_message, {"stacklevel": 3})]

    def test_log_best_candidate_returns_when_info_is_disabled(self, monkeypatch):
        logger = DBWardenLogger(debug_enabled=False, verbosity=Verbosity.NORMAL)
        logger.logger.setLevel(logging.WARNING)
        calls = []

        monkeypatch.setattr(
            logger,
            "info",
            lambda msg, *args, **kwargs: calls.append(("info", msg, kwargs)),
        )

        logger._log_best_candidate(
            [
                LogCandidate(
                    logging.INFO,
                    lambda: "normal info",
                    Verbosity.NORMAL,
                ),
            ]
        )

        assert calls == []

    def test_sql_debug_helper_emits_single_debug_record_with_header_and_sql_body(self):
        logger = DBWardenLogger(debug_enabled=True)
        records = []

        class CaptureHandler(logging.Handler):
            def emit(self, record):
                records.append(record)

        logger.logger.handlers = [CaptureHandler()]
        logger.logger.propagate = False

        logger.log_sql_statement("SELECT *\nFROM users")

        assert len(records) == 1
        message = records[0].getMessage()
        assert "SQL:" in message
        assert "SELECT *" in message
        assert "FROM users" in message

    def test_logger_wrapper_supports_positional_format_args(self):
        logger = DBWardenLogger(debug_enabled=False)
        records = []

        class CaptureHandler(logging.Handler):
            def emit(self, record):
                records.append(record)

        logger.logger.handlers = [CaptureHandler()]
        logger.logger.propagate = False

        logger.info("hello %s", "world")

        assert len(records) == 1
        assert records[0].getMessage() == "hello world"

    def test_logger_wrapper_preserves_explicit_stacklevel(self, monkeypatch):
        logger = DBWardenLogger(debug_enabled=False)
        captured = {}

        def fake_info(msg, *args, **kwargs):
            captured["msg"] = msg
            captured["args"] = args
            captured["kwargs"] = kwargs

        monkeypatch.setattr(logger.logger, "info", fake_info)

        logger.info("x", stacklevel=7)

        assert captured["msg"] == "x"
        assert captured["args"] == ()
        assert captured["kwargs"]["stacklevel"] == 7

    def test_format_db_context_with_db_name_only(self, monkeypatch):
        logger = DBWardenLogger(debug_enabled=False, db_name="primary")

        monkeypatch.setattr(
            "dbwarden.logging.colorize",
            lambda text, color: f"<{color}>{text}</{color}>",
        )

        assert logger._format_db_context() == "[<\033[36m>primary</\033[36m>]"

    def test_format_db_context_with_db_type_only(self, monkeypatch):
        logger = DBWardenLogger(debug_enabled=False, db_type="postgresql")

        monkeypatch.setattr(
            "dbwarden.logging.colorize",
            lambda text, color: f"<{color}>{text}</{color}>",
        )

        assert logger._format_db_context() == "[<\033[35m>postgresql</\033[35m>]"

    def test_logging_helper_uses_callsite_stacklevel_four(self):
        logger = DBWardenLogger(debug_enabled=False)
        records = []

        class CaptureHandler(logging.Handler):
            def emit(self, record):
                records.append(record)

        logger.logger.handlers = [CaptureHandler()]
        logger.logger.propagate = False

        def helper_call():
            expected_lineno = sys._getframe().f_lineno + 1
            logger.log_migration_start("0001", "V1__init.sql")
            return expected_lineno

        expected_lineno = helper_call()

        assert len(records) == 1
        record = records[0]
        assert record.pathname.endswith("test_logging.py")
        assert record.lineno == expected_lineno

    def test_log_table_columns_emits_debug_message(self):
        logger = DBWardenLogger(debug_enabled=True)
        records = []

        class CaptureHandler(logging.Handler):
            def emit(self, record):
                records.append(record)

        logger.logger.handlers = [CaptureHandler()]
        logger.logger.propagate = False

        logger.log_table_columns(
            "users",
            [{"name": "id"}, {"name": "email"}],
        )

        assert len(records) == 1
        assert records[0].getMessage() == "Table users columns: id, email"

    def test_log_migration_sql_gen_emits_debug_message(self):
        logger = DBWardenLogger(debug_enabled=True)
        records = []

        class CaptureHandler(logging.Handler):
            def emit(self, record):
                records.append(record)

        logger.logger.handlers = [CaptureHandler()]
        logger.logger.propagate = False

        logger.log_migration_sql_gen("users", "SELECT 1")

        assert len(records) == 1
        assert "Generated SQL for users:" in records[0].getMessage()
        assert "SELECT 1" in records[0].getMessage()

    def test_reset_logger(self):
        from dbwarden.logging import reset_logger

        logger1 = get_logger()
        reset_logger()
        logger2 = get_logger()
        assert logger1 is not logger2


class TestDebugLevel:
    def test_constructor_accepts_exact_debug_level(self):
        logger = DBWardenLogger(debug_level=logging.WARNING)
        assert logger.debug_level == logging.WARNING
        assert logger.logger.level == logging.WARNING
        assert logger.debug_enabled is False

    def test_debug_level_takes_precedence_over_debug_enabled(self):
        logger = DBWardenLogger(debug_enabled=True, debug_level=logging.WARNING)
        assert logger.debug_level == logging.WARNING
        assert logger.logger.level == logging.WARNING
        assert logger.debug_enabled is False

    def test_debug_enabled_property_mirrors_level(self):
        assert DBWardenLogger(debug_level=logging.DEBUG).debug_enabled is True
        assert DBWardenLogger(debug_level=logging.INFO).debug_enabled is False
        assert DBWardenLogger(debug_level=logging.ERROR).debug_enabled is False

    def test_set_debug_level_updates_logger_and_handlers(self):
        logger = DBWardenLogger(debug_enabled=True)
        logger.set_debug_level(logging.WARNING)
        assert logger.debug_level == logging.WARNING
        assert logger.logger.level == logging.WARNING
        assert all(h.level == logging.WARNING for h in logger.logger.handlers)

    def test_set_debug_level_rejects_non_int(self):
        logger = DBWardenLogger(debug_enabled=False)
        with pytest.raises(TypeError, match="debug_level must be an int"):
            logger.set_debug_level("warning")

    def test_set_debug_enabled_delegates_to_level(self):
        logger = DBWardenLogger(debug_enabled=False)
        logger.set_debug_enabled(False)
        assert logger.debug_level == logging.INFO
        logger.set_debug_enabled(True)
        assert logger.debug_level == logging.DEBUG
        assert logger.debug_enabled is True

    def test_get_logger_syncs_debug_level_on_singleton(self):
        from dbwarden.logging import reset_logger

        reset_logger()
        try:
            get_logger(debug_level=logging.DEBUG)
            assert get_logger().debug_level == logging.DEBUG
            get_logger(debug_level=logging.WARNING)
            assert get_logger().debug_level == logging.WARNING
        finally:
            reset_logger()

    def test_get_logger_plain_call_does_not_clear_debug_level(self):
        from dbwarden.logging import reset_logger

        reset_logger()
        try:
            get_logger(debug_level=logging.DEBUG)
            get_logger()
            get_logger(verbose=True)
            get_logger(db_name="primary")
            assert get_logger().debug_level == logging.DEBUG
            assert get_logger().debug_enabled is True
        finally:
            reset_logger()

    def test_get_logger_debug_level_priority_over_debug_enabled(self):
        from dbwarden.logging import reset_logger

        reset_logger()
        try:
            get_logger(debug_enabled=True, debug_level=logging.WARNING)
            assert get_logger().debug_level == logging.WARNING
        finally:
            reset_logger()


class TestResolveDebugLevel:
    def test_resolves_named_levels_case_insensitively(self):
        from dbwarden.logging import resolve_debug_level

        assert resolve_debug_level("debug") == logging.DEBUG
        assert resolve_debug_level("DEBUG") == logging.DEBUG
        assert resolve_debug_level("info") == logging.INFO
        assert resolve_debug_level("warning") == logging.WARNING
        assert resolve_debug_level("warn") == logging.WARNING
        assert resolve_debug_level("ERROR") == logging.ERROR
        assert resolve_debug_level("critical") == logging.CRITICAL

    @pytest.mark.parametrize("value", ["10", "20", "30", "40", "50"])
    def test_resolves_numeric_levels(self, value):
        from dbwarden.logging import resolve_debug_level

        assert resolve_debug_level(value) == int(value)

    @pytest.mark.parametrize("value", ["foo", "12", "0", "60", ""])
    def test_invalid_level_raises_value_error(self, value):
        from dbwarden.logging import resolve_debug_level

        with pytest.raises(ValueError, match="Invalid debug level"):
            resolve_debug_level(value)

    def test_whitespace_around_level_is_ignored(self):
        from dbwarden.logging import resolve_debug_level

        assert resolve_debug_level(" info ") == logging.INFO


class TestScanLogging:
    def test_log_model_file_scanned_emits_debug_message(self):
        logger = DBWardenLogger(debug_enabled=True)
        records = []

        class CaptureHandler(logging.Handler):
            def emit(self, record):
                records.append(record)

        logger.logger.handlers = [CaptureHandler()]
        logger.logger.propagate = False

        logger.log_model_file_scanned("/tmp/models/user.py")

        assert len(records) == 1
        assert records[0].getMessage() == "Scanning model file: /tmp/models/user.py"

    def test_log_model_file_load_failed_emits_debug_message(self):
        logger = DBWardenLogger(debug_enabled=True)
        records = []

        class CaptureHandler(logging.Handler):
            def emit(self, record):
                records.append(record)

        logger.logger.handlers = [CaptureHandler()]
        logger.logger.propagate = False

        logger.log_model_file_load_failed("/tmp/missing.py", "file not found")

        assert len(records) == 1
        assert records[0].getMessage() == (
            "Failed to load model file: /tmp/missing.py (file not found)"
        )


class TestNonTTYColorStripping:
    """ANSI helpers must emit plain text when the terminal is not a TTY."""

    def test_colorize_strips_ansi_when_no_tty(self, monkeypatch):
        from dbwarden import logging as logging_module

        monkeypatch.setattr(logging_module, "supports_color", lambda: False)

        assert logging_module.colorize("hello", logging_module.ANSI_COLORS["red"]) == "hello"

    def test_colorize_status_strips_ansi_when_no_tty(self, monkeypatch):
        from dbwarden import logging as logging_module

        monkeypatch.setattr(logging_module, "supports_color", lambda: False)

        assert logging_module.colorize_status("applied") == "[APPLIED]"

    def test_colorize_sql_strips_ansi_when_no_tty(self, monkeypatch):
        from dbwarden import logging as logging_module

        monkeypatch.setattr(logging_module, "supports_color", lambda: False)

        assert logging_module.colorize_sql("SELECT 1 -- note") == "SELECT 1 -- note"

    def test_colored_formatter_emits_plain_text_when_no_tty(self, monkeypatch):
        from dbwarden import logging as logging_module

        monkeypatch.setattr(logging_module, "supports_color", lambda: False)

        formatter = logging_module.ColoredFormatter("%(message)s")
        record = logging.LogRecord(
            name="dbwarden",
            level=logging.ERROR,
            pathname="test.py",
            lineno=1,
            msg="boom",
            args=(),
            exc_info=None,
        )

        output = formatter.format(record)

        assert output == "boom"
        assert "\033[" not in output


class TestMultithreadedLogging:
    """Logging through a shared DBWardenLogger must be safe across threads."""

    @staticmethod
    def _queue_logger(logger, queue):
        class QueueHandler(logging.Handler):
            def emit(self, record):
                queue.put(record)

        logger.logger.handlers = [QueueHandler()]
        logger.logger.propagate = False

    def test_concurrent_info_logging_captures_all_records(self):
        import queue
        import threading

        records = queue.Queue()
        logger = DBWardenLogger(debug_enabled=True)
        self._queue_logger(logger, records)
        errors = []

        def worker(n):
            try:
                for i in range(50):
                    logger.info(f"msg {n}-{i}")
            except Exception as exc:  # pragma: no cover - failure surface
                errors.append(exc)

        threads = [threading.Thread(target=worker, args=(n,)) for n in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        assert not errors
        assert records.qsize() == 8 * 50

    def test_concurrent_json_logging_produces_valid_json(self, monkeypatch):
        import queue
        import threading

        from dbwarden.logging import JSONFormatter, reset_logger

        reset_logger()
        monkeypatch.setenv("DBWARDEN_LOG_JSON", "true")
        try:
            records = queue.Queue()
            logger = get_logger(debug_enabled=True)
            self._queue_logger(logger, records)
            formatter = JSONFormatter()
            for handler in logger.logger.handlers:
                handler.setFormatter(formatter)
            errors = []

            def worker(n):
                try:
                    for i in range(20):
                        logger.info(f"json {n}-{i}")
                except Exception as exc:  # pragma: no cover - failure surface
                    errors.append(exc)

            threads = [threading.Thread(target=worker, args=(n,)) for n in range(6)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()

            assert not errors
            captured = [records.get() for _ in range(6 * 20)]
            for record in captured:
                parsed = json.loads(formatter.format(record))
                assert parsed["level"] == "INFO"
                assert "message" in parsed
        finally:
            reset_logger()


class TestGetLoggerThreadSafety:
    """get_logger() must initialize exactly once under concurrency."""

    def test_concurrent_initialization_returns_same_instance(self):
        import threading

        from dbwarden.logging import reset_logger

        reset_logger()
        try:
            results = []

            def worker():
                results.append(
                    get_logger(
                        debug_level=logging.DEBUG,
                        verbosity=Verbosity.VERBOSE,
                        db_name="primary",
                    )
                )

            threads = [threading.Thread(target=worker) for _ in range(20)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()

            assert len({id(r) for r in results}) == 1
            singleton = results[0]
            assert len(singleton.logger.handlers) == 1
            assert singleton.db_name == "primary"
        finally:
            reset_logger()

    def test_concurrent_reset_and_get_does_not_crash(self):
        import threading

        from dbwarden.logging import reset_logger

        reset_logger()
        try:
            errors = []

            def getter():
                try:
                    for _ in range(25):
                        get_logger()
                except Exception as exc:  # pragma: no cover - failure surface
                    errors.append(exc)

            def reseter():
                try:
                    for _ in range(25):
                        reset_logger()
                except Exception as exc:  # pragma: no cover - failure surface
                    errors.append(exc)

            threads = [threading.Thread(target=getter) for _ in range(3)] + [
                threading.Thread(target=reseter) for _ in range(3)
            ]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()

            assert not errors
            assert get_logger() is not None
        finally:
            reset_logger()


class TestTraceLevel:
    """TRACE level plumbing: level registration, method, and formatter."""

    def test_trace_level_registered_with_stdlib(self):
        from dbwarden.logging import TRACE_LEVEL

        assert TRACE_LEVEL == 5
        assert logging.getLevelName(TRACE_LEVEL) == "TRACE"

    def test_trace_method_emits_trace_record(self):
        from dbwarden.logging import TRACE_LEVEL

        logger = DBWardenLogger(debug_level=TRACE_LEVEL)
        records = []

        class CaptureHandler(logging.Handler):
            def emit(self, record):
                records.append(record)

        logger.logger.handlers = [CaptureHandler()]
        logger.logger.propagate = False

        logger.trace("fine detail")

        assert len(records) == 1
        assert records[0].levelno == TRACE_LEVEL
        assert records[0].getMessage() == "fine detail"

    def test_trace_method_suppressed_above_trace_level(self):
        logger = DBWardenLogger(debug_enabled=False)
        records = []

        class CaptureHandler(logging.Handler):
            def emit(self, record):
                records.append(record)

        logger.logger.handlers = [CaptureHandler()]
        logger.logger.propagate = False

        logger.trace("hidden")

        assert records == []

    def test_log_sql_trace_emits_when_enabled(self):
        from dbwarden.logging import TRACE_LEVEL

        logger = DBWardenLogger(debug_level=TRACE_LEVEL)
        records = []

        class CaptureHandler(logging.Handler):
            def emit(self, record):
                records.append(record)

        logger.logger.handlers = [CaptureHandler()]
        logger.logger.propagate = False

        logger.log_sql_trace("CREATE TABLE t (id int)")

        assert len(records) == 1
        assert records[0].levelno == TRACE_LEVEL
        assert records[0].getMessage() == "CREATE TABLE t (id int)"

    def test_log_sql_trace_suppressed_above_trace_level(self):
        logger = DBWardenLogger(debug_enabled=False)
        records = []

        class CaptureHandler(logging.Handler):
            def emit(self, record):
                records.append(record)

        logger.logger.handlers = [CaptureHandler()]
        logger.logger.propagate = False

        logger.log_sql_trace("SELECT 1")

        assert records == []

    def test_resolve_debug_level_accepts_trace(self):
        from dbwarden.logging import TRACE_LEVEL, resolve_debug_level

        assert resolve_debug_level("trace") == TRACE_LEVEL
        assert resolve_debug_level("TRACE") == TRACE_LEVEL
        assert resolve_debug_level("5") == TRACE_LEVEL

    def test_json_formatter_renders_trace_level(self):
        from dbwarden.logging import JSONFormatter, TRACE_LEVEL

        fmt = JSONFormatter()
        record = logging.LogRecord(
            name="dbwarden",
            level=TRACE_LEVEL,
            pathname="t.py",
            lineno=1,
            msg="sql dump",
            args=(),
            exc_info=None,
        )

        parsed = json.loads(fmt.format(record))

        assert parsed["level"] == "TRACE"
