from importlib.metadata import version
from typing import Final

MIGRATIONS_DIR: Final[str] = "migrations"
SEEDS_DIR: Final[str] = "seeds"
SEEDS_TABLE: Final[str] = "_dbwarden_seeds"
TOML_FILE: Final[str] = "warden.toml"
RUNS_ALWAYS_FILE_PREFIX: Final[str] = "RA__"
RUNS_ON_CHANGE_FILE_PREFIX: Final[str] = "ROC__"
VERSION_FILE_PREFIX: Final[str] = "V"
DEFAULT_DELIMITER: Final[str] = ";"
INTERNAL_TABLE_PREFIXES: Final[tuple[str, ...]] = (".inner", "_dbwarden_", "dbwarden_lock")

DBWARDEN_VERSION: Final[str] = version("dbwarden")

LOG_FORMAT: Final[str] = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
