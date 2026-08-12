"""
Logging utilities with severity-based levels and configurable verbosity.

This module builds on Python's standard logging library rather than modifying it. Standard logging levels DEBUG, INFO, WARNING, ERROR, and CRITICAL continue to represent message severity.

In addition to severity, this module introduces a seperate verbosity concept for user-facing INFO output. Verbosity controls how much detail is emitted without changing the semantic meaning of standard logging levels.

The supported verbosity levels are:
    QUIET: Emit only essential INFO messages and details.
    NORMAL: Emit standard INFO progress messages.
    VERBOSE: Emit additional explanatory or diagnostic informational messages.

This seperation allows callers to distinguish between dev diagnostics (DEBUG) and detailed user-facing output (INFO gated by verbosity).

Verbosity may be configured from multiple sources, such as constructor arguments, config files, env vars, or CLI options.

"""

import json
import logging
import os
import re
import sys
import threading
from datetime import datetime, timezone
from typing import Any, Optional
from enum import IntEnum
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from rich.logging import RichHandler

from dbwarden.constants import LOG_FORMAT
from dbwarden.output import console


TRACE_LEVEL = 5

if not hasattr(logging, "TRACE"):
    logging.TRACE = TRACE_LEVEL
logging.addLevelName(TRACE_LEVEL, "TRACE")


class Verbosity(IntEnum):
    QUIET = 0
    NORMAL = 1
    VERBOSE = 2

_RESERVED_EXTRA_FIELDS = frozenset({
    "args",
    "asctime",
    "created",
    "exc_info",
    "exc_text",
    "filename",
    "funcName",
    "levelname",
    "levelno",
    "lineno",
    "message",
    "module",
    "msecs",
    "msg",
    "name",
    "pathname",
    "process",
    "processName",
    "relativeCreated",
    "stack_info",
    "thread",
    "threadName",
    # DBWarden JSON payload keys
    "timestamp",
    "level",
    "logger",
    "exception",
    "db_name",
    "db_type",
})

ANSI_COLORS = {
    "reset": "\033[0m",
    "bold": "\033[1m",
    "dim": "\033[2m",
    "italic": "\033[3m",
    "underline": "\033[4m",
    "black": "\033[30m",
    "red": "\033[31m",
    "green": "\033[32m",
    "yellow": "\033[33m",
    "blue": "\033[34m",
    "magenta": "\033[35m",
    "cyan": "\033[36m",
    "white": "\033[37m",
    "bg_red": "\033[41m",
    "bg_green": "\033[42m",
}

STATUS_COLORS = {
    "PENDING": ANSI_COLORS["yellow"],
    "APPLIED": ANSI_COLORS["green"],
    "ROLLED_BACK": ANSI_COLORS["red"],
    "FAILED": ANSI_COLORS["red"],
    "RUNNING": ANSI_COLORS["blue"],
    "SKIPPED": ANSI_COLORS["dim"] + ANSI_COLORS["yellow"],
}

LOG_COLORS = {
    logging.DEBUG: ANSI_COLORS["dim"] + ANSI_COLORS["cyan"],
    logging.INFO: ANSI_COLORS["reset"],
    logging.WARNING: ANSI_COLORS["yellow"],
    logging.ERROR: ANSI_COLORS["red"],
    logging.CRITICAL: ANSI_COLORS["bold"] + ANSI_COLORS["red"],
}


def supports_color() -> bool:
    """Check if the terminal supports ANSI colors."""
    return sys.stdout.isatty()


def colorize(text: str, color: str) -> str:
    """Wrap text with ANSI color codes."""
    if not supports_color():
        return text
    return f"{color}{text}{ANSI_COLORS['reset']}"


def colorize_status(status: str) -> str:
    """Colorize a status string with appropriate color."""
    color = STATUS_COLORS.get(status.upper(), ANSI_COLORS["reset"])
    return colorize(f"[{status.upper()}]", color)


