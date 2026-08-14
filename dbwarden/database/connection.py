import logging
import re
import time
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, Generator

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

from dbwarden.config import get_database
from dbwarden.exceptions import DBDisconnectedError
from dbwarden.logging import get_logger


def _convert_url_to_clickhouse_dialect(url: str) -> str:
    """Convert HTTP URL or clickhousedb URL to clickhouse-connect dialect format.

    Supports both HTTP (port 8123) and native TCP (port 9000) protocols.
    Port 9000 triggers native protocol; all other ports use HTTP.
    """
    from sqlalchemy.engine import make_url

    if url.startswith(("http://", "https://", "clickhousedb://")):
        parsed = make_url(url)
        host = parsed.host or "localhost"
        port = parsed.port or 8123
        username = parsed.username or "default"
        password = parsed.password or ""
        database = parsed.database or "default"
        creds = f"{username}:{password}@" if password else f"{username}@"
        if port == 9000:
            return f"clickhousedb://{creds}{host}:{port}/{database}?protocol=native"
        return f"clickhousedb://{creds}{host}:{port}/{database}"

    return f"clickhousedb://{url}"


_engine_cache: dict[tuple[str, str], Engine] = {}


def sanitize_connection_error(message: str) -> str:
    """Remove passwords from driver errors before they cross the API boundary."""
    return re.sub(
        r"([A-Za-z][\w+.-]*://[^\s/@:]+:)[^\s/@]+(@)",
        r"\1***\2",
        message,
    )


def dispose_engine(url: str, db_type: str = "postgresql") -> None:
    """Dispose of a cached engine, releasing its connection pool."""
    key = (url, db_type)
    engine = _engine_cache.pop(key, None)
    if engine is not None:
        try:
            engine.dispose()
        except Exception:
            pass


def _get_engine(url: str, db_type: str = "postgresql") -> Engine:
    """Create SQLAlchemy engine with dialect-specific URL handling."""
    key = (url, db_type)
    if key in _engine_cache:
        return _engine_cache[key]

    final_url = url
    if db_type == "clickhouse":
        final_url = _convert_url_to_clickhouse_dialect(url)

    connect_args = {}
    if db_type == "sqlite":
        connect_args["check_same_thread"] = False
    engine = create_engine(url=final_url, connect_args=connect_args)
    _engine_cache[key] = engine
    return engine


_connection_init_logged = False


def _probe_connection(
    engine: Engine,
    db_type: str,
    logger: Any,
    url: str,
    max_retries: int = 5,
    retry_delay: float = 1.0,
) -> None:
    """Verify database connectivity with retry logic on failure."""
    for attempt in range(1, max_retries + 1):
        try:
            if db_type == "clickhouse":
                with engine.connect() as conn:
                    conn.execute(text("SELECT 1"))
            else:
                with engine.begin() as conn:
                    conn.execute(text("SELECT 1"))
            return
        except Exception as e:
            if attempt < max_retries:
                logger.warning(
                    f"Connection attempt {attempt}/{max_retries} failed, "
                    f"retrying in {retry_delay * attempt}s..."
                )
                time.sleep(retry_delay * attempt)
                dispose_engine(url, db_type)
                engine = _get_engine(url, db_type)
            else:
                logger.error(
                    f"Disconnected: could not connect to database "
                    f"after {max_retries} attempts"
                )
                raise DBDisconnectedError(sanitize_connection_error(str(e))) from e


def reset_connection_logging() -> None:
    """Reset the connection init logging flag for new CLI invocations."""
    global _connection_init_logged
    _connection_init_logged = False


_sandbox_url_var: ContextVar[str | None] = ContextVar("_sandbox_url", default=None)
_sandbox_db_type_var: ContextVar[str | None] = ContextVar("_sandbox_db_type", default=None)


def set_sandbox_override(url: str, db_type: str) -> None:
    """Set the sandbox database URL override for the current context."""
    _sandbox_url_var.set(url)
    _sandbox_db_type_var.set(db_type)


def clear_sandbox_override() -> None:
    """Clear the sandbox database URL override for the current context."""
    url = _sandbox_url_var.get()
    db_type = _sandbox_db_type_var.get()
    if url is not None:
        dispose_engine(url, db_type or "sqlite")
    _sandbox_url_var.set(None)
    _sandbox_db_type_var.set(None)


@contextmanager
def sandbox_override(url: str, db_type: str):
    """Context manager that temporarily sets the sandbox override."""
    token_url = _sandbox_url_var.set(url)
    token_type = _sandbox_db_type_var.set(db_type)
    try:
        yield
    finally:
        dispose_engine(url, db_type)
        _sandbox_url_var.reset(token_url)
        _sandbox_db_type_var.reset(token_type)


@contextmanager
def get_db_connection(db_name: str | None = None) -> Generator[Any, None, None]:
    """
    Context manager that yields a database connection.

    Args:
        db_name: Database name from config. If None, uses default database.
    """
    global _connection_init_logged
    config = get_database(db_name)

    sandbox_url = _sandbox_url_var.get()
    sandbox_db_type = _sandbox_db_type_var.get()
    url = sandbox_url if sandbox_url is not None else config.sqlalchemy_url
    db_type = sandbox_db_type if sandbox_db_type is not None else config.database_type

    from sqlalchemy.engine import make_url
    parsed = make_url(url)
    actual_db_name = db_name or (parsed.database or "default")
    logger = get_logger(
        db_name=actual_db_name,
        db_type=db_type,
    )

    engine = _get_engine(url, db_type)

    _probe_connection(engine, db_type, logger, url)

    if not _connection_init_logged:
        logger.log_connection_init(db_type)
        _connection_init_logged = True

    if db_type == "clickhouse":
        with engine.connect() as connection:
            yield connection
    else:
        with engine.begin() as connection:
            postgres_schema = config.postgres_schema
            if postgres_schema:
                connection.execute(
                    text("SET search_path TO :postgres_schema, public"),
                    parameters={"postgres_schema": postgres_schema},
                )
            yield connection


def get_engine(db_name: str | None = None) -> Engine:
    """
    Get SQLAlchemy engine for the specified database.

    Args:
        db_name: Database name from config. If None, uses default database.

    Returns:
        SQLAlchemy Engine instance.
    """
    config = get_database(db_name)
    return _get_engine(config.sqlalchemy_url, config.database_type)
