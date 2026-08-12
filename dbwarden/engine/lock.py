import time
import uuid
from contextlib import contextmanager

from dbwarden.config import get_config
from dbwarden.exceptions import LockError
from dbwarden.logging import get_logger
from dbwarden.repositories.lock_repo import acquire_lock, check_lock, release_lock


@contextmanager
def migration_lock(timeout: int = 300):
    """
    Context manager that provides migration locking.

    Ensures only one migration process can run at a time.

    Args:
        timeout: Maximum time to wait for lock (default: 300 seconds).

    Raises:
        LockError: If lock cannot be acquired within timeout.
    """
    logger = get_logger()
    config = get_config()
    owner_token = uuid.uuid4().hex

    wait_time = 0
    while wait_time < timeout:
        if acquire_lock(owner_token=owner_token):
            logger.info("Migration lock acquired")
            try:
                yield
            finally:
                release_lock(owner_token=owner_token)
                logger.info("Migration lock released")
            return
        else:
            time.sleep(1)
            wait_time += 1

    raise LockError(
        f"Could not acquire migration lock within {timeout} seconds. "
        "Another migration process may be running."
    )


def is_locked() -> bool:
    """Check if migration is currently locked."""
    return check_lock()