SQL_KEYWORDS = [
    "CREATE",
    "TABLE",
    "INDEX",
    "VIEW",
    "DROP",
    "ALTER",
    "INSERT",
    "UPDATE",
    "DELETE",
    "SELECT",
    "FROM",
    "WHERE",
    "AND",
    "OR",
    "JOIN",
    "LEFT",
    "RIGHT",
    "INNER",
    "OUTER",
    "ON",
    "ORDER",
    "BY",
    "GROUP",
    "HAVING",
    "LIMIT",
    "OFFSET",
    "PRIMARY",
    "KEY",
    "FOREIGN",
    "REFERENCES",
    "NOT",
    "NULL",
    "UNIQUE",
    "CHECK",
    "DEFAULT",
    "CONSTRAINT",
    "IF",
    "EXISTS",
    "BEGIN",
    "COMMIT",
    "ROLLBACK",
    "TRANSACTION",
    "VALUES",
    "SET",
    "AS",
    "IN",
    "IS",
    "LIKE",
    "BETWEEN",
    "CAST",
    "UNION",
    "ALL",
    "DISTINCT",
    "ASC",
    "DESC",
    "INTO",
    "TEMPORARY",
    "INTEGER",
    "VARCHAR",
    "TEXT",
    "BOOLEAN",
    "DATETIME",
    "FLOAT",
    "DATE",
    "JSON",
    "SERIAL",
    "BIGINT",
    "SMALLINT",
    "NUMERIC",
]


def colorize_sql(sql: str) -> str:
    """Apply basic SQL syntax highlighting."""
    if not supports_color():
        return sql

    result = re.sub(
        r"\b(" + "|".join(re.escape(keyword) for keyword in SQL_KEYWORDS) + r")\b",
        lambda m: colorize(m.group(0), ANSI_COLORS["magenta"]),
        sql,
        flags=re.IGNORECASE,
    )

    result = re.sub(
        r"(?<!\\)'(?:[^'\\]|\\.)*+'",
        lambda m: colorize(m.group(0), ANSI_COLORS["green"]),
        result,
    )

    result = re.sub(
        r"--.*$",
        lambda m: colorize(m.group(0), ANSI_COLORS["dim"] + ANSI_COLORS["cyan"]),
        result,
        flags=re.MULTILINE,
    )

    return result


class ColoredFormatter(logging.Formatter):
    """Custom formatter that adds colors to log output."""

    def formatMessage(self, record: logging.LogRecord) -> str:
        msg = super().formatMessage(record)
        if supports_color():
            color = LOG_COLORS.get(record.levelno, ANSI_COLORS["reset"])
            msg = colorize(msg, color)
        return msg


class JSONFormatter(logging.Formatter):
    """Formats log records as newline-delimited JSON objects.

    Activated when the ``DBWARDEN_LOG_JSON`` environment variable is set.
    """

    _STANDARD_RECORD_FIELDS = frozenset(logging.makeLogRecord({}).__dict__)
    _RESERVED_EXTRA_FIELDS = _STANDARD_RECORD_FIELDS | {
        "asctime",
        "message",
        "timestamp",
        "level",
        "logger",
        "exception",
        "db_name",
        "db_type",
    }

    def formatTime(
        self,
        record: logging.LogRecord,
        datefmt: str | None = None,
    ) -> str:
        dt = datetime.fromtimestamp(record.created, timezone.utc)
        if datefmt:
            return dt.strftime(datefmt)
        return dt.isoformat(timespec="milliseconds")

    def _extract_extra_fields(self, record: logging.LogRecord) -> dict[str, Any]:
        extras: dict[str, Any] = {}
        for key, value in record.__dict__.items():
            if key in self._RESERVED_EXTRA_FIELDS:
                continue
            extras[key] = value
        return extras

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": self.formatTime(record, "%Y-%m-%dT%H:%M:%S.%fZ"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if hasattr(record, "db_name") and record.db_name:
            payload["db_name"] = record.db_name
        if hasattr(record, "db_type") and record.db_type:
            payload["db_type"] = record.db_type
        payload.update(self._extract_extra_fields(record))
        if (
            record.exc_info
            and isinstance(record.exc_info, tuple)
            and record.exc_info[0]
        ):
            payload["exception"] = self.formatException(record.exc_info)
        if record.exc_text:
            payload["exception"] = record.exc_text
        return json.dumps(payload, default=str)


