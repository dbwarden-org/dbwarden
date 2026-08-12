import logging
import secrets

from sqlalchemy import text

from dbwarden.database.connection import get_db_connection
from dbwarden.database.queries import QueryMethod, get_query

logger = logging.getLogger("dbwarden.lock")


def create_lock_table_if_not_exists(db_name: str | None = None) -> None:
    """Create the lock table if it doesn't exist."""
    with get_db_connection(db_name) as connection:
        connection.execute(text(get_query(QueryMethod.CREATE_LOCK_TABLE, db_name)))
        try:
            connection.execute(text(get_query(QueryMethod.ADD_LOCK_OWNER_COLUMN, db_name)))
        except Exception as exc:
            if "duplicate" not in str(exc).lower() and "exists" not in str(exc).lower():
                raise
        connection.execute(text(get_query(QueryMethod.INITIALIZE_LOCK, db_name)))


def acquire_lock(db_name: str | None = None, owner_token: str | None = None) -> bool:
    """Attempt to acquire the migration lock."""
    owner_token = owner_token or secrets.token_urlsafe(32)
    try:
        with get_db_connection(db_name) as connection:
            result = connection.execute(
                text(get_query(QueryMethod.ACQUIRE_LOCK, db_name)),
                {"owner_token": owner_token},
            )
        return result.rowcount == 1
    except Exception as exc:
        logger.warning("Failed to acquire migration lock: %s", exc)
        return False


def release_lock(db_name: str | None = None, owner_token: str | None = None) -> bool:
    """Release the migration lock."""
    if not owner_token:
        logger.warning("Refusing ownerless migration lock release")
        return False
    try:
        with get_db_connection(db_name) as connection:
            result = connection.execute(
                text(get_query(QueryMethod.RELEASE_LOCK, db_name)),
                {"owner_token": owner_token},
            )
        return result.rowcount == 1
    except Exception as exc:
        logger.warning("Failed to release migration lock: %s", exc)
        return False


def force_release_lock(db_name: str | None = None) -> bool:
    """Force release a lock as an explicit recovery operation."""
    try:
        with get_db_connection(db_name) as connection:
            result = connection.execute(text(get_query(QueryMethod.FORCE_RELEASE_LOCK, db_name)))
        return result.rowcount == 1
    except Exception as exc:
        logger.warning("Failed to force release migration lock: %s", exc)
        return False


def check_lock(db_name: str | None = None) -> bool:
    """Check if migration lock is currently held."""
    try:
        with get_db_connection(db_name) as connection:
            result = connection.execute(
                text(get_query(QueryMethod.CHECK_LOCK, db_name))
            )
            locked = result.scalar_one_or_none()
            return bool(locked)
    except Exception as exc:
        logger.debug("Lock check failed (table may not exist yet): %s", exc)
        return False
