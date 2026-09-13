from importlib.metadata import version

from dbwarden.config_registry import DbwardenConfig, DbwardenDatabase, database_config
from dbwarden.databases.clickhouse import ChEngineSpec, CHTableMeta
from dbwarden.databases.mysql import MyColumnMeta, MyTableMeta
from dbwarden.databases.pgsql import PGViewMeta
from dbwarden.databases.sqlite import SqColumnMeta, SqTableMeta
from dbwarden.plugin import load_plugins, register_migration_hooks
from dbwarden.seed import Seed, SeedRow, seed_data

__version__ = version("dbwarden")

__all__ = [
    "__version__",
    "database_config",
    "DbwardenConfig",
    "DbwardenDatabase",
    "ChEngineSpec",
    "CHTableMeta",
    "MyColumnMeta",
    "MyTableMeta",
    "PGViewMeta",
    "SqColumnMeta",
    "SqTableMeta",
    "load_plugins",
    "register_migration_hooks",
    "Seed",
    "SeedRow",
    "seed_data",
]
