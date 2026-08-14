from pathlib import Path

from dbwarden.config.resolve import _file_has_database_config_call


def test_discovery_recognizes_aliased_declarative_base(tmp_path: Path):
    path = tmp_path / "config.py"
    path.write_text(
        "from dbwarden import DbwardenDatabase as Database\n"
        "class Primary(Database):\n"
        "    database_name = 'primary'\n"
        "    database_url_sync = 'sqlite:///primary.db'\n",
        encoding="utf-8",
    )

    assert _file_has_database_config_call(path)


def test_discovery_recognizes_indirect_declarative_base(tmp_path: Path):
    path = tmp_path / "config.py"
    path.write_text(
        "from dbwarden import DbwardenDatabase\n"
        "class Shared(DbwardenDatabase):\n"
        "    __abstract__ = True\n"
        "class Primary(Shared):\n"
        "    database_name = 'primary'\n"
        "    database_url_sync = 'sqlite:///primary.db'\n",
        encoding="utf-8",
    )

    assert _file_has_database_config_call(path)


def test_discovery_recognizes_module_attribute_base(tmp_path: Path):
    path = tmp_path / "config.py"
    path.write_text(
        "import dbwarden as db\n"
        "class Primary(db.DbwardenDatabase):\n"
        "    database_name = 'primary'\n"
        "    database_url_sync = 'sqlite:///primary.db'\n",
        encoding="utf-8",
    )

    assert _file_has_database_config_call(path)
