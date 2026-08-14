import os
import tempfile
from pathlib import Path

import pytest

from dbwarden.commands.init import init_cmd
from dbwarden.config import (
    get_database,
    get_multi_db_config,
    set_dev_mode,
)
from dbwarden.exceptions import ConfigurationError


def _write_settings(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")


class TestConfigAlt:
    @pytest.fixture(autouse=True)
    def _reset_dev_mode(self):
        set_dev_mode(False)
        yield
        set_dev_mode(False)

    def test_load_single_database_from_python_config(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            old_cwd = os.getcwd()
            os.chdir(tmpdir)
            try:
                _write_settings(
                    Path("dbwarden.py"),
                    "from dbwarden import database_config\n\n"
                    "database_config(database_name='primary', default=True, database_type='sqlite', database_url_sync='sqlite:///./app.db')\n",
                )

                cfg = get_multi_db_config()
                assert cfg.default == "primary"
                assert "primary" in cfg.databases
                assert cfg.databases["primary"].sqlalchemy_url == "sqlite:///./app.db"
            finally:
                os.chdir(old_cwd)

    def test_multiple_databases_require_model_paths(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            old_cwd = os.getcwd()
            os.chdir(tmpdir)
            try:
                _write_settings(
                    Path("dbwarden.py"),
                    "from dbwarden import database_config\n\n"
                    "database_config(database_name='primary', default=True, database_type='sqlite', database_url_sync='sqlite:///./app.db')\n"
                    "database_config(database_name='analytics', database_type='sqlite', database_url_sync='sqlite:///./analytics.db')\n",
                )

                with pytest.raises(ConfigurationError, match="model_paths is required"):
                    get_multi_db_config()
            finally:
                os.chdir(old_cwd)

    def test_model_paths_overlap_requires_opt_in(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            old_cwd = os.getcwd()
            os.chdir(tmpdir)
            try:
                _write_settings(
                    Path("dbwarden.py"),
                    "from dbwarden import database_config\n\n"
                    "database_config(database_name='primary', default=True, database_type='sqlite', database_url_sync='sqlite:///./app.db', model_paths=['models/shared'])\n"
                    "database_config(database_name='analytics', database_type='sqlite', database_url_sync='sqlite:///./analytics.db', model_paths=['models/shared'])\n",
                )

                with pytest.raises(ConfigurationError, match="model_paths overlap"):
                    get_multi_db_config()
            finally:
                os.chdir(old_cwd)

    def test_dev_mode_uses_dev_database_url(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            old_cwd = os.getcwd()
            os.chdir(tmpdir)
            try:
                _write_settings(
                    Path("dbwarden.py"),
                    "from dbwarden import database_config\n\n"
                    "database_config(database_name='primary', default=True, database_type='postgresql', database_url_sync='postgresql://user:pass@localhost:5432/main', dev_database_type='sqlite', dev_database_url='sqlite:///./dev.db')\n",
                )

                set_dev_mode(True)
                cfg = get_database("primary")
                assert cfg.sqlalchemy_url == "sqlite:///./dev.db"
                assert cfg.database_type == "sqlite"
            finally:
                os.chdir(old_cwd)

    def test_init_creates_python_settings_scaffold(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            old_cwd = os.getcwd()
            os.chdir(tmpdir)
            try:
                init_cmd()
                settings = Path("dbwarden.py").read_text(encoding="utf-8")
                assert "from dbwarden import DbwardenDatabase" in settings
                assert "class Primary(DbwardenDatabase):" in settings
            finally:
                os.chdir(old_cwd)

    def test_secure_values_uses_variable_expression_display(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            old_cwd = os.getcwd()
            os.chdir(tmpdir)
            try:
                _write_settings(
                    Path("dbwarden.py"),
                    "from dbwarden import database_config\n\n"
                    "DATABASE_URL = 'postgresql://user:pass@localhost/db'\n"
                    "database_config(database_name='primary', default=True, database_type='postgresql', database_url_sync=DATABASE_URL, secure_values=True)\n",
                )

                cfg = get_multi_db_config()
                assert cfg.databases["primary"].secure_values is True
                assert cfg.databases["primary"].secure_display_values["database_url_sync"] == "DATABASE_URL"
                assert cfg.databases["primary"].sqlalchemy_url == "postgresql://user:pass@localhost/db"
            finally:
                os.chdir(old_cwd)