def _use_json_logging() -> bool:
    return os.environ.get("DBWARDEN_LOG_JSON", "false").lower() in (
        "true",
        "1",
        "yes",
    )


LogMessage = Callable[[], str]

@dataclass(frozen=True)
class LogCandidate:
    """Candidate log entry keyed by a stdlib Python logging level.

    log_severity_level intentionally uses int because Python's logging
    module defines levels like logging.DEBUG and logging.INFO as integer
    constants. Storing the raw stdlib level keeps this class directly
    compatible with logging APIs and avoids wrapping or translating
    those values through a separate enum.
    """

    log_severity_level: int
    message_factory: LogMessage
    log_verbosity_level: Verbosity = Verbosity.NORMAL


class DBWardenLogger:
    """
    Structured logging for DBWarden operations.

    Provides configurable logging levels and colored output
    for migration operations with database context.
    """

    Verbosity = Verbosity

    def __init__(
        self,
        name: str = "dbwarden",
        verbose: bool = False,
        debug_enabled: bool = False,
        debug_level: int | None = None,
        verbosity: Verbosity | None = None,
        db_name: str | None = None,
        db_type: str | None = None,
    ) -> None:
        """
        Initialize the DBWarden logger.

        Args:
            name: Logger name (default: "dbwarden")
            verbose: Legacy compatibility flag. When ``verbosity`` is not set,
                ``True`` maps to ``Verbosity.VERBOSE`` and ``False`` falls back
                to ``Verbosity.NORMAL``.
            debug_enabled: Switches between ``logging.DEBUG`` when true and
                ``logging.INFO`` when false. Kept for backward compatibility;
                ``debug_level`` takes precedence when both are provided.
            debug_level: Exact stdlib logging level (e.g. ``logging.DEBUG``,
                ``logging.WARNING``). When ``None``, falls back to
                ``debug_enabled``.
            verbosity: Explicit INFO-output verbosity. When provided, it takes
                precedence over ``verbose``.
            db_name: Database name from multi-db config.
            db_type: Database type (sqlite, postgresql, mysql, mariadb, clickhouse).
        """
        self.logger = logging.getLogger(name)
        self.verbose = verbose
        if debug_level is not None:
            self.debug_level = debug_level
        else:
            self.debug_level = logging.DEBUG if debug_enabled else logging.INFO

        if verbosity is not None and not isinstance(verbosity, Verbosity):
            raise TypeError(
                f"verbosity must be a Verbosity member, got {type(verbosity)}"
            )

        if verbosity is not None:
            self.verbosity = verbosity
        elif verbose:
            self.verbosity = Verbosity.VERBOSE
        else:
            self.verbosity = Verbosity.NORMAL
        self.db_name = db_name
        self.db_type = db_type
        self._setup_logger()

    @property
    def debug_enabled(self) -> bool:
        """Whether the logger severity is at ``logging.DEBUG`` level."""
        return self.debug_level == logging.DEBUG

    def _setup_logger(self) -> None:
        """Configure the logger with appropriate handlers and level."""
        self.logger.setLevel(self.debug_level)

        for handler in self.logger.handlers[:]:
            self.logger.removeHandler(handler)

        if _use_json_logging():
            handler = logging.StreamHandler(sys.stdout)
            handler.setLevel(self.debug_level)
            formatter: logging.Formatter = JSONFormatter()
            handler.setFormatter(formatter)
        else:
            handler = RichHandler(
                console=console,
                rich_tracebacks=True,
                show_path=False,
                markup=False,
            )
            handler.setLevel(self.debug_level)
            handler.setFormatter(logging.Formatter("%(name)s - %(message)s"))
        self.logger.addHandler(handler)

    def _apply_logging_defaults(self, kwargs: dict[str, Any]) -> dict[str, Any]:
        return dict(kwargs, stacklevel=kwargs.get("stacklevel", 2))

    def set_debug_enabled(self, debug_enabled: bool) -> None:
        """Enable or disable DEBUG severity (backward-compatible helper)."""
        self.set_debug_level(logging.DEBUG if debug_enabled else logging.INFO)

    def set_debug_level(self, debug_level: int) -> None:
        """Set the exact stdlib logging severity level for this logger."""
        if not isinstance(debug_level, int):
            raise TypeError(
                f"debug_level must be an int logging level, got {type(debug_level)}"
            )

        self.debug_level = debug_level
        self.logger.setLevel(debug_level)
        for handler in self.logger.handlers:
            handler.setLevel(debug_level)

    def set_verbosity(self, verbosity: Verbosity) -> None:
        if not isinstance(verbosity, Verbosity):
            raise TypeError(
                f"verbosity must be a Verbosity member, got {type(verbosity)}"
            )

        self.verbosity = verbosity

    def set_verbose(self, verbose: bool) -> None:
        if not isinstance(verbose, bool):
            raise TypeError(f"verbose must be a bool, got {type(verbose)}")

        self.verbose = verbose
        self.verbosity = Verbosity.VERBOSE if verbose else Verbosity.NORMAL

    def debug(self, msg: str, *args, **kwargs) -> None:
        """Log a debug message."""
        logging_kwargs = self._apply_logging_defaults(kwargs)
        self.logger.debug(msg, *args, **logging_kwargs)

    def trace(self, msg: str, *args, **kwargs) -> None:
        """Log a trace message (finer-grained than DEBUG)."""
        logging_kwargs = self._apply_logging_defaults(kwargs)
        self.logger.log(TRACE_LEVEL, msg, *args, **logging_kwargs)

    def info(self, msg: str, *args, **kwargs) -> None:
        """Log an info message."""
        logging_kwargs = self._apply_logging_defaults(kwargs)
        self.logger.info(msg, *args, **logging_kwargs)

    def warning(self, msg: str, *args, **kwargs) -> None:
        """Log a warning message."""
        logging_kwargs = self._apply_logging_defaults(kwargs)
        self.logger.warning(msg, *args, **logging_kwargs)

    def error(self, msg: str, *args, **kwargs) -> None:
        """Log an error message."""
        logging_kwargs = self._apply_logging_defaults(kwargs)
        self.logger.error(msg, *args, **logging_kwargs)

    def critical(self, msg: str, *args, **kwargs) -> None:
        """Log a critical message."""
        logging_kwargs = self._apply_logging_defaults(kwargs)
        self.logger.critical(msg, *args, **logging_kwargs)

    def exception(self, msg: str, *args, **kwargs) -> None:
        """Log an exception message."""
        if sys.exc_info()[0] is not None:
            logging_kwargs = self._apply_logging_defaults(kwargs)
            self.logger.exception(msg, *args, **logging_kwargs)

    def _format_db_context(self) -> str:
        """Format database context for log messages with colors."""
        if self.db_name and self.db_type:
            return f"[{colorize(self.db_name, ANSI_COLORS['cyan'])}/{colorize(self.db_type, ANSI_COLORS['magenta'])}]"
        elif self.db_name:
            return f"[{colorize(self.db_name, ANSI_COLORS['cyan'])}]"
        elif self.db_type:
            return f"[{colorize(self.db_type, ANSI_COLORS['magenta'])}]"
        return ""

    def _prefixed(self, msg: str) -> str:
        ctx = self._format_db_context()
        return f"{ctx} {msg}" if ctx else msg

    def _log_best_candidate(
        self,
        candidates: Sequence[LogCandidate],
        *,
        stacklevel: int = 3,
    ) -> None:
        """Log the selected message variant for the current logger state.

        This helper currently assumes there is one log format for each of these
        cases: DEBUG, INFO at VERBOSE verbosity, INFO at NORMAL verbosity, and
        INFO at QUIET verbosity. It only considers DEBUG and INFO candidates
        today. WARNING, ERROR, and CRITICAL candidates could be added here
        later, but they are not part of the current selection logic.

        LogCandidate.log_severity_level is treated as a Python stdlib
        logging level integer here on purpose. Call sites can pass
        logging.DEBUG or logging.INFO directly, which matches what
        logging.Logger expects and keeps severity selection aligned with
        the standard library's level model.
        """
        if self.logger.isEnabledFor(logging.DEBUG):
            for candidate in candidates:
                if candidate.log_severity_level == logging.DEBUG:
                    message = candidate.message_factory()
                    self.debug(self._prefixed(message), stacklevel=stacklevel)
                    return

        if not self.logger.isEnabledFor(logging.INFO):
            return

        info_candidates = [
                candidate
                for candidate in candidates
                if candidate.log_severity_level == logging.INFO
                and self.verbosity >= candidate.log_verbosity_level
        ]

        # No try/except here: each logging helper defines its LogCandidate
        # set statically, so this branch expects well-formed internal
        # candidates rather than arbitrary user-provided input.
        if not info_candidates:
            return

        selected = max(
                info_candidates,
                key=lambda candidate: candidate.log_verbosity_level,
        )

        message = selected.message_factory()
        self.info(self._prefixed(message), stacklevel=stacklevel)

    def log_connection_init(self, db_type: str | None = None) -> None:
        """Log database connection initialization."""
        if db_type:
            self.db_type = db_type
        self._log_best_candidate(
            [
                LogCandidate(
                    logging.INFO,
                    lambda: "Database connection initialized",
                    Verbosity.NORMAL,
                ),
            ],
            stacklevel=4,
        )

    def log_pending_migrations(self, migrations: list[str]) -> None:
        """Log list of pending migrations."""
        if migrations:
            self._log_best_candidate(
                [
                    LogCandidate(
                        logging.INFO,
                        lambda: f"Pending migrations ({len(migrations)}):",
                        Verbosity.NORMAL,
                    ),
                ],
                stacklevel=4,
            )
            for m in migrations:
                self._log_best_candidate(
                    [
                        LogCandidate(
                            logging.INFO,
                            lambda m=m: (
                                f"  {colorize('[PENDING]', ANSI_COLORS['yellow'])} "
                                f"{colorize(m, ANSI_COLORS['white'])}"
                            ),
                            Verbosity.NORMAL,
                        ),
                    ],
                    stacklevel=4,
                )

    def log_migration_start(self, version: str, filename: str) -> None:
        """Log migration start."""
        self._log_best_candidate(
            [
                LogCandidate(
                    logging.INFO,
                    lambda: f"Starting migration: {filename} (version: {version})",
                    Verbosity.NORMAL,
                ),
            ],
            stacklevel=4,
        )

    def log_migration_end(self, version: str, filename: str, duration: float) -> None:
        """Log migration end with duration."""
        self._log_best_candidate(
            [
                LogCandidate(
                    logging.INFO,
                    lambda: (
                        f"{colorize('[APPLIED]', ANSI_COLORS['green'])} "
                        f"Completed migration: {colorize(filename, ANSI_COLORS['white'])} "
                        f"(version: {colorize(version, ANSI_COLORS['dim'] + ANSI_COLORS['cyan'])}) "
                        f"in {colorize(f'{duration:.2f}s', ANSI_COLORS['dim'])}"
                    ),
                    Verbosity.NORMAL,
                ),
            ],
            stacklevel=4,
        )

    def log_migration_skipped(self, version: str, filename: str, checksum: str) -> None:
        """Log migration skipped because checksum already applied."""
        self._log_best_candidate(
            [
                LogCandidate(
                    logging.INFO,
                    lambda: (
                        f"{colorize('[SKIPPED]', STATUS_COLORS['SKIPPED'])} "
                        f"Migration already applied: {colorize(filename, ANSI_COLORS['white'])} "
                        f"(version: {version}, checksum: {checksum[:16]}...)"
                    ),
                    Verbosity.NORMAL,
                ),
            ],
            stacklevel=4,
        )

    def log_rollback_start(self, version: str, filename: str) -> None:
        """Log rollback start."""
        self._log_best_candidate(
            [
                LogCandidate(
                    logging.INFO,
                    lambda: f"Rolling back migration: {filename} (version: {version})",
                    Verbosity.NORMAL,
                ),
            ],
            stacklevel=4,
        )

    def log_rollback_end(self, version: str, filename: str, duration: float) -> None:
        """Log rollback end with duration."""
        self._log_best_candidate(
            [
                LogCandidate(
                    logging.INFO,
                    lambda: (
                        f"{colorize('[ROLLED_BACK]', ANSI_COLORS['red'])} "
                        f"Rollback completed: {filename} (version: {version}) "
                        f"in {duration:.2f}s"
                    ),
                    Verbosity.NORMAL,
                ),
            ],
            stacklevel=4,
        )

    def log_sql_statement(self, sql: str) -> None:
        """Log SQL statement with syntax highlighting."""
        self._log_best_candidate(
            [
                LogCandidate(
                    logging.DEBUG,
                    lambda: "\n".join(
                        [
                            colorize("SQL:", ANSI_COLORS["bold"] + ANSI_COLORS["blue"]),
                            *[
                                colorize_sql(line)
                                for line in sql.strip().split("\n")
                                if line.strip()
                            ],
                        ]
                    ),
                ),
            ],
            stacklevel=4,
        )

    def log_sql_trace(self, sql: str) -> None:
        """Log a raw SQL statement at TRACE level (finest-grained dumps)."""
        if self.logger.isEnabledFor(TRACE_LEVEL):
            self.trace(sql.strip(), stacklevel=3)

    def log_backup_created(self, backup_path: str) -> None:
        """Log backup creation."""
        self._log_best_candidate(
            [
                LogCandidate(
                    logging.INFO,
                    lambda: (
                        f"{colorize('[APPLIED]', ANSI_COLORS['green'])} "
                        f"Backup created: {backup_path}"
                    ),
                    Verbosity.NORMAL,
                ),
            ],
            stacklevel=4,
        )

    def log_baseline_set(self, version: str) -> None:
        """Log baseline migration set."""
        self._log_best_candidate(
            [
                LogCandidate(
                    logging.INFO,
                    lambda: (
                        f"{colorize('[APPLIED]', ANSI_COLORS['green'])} "
                        f"Baseline set at version: {version}"
                    ),
                    Verbosity.NORMAL,
                ),
            ],
            stacklevel=4,
        )

    def log_seed_migration(self, filename: str) -> None:
        """Log seed migration."""
        self._log_best_candidate(
            [
                LogCandidate(
                    logging.INFO,
                    lambda: (
                        f"{colorize('[APPLIED]', ANSI_COLORS['green'])} "
                        f"Seed data applied: {filename}"
                    ),
                    Verbosity.NORMAL,
                ),
            ],
            stacklevel=4,
        )

    def log_model_discovered(self, table_name: str, columns: list) -> None:
        """Log model discovered from SQLAlchemy models (debug_enabled only)."""
        self._log_best_candidate(
            [
                LogCandidate(
                    logging.DEBUG,
                    lambda: f"Discovered model: {table_name} with {len(columns)} columns",
                ),
            ],
            stacklevel=4,
        )

    def log_model_paths(self, paths: list[str]) -> None:
        """Log model paths being used (debug_enabled only)."""
        self._log_best_candidate(
            [
                LogCandidate(
                    logging.DEBUG,
                    lambda: f"Using model paths: {paths}",
                ),
            ],
            stacklevel=4,
        )

    def log_model_file_scanned(self, filepath: str) -> None:
        """Log a model file that was discovered for scanning (debug only)."""
        self._log_best_candidate(
            [
                LogCandidate(
                    logging.DEBUG,
                    lambda: f"Scanning model file: {filepath}",
                ),
            ],
            stacklevel=4,
        )

    def log_model_file_load_failed(self, filepath: str, reason: str) -> None:
        """Log a model file that failed to load (debug only)."""
        self._log_best_candidate(
            [
                LogCandidate(
                    logging.DEBUG,
                    lambda: f"Failed to load model file: {filepath} ({reason})",
                ),
            ],
            stacklevel=4,
        )

    def log_table_columns(self, table_name: str, columns: list) -> None:
        """Log table columns (debug_enabled only)."""
        self._log_best_candidate(
            [
                LogCandidate(
                    logging.DEBUG,
                    lambda: (
                        f"Table {table_name} columns: "
                        f"{', '.join(c['name'] for c in columns)}"
                    ),
                ),
            ],
            stacklevel=4,
        )

    def log_migration_sql_gen(self, table_name: str, sql: str) -> None:
        """Log generated migration SQL (debug_enabled only)."""
        self._log_best_candidate(
            [
                LogCandidate(
                    logging.DEBUG,
                    lambda: (
                        f"Generated SQL for {table_name}:\n{colorize_sql(sql)}"
                    ),
                ),
            ],
            stacklevel=4,
        )


