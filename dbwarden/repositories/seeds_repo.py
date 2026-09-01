from __future__ import annotations

from sqlalchemy import text

from dbwarden.connection.connection import get_db_connection
from dbwarden.connection.queries import QueryMethod, get_seed_query, get_seed_table_name
from dbwarden.models import SeedRecord


def create_seeds_table_if_not_exists(db_name: str | None = None) -> None:
    with get_db_connection(db_name) as connection:
        connection.execute(
            text(get_seed_query(QueryMethod.CREATE_SEEDS_TABLE, db_name))
        )


def seeds_table_exists(db_name: str | None = None) -> bool:
    with get_db_connection(db_name) as connection:
        try:
            connection.execute(
                text(get_seed_query(QueryMethod.GET_ALL_SEEDS, db_name))
            )
            return True
        except Exception:
            return False


def seed_is_applied(version: str, db_name: str | None = None) -> bool:
    if not seeds_table_exists(db_name):
        return False
    with get_db_connection(db_name) as connection:
        result = connection.execute(
            text(get_seed_query(QueryMethod.CHECK_SEED_EXISTS, db_name)),
            {"version": version},
        )
        row = result.fetchone()
        if row is None:
            return False
        count = row[0]
        return count > 0


def get_applied_seed_versions(db_name: str | None = None) -> set[str]:
    if not seeds_table_exists(db_name):
        return set()
    with get_db_connection(db_name) as connection:
        result = connection.execute(
            text(get_seed_query(QueryMethod.GET_APPLIED_SEED_VERSIONS, db_name))
        )
        return {row[0] for row in result.fetchall()}


def get_all_seed_records(db_name: str | None = None) -> list[SeedRecord]:
    if not seeds_table_exists(db_name):
        return []
    with get_db_connection(db_name) as connection:
        results = connection.execute(
            text(get_seed_query(QueryMethod.GET_ALL_SEEDS, db_name))
        )
        return [
            SeedRecord(
                version=row.version,
                description=row.description,
                filename=row.filename,
                seed_type=row.seed_type,
                applied_at=row.applied_at,
                checksum=row.checksum,
            )
            for row in results.fetchall()
        ]


def record_applied_seed(
    version: str,
    description: str,
    filename: str,
    seed_type: str,
    checksum: str,
    db_name: str | None = None,
) -> None:
    with get_db_connection(db_name) as connection:
        connection.execute(
            text(get_seed_query(QueryMethod.INSERT_SEED, db_name)),
            {
                "version": version,
                "description": description,
                "filename": filename,
                "seed_type": seed_type,
                "checksum": checksum,
            },
        )


def remove_seed_record(version: str, db_name: str | None = None) -> None:
    with get_db_connection(db_name) as connection:
        connection.execute(
            text(get_seed_query(QueryMethod.DELETE_SEED, db_name)),
            {"version": version},
        )


def get_seed_record(version: str, db_name: str | None = None) -> SeedRecord | None:
    if not seeds_table_exists(db_name):
        return None
    table = get_seed_table_name(db_name)
    with get_db_connection(db_name) as connection:
        result = connection.execute(
            text(f"SELECT * FROM {table} WHERE version = :version"),
            {"version": version},
        )
        row = result.fetchone()
        if row is None:
            return None
        return SeedRecord(
            version=row.version,
            description=row.description,
            filename=row.filename,
            seed_type=row.seed_type,
            applied_at=row.applied_at,
            checksum=row.checksum,
        )


def get_seeds_directory(db_name: str | None = None) -> str:
    from pathlib import Path

    from dbwarden.config import get_database
    from dbwarden.constants import SEEDS_DIR
    from dbwarden.exceptions import DirectoryNotFoundError

    from dbwarden.engine.version import _validate_path_within_project

    config = get_database(db_name)
    current_dir = Path.cwd()
    seeds_dir = current_dir / SEEDS_DIR

    _validate_path_within_project(seeds_dir, current_dir, SEEDS_DIR)

    if not seeds_dir.exists() or not seeds_dir.is_dir():
        raise DirectoryNotFoundError(
            f"Seeds directory '{SEEDS_DIR}' not found. "
            f"Please run 'dbwarden seed create' first."
        )
    return str(seeds_dir)
