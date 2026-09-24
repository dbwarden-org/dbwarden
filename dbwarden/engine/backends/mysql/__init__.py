__all__ = [
    "append_mysql_column_attrs",
    "assert_complete_mysql_type",
    "build_mysql_alter_default_sql",
    "extract_mysql_meta",
    "mysql_column_definition_for_meta",
    "normalize_mysql_default",
    "normalize_mysql_table_value",
    "render_mysql_column_type",
    "resolve_mysql_imports",
]
from .extract import (
    assert_complete_mysql_type,
    mysql_column_definition_for_meta,
    normalize_mysql_default,
    normalize_mysql_table_value,
)
from .generate_models import extract_mysql_meta, resolve_mysql_imports
from .render import append_mysql_column_attrs, render_mysql_column_type


def __getattr__(name):
    if name == "build_mysql_alter_default_sql":
        from .sql_build import build_mysql_alter_default_sql
        return build_mysql_alter_default_sql
    raise AttributeError(name)