_global_logger: Optional[DBWardenLogger] = None
_LOGGER_LOCK = threading.RLock()


def get_logger(
    debug_enabled: bool = False,
    verbose: bool | None = None,
    debug_level: int | None = None,
    verbosity: Verbosity | None = None,
    db_name: str | None = None,
    db_type: str | None = None,
) -> DBWardenLogger:
    """
    Get the global DBWarden logger instance.

    Args:
        debug_enabled: If True, sets logger to DEBUG level. Kept for backward
            compatibility; ``debug_level`` takes precedence when both provided.
        verbose: Legacy compatibility flag. Used only when explicitly
            provided and ``verbosity`` is not set, where ``True`` maps to
            ``Verbosity.VERBOSE`` and ``False`` maps to ``Verbosity.NORMAL``.
            When omitted, the existing singleton verbosity is left unchanged.
        debug_level: Exact stdlib logging level (e.g. ``logging.DEBUG``,
            ``logging.WARNING``). When provided, sets the singleton severity;
            ``None`` leaves the current severity untouched.
        verbosity: Explicit INFO-output verbosity. When provided, it takes
            precedence over ``verbose``.
        db_name: Database name from multi-db config.
        db_type: Database type (sqlite, postgresql, mysql, mariadb, clickhouse).

    Returns:
        DBWardenLogger: The global logger instance.
    """
    global _global_logger
    if _global_logger is None:
        with _LOGGER_LOCK:
            if _global_logger is None:
                _global_logger = DBWardenLogger(
                    debug_enabled=debug_enabled,
                    debug_level=debug_level,
                    verbose=verbose if verbose is not None else False,
                    verbosity=verbosity,
                    db_name=db_name,
                    db_type=db_type,
                )
    else:
        with _LOGGER_LOCK:
            if debug_level is not None and _global_logger.debug_level != debug_level:
                _global_logger.set_debug_level(debug_level)
            elif debug_enabled and not _global_logger.debug_enabled:
                _global_logger.set_debug_enabled(True)
            if verbosity is not None and _global_logger.verbosity != verbosity:
                _global_logger.set_verbosity(verbosity)
            elif verbose is not None and _global_logger.verbose != verbose:
                _global_logger.set_verbose(verbose)
            if db_name is not None:
                _global_logger.db_name = db_name
            if db_type is not None:
                _global_logger.db_type = db_type
    return _global_logger


