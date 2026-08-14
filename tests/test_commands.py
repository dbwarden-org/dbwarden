import os
import sys
import tempfile
from io import StringIO
from pathlib import Path

from dbwarden.commands.init import init_cmd
from dbwarden.commands.utils import config_cmd, version_cmd
from dbwarden.constants import MIGRATIONS_DIR


class TestInitCommand:
    def test_init_creates_migrations_directory(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            old_cwd = os.getcwd()
            os.chdir(tmpdir)
            try:
                init_cmd()
                migrations_path = Path(tmpdir) / MIGRATIONS_DIR
                assert migrations_path.exists()
                assert migrations_path.is_dir()
            finally:
                os.chdir(old_cwd)

    def test_init_creates_dbwarden_py(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            old_cwd = os.getcwd()
            os.chdir(tmpdir)
            try:
                init_cmd()
                settings_path = Path(tmpdir) / "dbwarden.py"
                assert settings_path.exists()
                content = settings_path.read_text(encoding="utf-8")
                assert "from dbwarden import DbwardenDatabase" in content
                assert "class Primary(DbwardenDatabase):" in content
                assert "database_config(" not in content
            finally:
                os.chdir(old_cwd)

    def test_init_does_not_create_backup_on_first_run(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            old_cwd = os.getcwd()
            os.chdir(tmpdir)
            try:
                init_cmd()
                assert Path(tmpdir, "dbwarden.py").exists()
                assert not Path(tmpdir, "dbwarden.py.bak").exists()
            finally:
                os.chdir(old_cwd)

    def test_init_does_not_duplicate_scaffold(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            old_cwd = os.getcwd()
            os.chdir(tmpdir)
            try:
                init_cmd()
                init_cmd()
                init_cmd()

                settings_path = Path(tmpdir) / "dbwarden.py"
                content = settings_path.read_text(encoding="utf-8")
                assert content.count("from dbwarden import DbwardenDatabase") == 1
                assert content.count("class Primary(DbwardenDatabase):") == 1
            finally:
                os.chdir(old_cwd)

    def test_init_preserves_existing_function_config(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            old_cwd = os.getcwd()
            os.chdir(tmpdir)
            try:
                settings_path = Path(tmpdir) / "dbwarden.py"
                settings_path.write_text(
                    "from dbwarden import database_config\n\n"
                    "primary = database_config(database_name='primary', "
                    "database_url_sync='sqlite:///primary.db')\n",
                    encoding="utf-8",
                )
                init_cmd()
                content = settings_path.read_text(encoding="utf-8")
                assert "from dbwarden import database_config" in content
                assert "class Primary(DbwardenDatabase):" not in content
            finally:
                os.chdir(old_cwd)


class TestConfigCommand:
    def _run_config_capture_output(self, tmpdir: str) -> str:
        old_cwd = os.getcwd()
        os.chdir(tmpdir)
        try:
            old_stdout = sys.stdout
            sys.stdout = StringIO()
            config_cmd()
            output = sys.stdout.getvalue()
            sys.stdout = old_stdout
            return output
        finally:
            os.chdir(old_cwd)

    def test_config_displays_database_url(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            Path(tmpdir, "dbwarden.py").write_text(
                "from dbwarden import database_config\n\n"
                "database_config(database_name='primary', default=True, database_type='sqlite', database_url_sync='sqlite:///./test.db')\n",
                encoding="utf-8",
            )
            output = self._run_config_capture_output(tmpdir)
            assert "database_url_sync" in output
            assert "sqlite:///./test.db" in output

    def test_config_masks_password(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            Path(tmpdir, "dbwarden.py").write_text(
                "from dbwarden import database_config\n\n"
                "database_config(database_name='primary', default=True, database_type='postgresql', database_url_sync='postgresql://user:secretpassword@localhost/db')\n",
                encoding="utf-8",
            )
            output = self._run_config_capture_output(tmpdir)
            assert "secretpassword" not in output
            assert "user:***" in output

    def test_config_secure_values_uses_variable_name_for_variable_kwargs(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            Path(tmpdir, "dbwarden.py").write_text(
                "from dbwarden import database_config\n\n"
                "DATABASE_URL = 'postgresql://user:secretpassword@localhost/db'\n\n"
                "database_config(database_name='primary', default=True, database_type='postgresql', database_url_sync=DATABASE_URL, secure_values=True)\n",
                encoding="utf-8",
            )
            output = self._run_config_capture_output(tmpdir)
            assert "DATABASE_URL" in output
            assert "secretpassword" not in output

    def test_config_no_source_found(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            old_cwd = os.getcwd()
            os.chdir(tmpdir)
            try:
                old_stdout = sys.stdout
                sys.stdout = StringIO()
                try:
                    config_cmd()
                except Exception as exc:
                    assert "No configuration found" in str(exc)
                finally:
                    sys.stdout = old_stdout
            finally:
                os.chdir(old_cwd)


class TestVersionCommand:
    def _run_version_capture_output(self) -> str:
        old_stdout = sys.stdout
        sys.stdout = StringIO()
        version_cmd()
        output = sys.stdout.getvalue()
        sys.stdout = old_stdout
        return output

    def test_version_displays_version(self):
        output = self._run_version_capture_output()
        assert len(output.strip()) > 0
        assert "." in output.strip()

    def test_version_is_string(self):
        output = self._run_version_capture_output()
        assert isinstance(output.strip(), str)
