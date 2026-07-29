from sqlalchemy.engine import make_url

from dbwarden.config import DEFAULT_MIGRATION_TABLE, DEFAULT_SEEDS_TABLE, get_database
from dbwarden.database.queries.store import (
    CLICKHOUSE_QUERIES,
    MYSQL_QUERIES,
    POSTGRES_QUERIES,
    SQLITE_QUERIES,
    QueryMethod,
)
from dbwarden.logging import get_logger

DEFAULT_POSTGRES_SCHEMA = "public"


def _get_backend_name(db_name: str | None = None) -> str:
    config = get_database(db_name)
    return config.database_type


def _get_queries_for_backend(db_name: str | None = None) -> dict:
    backend = _get_backend_name(db_name)
    if backend == "postgresql":
        return POSTGRES_QUERIES
    if backend in ("mysql", "mariadb"):
        return MYSQL_QUERIES
    if backend == "clickhouse":
        return CLICKHOUSE_QUERIES
    return SQLITE_QUERIES


def get_migration_table_name(db_name: str | None = None) -> str:
    try:
        return get_database(db_name).migration_table
    except Exception:
        return DEFAULT_MIGRATION_TABLE


def get_schema_name(db_name: str | None = None) -> str:
    try:
        return get_database(db_name).postgres_schema or DEFAULT_POSTGRES_SCHEMA
    except Exception:
        get_logger().warning(
            "Could not resolve database config for %r; "
            "falling back to schema %r.  "
            "Ensure DATABASE_URL is configured.",
            db_name, DEFAULT_POSTGRES_SCHEMA,
        )
        return DEFAULT_POSTGRES_SCHEMA


def get_query(method: QueryMethod, db_name: str | None = None, **kwargs) -> str:
    query = _get_queries_for_backend(db_name).get(method, "")
    if not query:
        return ""
    safe_kwargs = {k: v for k, v in kwargs.items() if k not in ("migration_table", "seed_table")}
    return query.format(
        migration_table=get_migration_table_name(db_name),
        schema=get_schema_name(db_name),
        **safe_kwargs,
    )


def get_seed_table_name(db_name: str | None = None) -> str:
    try:
        return get_database(db_name).seed_table
    except Exception:
        return DEFAULT_SEEDS_TABLE


def get_seed_query(method: QueryMethod, db_name: str | None = None, **kwargs) -> str:
    query = _get_queries_for_backend(db_name).get(method, "")
    if not query:
        return ""
    safe_kwargs = {k: v for k, v in kwargs.items() if k not in ("migration_table", "seed_table")}
    return query.format(
        seed_table=get_seed_table_name(db_name),
        schema=get_schema_name(db_name),
        **safe_kwargs,
    )
