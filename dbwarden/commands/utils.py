from dbwarden.config import display_value, get_multi_db_config
from dbwarden.constants import DBWARDEN_VERSION
from dbwarden.output import emit_json, info, json_mode, kv_table, render, section, success


def config_cmd() -> None:
    """Display current DBWarden configuration."""
    config = get_multi_db_config()

    payload: dict = {"default": config.default, "databases": {}}
    for name, db in config.databases.items():
        rows = {
            "database_type": display_value(db, "database_type", db.database_type),
            "database_url_sync": display_value(
                db,
                "database_url_sync",
                _mask_password(db.sqlalchemy_url),
            ),
            "migrations_dir": display_value(db, "migrations_dir", db.migrations_dir),
            "migration_table": display_value(db, "migration_table", db.migration_table),
            "seed_table": display_value(db, "seed_table", db.seed_table),
        }
        if db.model_paths:
            rows["model_paths"] = display_value(db, "model_paths", db.model_paths)
        if db.dev_database_url:
            rows["dev_database_url"] = display_value(
                db,
                "dev_database_url",
                _mask_password(db.dev_database_url),
            )
        if db.dev_database_type:
            rows["dev_database_type"] = display_value(db, "dev_database_type", db.dev_database_type)
        payload["databases"][name] = rows

    if json_mode():
        emit_json(payload)
        return

    section("DBWarden Configuration")
    success(f"default: {config.default}")
    for name, db in config.databases.items():
        marker = " (default)" if name == config.default else ""
        database_url = display_value(
            db,
            "database_url_sync",
            _mask_password(db.sqlalchemy_url),
        )
        rows = {
            "database_type": display_value(db, "database_type", db.database_type),
            "database_url_sync": database_url,
            "migrations_dir": display_value(db, "migrations_dir", db.migrations_dir),
            "migration_table": display_value(db, "migration_table", db.migration_table),
            "seed_table": display_value(db, "seed_table", db.seed_table),
        }
        if db.model_paths:
            rows["model_paths"] = display_value(db, "model_paths", db.model_paths)
        if db.dev_database_url:
            dev_database_url = display_value(
                db,
                "dev_database_url",
                _mask_password(db.dev_database_url),
            )
            rows["dev_database_url"] = dev_database_url
        if db.dev_database_type:
            rows["dev_database_type"] = display_value(db, "dev_database_type", db.dev_database_type)
        render(kv_table(f"Database: {name}{marker}", rows))

    info("Config source: python settings")


def _mask_password(url: str) -> str:
    """Mask password in connection URL."""
    if "@" in url:
        try:
            protocol, rest = url.split("://", 1)
            if "@" in rest:
                creds, host_part = rest.split("@", 1)
                if ":" in creds:
                    user, _ = creds.split(":", 1)
                    return f"{protocol}://{user}:***@{host_part}"
        except Exception:
            pass
    return url


def version_cmd() -> None:
    """Display DBWarden version."""
    if json_mode():
        emit_json({"version": DBWARDEN_VERSION})
        return
    success(DBWARDEN_VERSION)