def resolve_debug_level(value: str) -> int:
    """Resolve a CLI debug level name or numeric string to a stdlib logging level.

    Accepts case-insensitive names ``trace``, ``debug``, ``info``,
    ``warning``/``warn``, ``error``, ``critical``, or an integer string between
    5 and 50 that is a valid logging level.

    Raises:
        ValueError: If the value is not a recognized level.
    """
    normalized = str(value).strip().lower()
    named = {
        "trace": TRACE_LEVEL,
        "debug": logging.DEBUG,
        "info": logging.INFO,
        "warning": logging.WARNING,
        "warn": logging.WARNING,
        "error": logging.ERROR,
        "critical": logging.CRITICAL,
    }
    if normalized in named:
        return named[normalized]

    if normalized.isdigit():
        level = int(normalized)
        if level in (5, 10, 20, 30, 40, 50):
            return level

    raise ValueError(
        f"Invalid debug level {value!r}. Expected one of: "
        "trace, debug, info, warning, error, critical, or a numeric level (5, 10, 20, 30, 40, 50)."
    )


def reset_logger() -> None:
    """Reset the global logger instance."""
    global _global_logger
    with _LOGGER_LOCK:
        _global_logger = None


def enable_json_logging() -> None:
    """Force JSON-formatted log output (same effect as ``DBWARDEN_LOG_JSON=true``).

    Reconfigures the existing singleton so the change applies even when the
    logger has already been initialized.
    """
    os.environ["DBWARDEN_LOG_JSON"] = "true"
    with _LOGGER_LOCK:
        if _global_logger is not None:
            _global_logger._setup_logger()
