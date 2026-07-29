import re

from dbwarden.config import get_database, is_strict_translation
from dbwarden.engine.sqlite_translation import translate_type_to_sqlite
from dbwarden.logging import get_logger


VALID_IDENTIFIER_RE = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")


def _validate_identifier(name: str, field: str = "identifier") -> None:
    if not name or not VALID_IDENTIFIER_RE.match(name):
        raise ValueError(
            f"Invalid {field}: '{name}'. "
            "Must start with letter/underscore, contain only alphanumeric and underscore."
        )


def _get_backend_name(db_name: str | None = None) -> str:
    try:
        config = get_database(db_name)
        return config.database_type
    except Exception:
        get_logger().warning(
            "Could not resolve database config for %r; "
            "falling back to 'sqlite'.  This may cause the wrong SQL "
            "dialect to be used.  Ensure DATABASE_URL is configured.",
            db_name,
        )
        return "sqlite"


def _map_sqlalchemy_type_to_backend(
    type_str: str, is_primary_key: bool = False, db_name: str | None = None,
    autoincrement: bool | None = None,
    backend: str | None = None,
) -> str:
    backend = backend or _get_backend_name(db_name)

    if backend == "postgresql":
        type_upper = type_str.upper()

        if autoincrement is not False and is_primary_key and type_upper == "INTEGER":
            return "SERIAL"
        if autoincrement is not False and is_primary_key and type_upper == "BIGINTEGER":
            return "BIGSERIAL"

        type_mapping = {
            "DATETIME": "TIMESTAMP",
            "DATETIME(6)": "TIMESTAMP(6)",
            "DATETIME WITH TIME ZONE": "TIMESTAMPTZ",
            "TIMESTAMP(6) WITHOUT TIME ZONE": "TIMESTAMP(6)",
            "BLOB": "BYTEA",
            "BYTEA": "BYTEA",
        }
        return type_mapping.get(type_str.upper(), type_str)

    if backend in ("mysql", "mariadb"):
        type_upper = type_str.upper()
        type_mapping = {
            "BOOLEAN": "TINYINT(1)",
            "SERIAL": "BIGINT UNSIGNED",
        }
        return type_mapping.get(type_upper, type_str)

    if backend == "sqlite":
        strict = is_strict_translation()
        translated, warning = translate_type_to_sqlite(type_str, strict=strict)
        if warning:
            get_logger().warning(warning)
        return translated

    return type_str
