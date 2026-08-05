import os
import sys
import tempfile
from pathlib import Path

import pytest

from dbwarden.config import (
    DatabaseConfig,
    MultiDbConfig,
    get_config,
    get_database,
    get_multi_db_config,
    get_toml_path,
    list_databases,
    set_dev_mode,
    _finalize_entries,
)
from dbwarden.config_schema import DatabaseEntry, structure_database_entry
from dbwarden.exceptions import ConfigurationError

def _write_settings(path: Path, lines: list[str]) -> None:
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


@pytest.fixture(autouse=True)
def reset_dev_mode():
    set_dev_mode(False)
    yield
    set_dev_mode(False)


class TestConfig:
    def test_get_config_from_python_settings(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            old = os.getcwd()
            os.chdir(tmpdir)
            try:
                _write_settings(
                    Path("dbwarden.py"),
                    [
                        "from dbwarden import database_config",
                        "",
                        "database_config(database_name='primary', default=True, database_type='sqlite', database_url_sync='sqlite:///./test.db')",
                    ],
                )
                config = get_config()
                assert config.sqlalchemy_url == "sqlite:///./test.db"
                assert config.database_type == "sqlite"
                assert config.migrations_dir == "migrations/primary"
                assert config.migration_table == "_dbwarden_migrations"
            finally:
                os.chdir(old)

    def test_get_config_uses_custom_migration_table(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            old = os.getcwd()
            os.chdir(tmpdir)
            try:
                _write_settings(
                    Path("dbwarden.py"),
                    [
                        "from dbwarden import database_config",
                        "",
                        "database_config(database_name='primary', default=True, database_type='sqlite', database_url_sync='sqlite:///./test.db', migration_table='custom_migrations')",
                    ],
                )
                config = get_config()
                assert config.migration_table == "custom_migrations"
            finally:
                os.chdir(old)

    def test_get_config_uses_custom_seed_table(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            old = os.getcwd()
            os.chdir(tmpdir)
            try:
                _write_settings(
                    Path("dbwarden.py"),
                    [
                        "from dbwarden import database_config",
                        "",
                        "database_config(database_name='primary', default=True, database_type='sqlite', database_url_sync='sqlite:///./test.db', seed_table='custom_seeds')",
                    ],
                )
                config = get_config()
                assert config.seed_table == "custom_seeds"
            finally:
                os.chdir(old)

    def test_get_config_default_seed_table(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            old = os.getcwd()
            os.chdir(tmpdir)
            try:
                _write_settings(
                    Path("dbwarden.py"),
                    [
                        "from dbwarden import database_config",
                        "",
                        "database_config(database_name='primary', default=True, database_type='sqlite', database_url_sync='sqlite:///./test.db')",
                    ],
                )
                config = get_config()
                assert config.seed_table == "_dbwarden_seeds"
            finally:
                os.chdir(old)

    def test_get_config_with_model_paths(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            old = os.getcwd()
            os.chdir(tmpdir)
            try:
                _write_settings(
                    Path("dbwarden.py"),
                    [
                        "from dbwarden import database_config",
                        "",
                        "database_config(database_name='primary', default=True, database_type='sqlite', database_url_sync='sqlite:///./test.db', model_paths=['./models/user.py'])",
                    ],
                )
                config = get_config()
                assert config.model_paths == ["./models/user.py"]
            finally:
                os.chdir(old)

    def test_get_config_with_model_tables(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            old = os.getcwd()
            os.chdir(tmpdir)
            try:
                _write_settings(
                    Path("dbwarden.py"),
                    [
                        "from dbwarden import database_config",
                        "",
                        "database_config(database_name='primary', default=True, database_type='sqlite', database_url_sync='sqlite:///./test.db', model_tables=['users', 'posts'])",
                    ],
                )
                config = get_config()
                assert config.model_tables == ["users", "posts"]
            finally:
                os.chdir(old)

    def test_get_config_model_tables_none_by_default(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            old = os.getcwd()
            os.chdir(tmpdir)
            try:
                _write_settings(
                    Path("dbwarden.py"),
                    [
                        "from dbwarden import database_config",
                        "",
                        "database_config(database_name='primary', default=True, database_type='sqlite', database_url_sync='sqlite:///./test.db')",
                    ],
                )
                config = get_config()
                assert config.model_tables is None
            finally:
                os.chdir(old)

    def test_missing_urls_raises_error(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            old = os.getcwd()
            os.chdir(tmpdir)
            try:
                _write_settings(
                    Path("dbwarden.py"),
                    [
                        "from dbwarden import database_config",
                        "",
                        "database_config(database_name='primary', default=True, database_type='sqlite')",
                    ],
                )
                with pytest.raises(ConfigurationError, match="database_url_sync or database_url_async"):
                    get_config()
            finally:
                os.chdir(old)

    def test_get_toml_path_returns_none_when_not_found(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            old = os.getcwd()
            os.chdir(tmpdir)
            try:
                assert get_toml_path() is None
            finally:
                os.chdir(old)

    def test_default_database_not_found_raises_error(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            old = os.getcwd()
            os.chdir(tmpdir)
            try:
                _write_settings(
                    Path("dbwarden.py"),
                    [
                        "from dbwarden import database_config",
                        "",
                        "database_config(database_name='primary', default=False, database_type='sqlite', database_url_sync='sqlite:///./test.db')",
                    ],
                )
                with pytest.raises(ConfigurationError, match="Exactly one default"):
                    get_multi_db_config()
            finally:
                os.chdir(old)

    def test_get_database_by_name(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            old = os.getcwd()
            os.chdir(tmpdir)
            try:
                _write_settings(
                    Path("dbwarden.py"),
                    [
                        "from dbwarden import database_config",
                        "",
                        "database_config(database_name='primary', default=True, database_type='sqlite', database_url_sync='sqlite:///./primary.db', model_paths=['models/primary'])",
                        "database_config(database_name='analytics', database_type='sqlite', database_url_sync='sqlite:///./analytics.db', model_paths=['models/analytics'])",
                    ],
                )
                db = get_database("analytics")
                assert db.sqlalchemy_url == "sqlite:///./analytics.db"
            finally:
                os.chdir(old)

    def test_get_database_nonexistent_raises_error(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            old = os.getcwd()
            os.chdir(tmpdir)
            try:
                _write_settings(
                    Path("dbwarden.py"),
                    [
                        "from dbwarden import database_config",
                        "",
                        "database_config(database_name='primary', default=True, database_type='sqlite', database_url_sync='sqlite:///./test.db')",
                    ],
                )
                with pytest.raises(ConfigurationError, match="not found in settings config"):
                    get_database("nonexistent")
            finally:
                os.chdir(old)

    def test_list_databases(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            old = os.getcwd()
            os.chdir(tmpdir)
            try:
                _write_settings(
                    Path("dbwarden.py"),
                    [
                        "from dbwarden import database_config",
                        "",
                        "database_config(database_name='primary', default=True, database_type='sqlite', database_url_sync='sqlite:///./primary.db', model_paths=['models/primary'])",
                        "database_config(database_name='analytics', database_type='sqlite', database_url_sync='sqlite:///./analytics.db', model_paths=['models/analytics'])",
                        "database_config(database_name='legacy', database_type='sqlite', database_url_sync='sqlite:///./legacy.db', model_paths=['models/legacy'])",
                    ],
                )
                dbs = list_databases()
                assert set(dbs) == {"primary", "analytics", "legacy"}
            finally:
                os.chdir(old)

    def test_get_multi_db_config(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            old = os.getcwd()
            os.chdir(tmpdir)
            try:
                _write_settings(
                    Path("dbwarden.py"),
                    [
                        "from dbwarden import database_config",
                        "",
                        "database_config(database_name='primary', default=True, database_type='sqlite', database_url_sync='sqlite:///./primary.db', model_paths=['models/primary'])",
                        "database_config(database_name='analytics', database_type='sqlite', database_url_sync='sqlite:///./analytics.db', model_paths=['models/analytics'])",
                    ],
                )
                config = get_multi_db_config()
                assert isinstance(config, MultiDbConfig)
                assert config.default == "primary"
                assert len(config.databases) == 2
            finally:
                os.chdir(old)

    def test_get_database_uses_dev_database_in_dev_mode(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            old = os.getcwd()
            os.chdir(tmpdir)
            try:
                _write_settings(
                    Path("dbwarden.py"),
                    [
                        "from dbwarden import database_config",
                        "",
                        "database_config(database_name='primary', default=True, database_type='postgresql', database_url_sync='postgresql://user:password@localhost:5432/main', dev_database_url='sqlite:///./development.db')",
                    ],
                )
                set_dev_mode(True)
                config = get_database("primary")
                assert config.sqlalchemy_url == "sqlite:///./development.db"
                assert config.database_type == "sqlite"
            finally:
                os.chdir(old)

    def test_get_database_dev_mode_requires_dev_database_url(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            old = os.getcwd()
            os.chdir(tmpdir)
            try:
                _write_settings(
                    Path("dbwarden.py"),
                    [
                        "from dbwarden import database_config",
                        "",
                        "database_config(database_name='primary', default=True, database_type='sqlite', database_url_sync='sqlite:///./primary.db')",
                    ],
                )
                set_dev_mode(True)
                with pytest.raises(ConfigurationError, match="has no dev_database_url configured"):
                    get_database("primary")
            finally:
                os.chdir(old)

    def test_dev_database_type_requires_dev_database_url(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            old = os.getcwd()
            os.chdir(tmpdir)
            try:
                _write_settings(
                    Path("dbwarden.py"),
                    [
                        "from dbwarden import database_config",
                        "",
                        "database_config(database_name='primary', default=True, database_type='sqlite', database_url_sync='sqlite:///./primary.db', dev_database_type='sqlite')",
                    ],
                )
                with pytest.raises(ConfigurationError, match="dev_database_url is required"):
                    get_multi_db_config()
            finally:
                os.chdir(old)

    def test_duplicate_database_urls_raise_error(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            old = os.getcwd()
            os.chdir(tmpdir)
            try:
                _write_settings(
                    Path("dbwarden.py"),
                    [
                        "from dbwarden import database_config",
                        "",
                        "database_config(database_name='primary', default=True, database_type='sqlite', database_url_sync='sqlite:///./same.db', model_paths=['models/primary'])",
                        "database_config(database_name='analytics', database_type='sqlite', database_url_sync='sqlite:///./same.db', model_paths=['models/analytics'])",
                    ],
                )
                with pytest.raises(ConfigurationError, match="Duplicate database_url_sync"):
                    get_multi_db_config()
            finally:
                os.chdir(old)


class TestConfigSandboxClassification:
    """Tests for config source classification (isolated vs in-package) and
    precedence rules.

    Verifies that:
    - Top-level ``dbwarden.py`` is sandboxed (isolated).
    - ``DBWARDEN_CONFIG_MODULE`` beats full-scan discovery.
    - Full-scan discovered package-internal files (project-root or ``src/``
      layout) import normally (no sandbox).
    - Full-scan discovered files directly at project root are sandboxed.
    - Repeated config loads work (sys.modules ejection).
    """

    _added_paths: list[str] = []

    @staticmethod
    def _purge_package_modules(prefix: str = "app") -> None:
        for mod_name in list(sys.modules.keys()):
            if mod_name == prefix or mod_name.startswith(prefix + "."):
                del sys.modules[mod_name]

    def _cleanup_paths(self) -> None:
        for p in self._added_paths:
            if p in sys.path:
                sys.path.remove(p)
        self._added_paths.clear()

    def _add_sys_path(self, p: str) -> None:
        if p not in sys.path:
            sys.path.insert(0, p)
            self._added_paths.append(p)

    @staticmethod
    def _unused_module_name() -> str:
        """Return a module name that is not currently in sys.modules."""
        idx = 0
        while True:
            name = f"_dbw_test_never_imported_{idx}"
            if name not in sys.modules:
                return name
            idx += 1

    def _ensure_git_marker(self, tmpdir: str) -> None:
        (Path(tmpdir) / ".git").mkdir()

    def _write_package(
        self, base: Path, pkg_path: str, files: dict[str, str]
    ) -> None:
        for relpath, content in files.items():
            full = base / pkg_path / relpath
            full.parent.mkdir(parents=True, exist_ok=True)
            full.write_text(content)

    def test_top_level_dbwarden_py_config_works(self):
        """dbwarden.py at project root loads config correctly."""
        tmp = Path(tempfile.mkdtemp())
        old_cwd = os.getcwd()
        os.chdir(str(tmp))
        try:
            self._ensure_git_marker(str(tmp))
            (tmp / "dbwarden.py").write_text(
                "from dbwarden import database_config\n"
                "database_config(database_name='primary', default=True, "
                "database_type='sqlite', database_url_sync='sqlite:///./test.db')\n"
            )
            config = get_config()
            assert config.database_type == "sqlite"
        finally:
            os.chdir(old_cwd)
            import shutil
            shutil.rmtree(str(tmp), ignore_errors=True)

    def test_package_internal_config_imports_normally(self):
        """Full-scan discovered file inside app package imports normally."""
        tmp = Path(tempfile.mkdtemp())
        old_cwd = os.getcwd()
        os.chdir(str(tmp))
        try:
            self._purge_package_modules()
            self._ensure_git_marker(str(tmp))
            self._add_sys_path(str(tmp))
            self._write_package(tmp, "app/core", {
                "__init__.py": "",
                "config.py": "DATABASE_URL = 'sqlite:///./test.db'\n",
                "databases.py": (
                    "from dbwarden import database_config\n"
                    "from app.core.config import DATABASE_URL\n\n"
                    "database_config(database_name='primary', default=True, "
                    "database_type='sqlite', database_url_sync=DATABASE_URL)\n"
                ),
            })
            (tmp / "app" / "__init__.py").write_text("")

            cfg = get_config()
            assert cfg.database_type == "sqlite"
            assert cfg.sqlalchemy_url.endswith("test.db")
        finally:
            self._purge_package_modules()
            self._cleanup_paths()
            os.chdir(old_cwd)
            import shutil
            shutil.rmtree(str(tmp), ignore_errors=True)

    def test_root_file_not_named_dbwarden_py_loads_config(self):
        """Any file at project root with database_config() loads config."""
        tmp = Path(tempfile.mkdtemp())
        old_cwd = os.getcwd()
        os.chdir(str(tmp))
        try:
            self._ensure_git_marker(str(tmp))
            (tmp / "settings.py").write_text(
                "from dbwarden import database_config\n"
                "database_config(database_name='primary', default=True, "
                "database_type='sqlite', database_url_sync='sqlite:///./test.db')\n"
            )
            config = get_config()
            assert config.database_type == "sqlite"
        finally:
            os.chdir(old_cwd)
            import shutil
            shutil.rmtree(str(tmp), ignore_errors=True)

    def test_dbwarden_config_module_beats_full_scan(self):
        """DBWARDEN_CONFIG_MODULE takes priority over full-scan-discovered files."""
        tmp = Path(tempfile.mkdtemp())
        old_cwd = os.getcwd()
        old_env = os.environ.get("DBWARDEN_CONFIG_MODULE")
        os.chdir(str(tmp))
        try:
            self._purge_package_modules()
            self._ensure_git_marker(str(tmp))
            self._add_sys_path(str(tmp))
            # Create a full-scan-discoverable file (would be found by full-scan)
            self._write_package(tmp, "app/core", {
                "__init__.py": "",
                "config.py": "DATABASE_URL = 'sqlite:///./full-scan.db'\n",
                "databases.py": (
                    "from dbwarden import database_config\n"
                    "from app.core.config import DATABASE_URL\n\n"
                    "database_config(database_name='primary', default=True, "
                    "database_type='sqlite', database_url_sync=DATABASE_URL)\n"
                ),
            })
            (tmp / "app" / "__init__.py").write_text("")

            # Also create a separate package for DBWARDEN_CONFIG_MODULE to point at
            pkg_dir = Path(tempfile.mkdtemp())
            self._add_sys_path(str(pkg_dir))
            self._write_package(pkg_dir, "other/db", {
                "__init__.py": "",
                "config.py": "OTHER_URL = 'sqlite:///./explicit.db'\n",
                "databases.py": (
                    "from dbwarden import database_config\n"
                    "from other.db.config import OTHER_URL\n\n"
                    "database_config(database_name='primary', default=True, "
                    "database_type='sqlite', database_url_sync=OTHER_URL)\n"
                ),
            })
            (pkg_dir / "other" / "__init__.py").write_text("")

            os.environ["DBWARDEN_CONFIG_MODULE"] = "other.db.databases"

            # Should resolve to explicit module (other.db.databases), not full-scan
            cfg = get_config()
            assert cfg.sqlalchemy_url.endswith("explicit.db")
        finally:
            if old_env is not None:
                os.environ["DBWARDEN_CONFIG_MODULE"] = old_env
            else:
                os.environ.pop("DBWARDEN_CONFIG_MODULE", None)
            self._purge_package_modules("other")
            self._purge_package_modules("app")
            self._cleanup_paths()
            os.chdir(old_cwd)
            import shutil
            shutil.rmtree(str(tmp), ignore_errors=True)
            shutil.rmtree(str(pkg_dir), ignore_errors=True)


    def test_src_layout_resolved_correctly(self):
        """Config file under src/ is resolved via src/ import root, not project root."""
        tmp = Path(tempfile.mkdtemp())
        old_cwd = os.getcwd()
        os.chdir(str(tmp))
        try:
            self._purge_package_modules("myapp")
            self._ensure_git_marker(str(tmp))
            self._add_sys_path(str(tmp))
            self._write_package(tmp, "src/myapp", {
                "__init__.py": "",
                "config.py": "DATABASE_URL = 'sqlite:///./test.db'\n",
                "databases.py": (
                    "from dbwarden import database_config\n"
                    "from myapp.config import DATABASE_URL\n\n"
                    "database_config(database_name='primary', default=True, "
                    "database_type='sqlite', database_url_sync=DATABASE_URL)\n"
                ),
            })
            (tmp / "src" / "__init__.py").write_text("")

            cfg = get_config()
            assert cfg.database_type == "sqlite"
            assert cfg.sqlalchemy_url.endswith("test.db")
        finally:
            self._purge_package_modules("myapp")
            self._cleanup_paths()
            os.chdir(old_cwd)
            import shutil
            shutil.rmtree(str(tmp), ignore_errors=True)

    def test_repeated_load_with_registry_reset(self):
        """Repeated get_database() + get_multi_db_config() still registers entries.

        Regression test for sys.modules ejection: the second call must re-execute
        the module so database_config(...) re-registers after reset_registry().
        """
        tmp = Path(tempfile.mkdtemp())
        old_cwd = os.getcwd()
        os.chdir(str(tmp))
        try:
            self._purge_package_modules("app")
            self._ensure_git_marker(str(tmp))
            self._add_sys_path(str(tmp))
            self._write_package(tmp, "app/core", {
                "__init__.py": "",
                "config.py": "DATABASE_URL = 'sqlite:///./test.db'\n",
                "databases.py": (
                    "from dbwarden import database_config\n"
                    "from app.core.config import DATABASE_URL\n\n"
                    "database_config(database_name='primary', default=True, "
                    "database_type='sqlite', database_url_sync=DATABASE_URL)\n"
                ),
            })
            (tmp / "app" / "__init__.py").write_text("")

            # First load (cached in sys.modules after)
            cfg1 = get_database()
            assert cfg1.sqlalchemy_url.endswith("test.db")

            # Second load from the same process, must re-register after reset
            cfg2 = get_multi_db_config()
            assert cfg2.default == "primary"
            assert len(cfg2.databases) == 1

            # Third load via get_database again
            cfg3 = get_database()
            assert cfg3.sqlalchemy_url.endswith("test.db")
        finally:
            self._purge_package_modules("app")
            self._cleanup_paths()
            os.chdir(old_cwd)
            import shutil
            shutil.rmtree(str(tmp), ignore_errors=True)


class TestModelTablesConfig:
    def test_model_tables_overlap_detected(self):
        from pathlib import Path
        entries = [
            DatabaseEntry(
                database_name="db1", database_type="sqlite",
                database_url_sync="sqlite:///./db1.db", default=True,
                model_paths=["models"], model_tables=["users", "posts"],
            ),
            DatabaseEntry(
                database_name="db2", database_type="sqlite",
                database_url_sync="sqlite:///./db2.db",
                model_paths=["other_models"], model_tables=["posts", "comments"],
            ),
        ]
        with pytest.raises(ConfigurationError, match="model_tables overlap"):
            _finalize_entries(entries, Path.cwd())

    def test_model_tables_overlap_allowed_with_overlap_models(self):
        from pathlib import Path
        entries = [
            DatabaseEntry(
                database_name="db1", database_type="sqlite",
                database_url_sync="sqlite:///./db1.db", default=True,
                model_paths=["models"], model_tables=["users", "posts"],
                overlap_models=True,
            ),
            DatabaseEntry(
                database_name="db2", database_type="sqlite",
                database_url_sync="sqlite:///./db2.db",
                model_paths=["other_models"], model_tables=["posts", "comments"],
                overlap_models=True,
            ),
        ]
        result = _finalize_entries(entries, Path.cwd())
        assert "db1" in result.databases
        assert "db2" in result.databases

    def test_model_tables_overlap_detected_when_only_one_side_opts_in(self):
        """A non-opting entry's overlap must still be flagged even if the peer opted in."""
        from pathlib import Path
        entries = [
            DatabaseEntry(
                database_name="db1", database_type="sqlite",
                database_url_sync="sqlite:///./db1.db", default=True,
                model_paths=["models"], model_tables=["users", "posts"],
            ),
            DatabaseEntry(
                database_name="db2", database_type="sqlite",
                database_url_sync="sqlite:///./db2.db",
                model_paths=["other_models"], model_tables=["posts", "comments"],
                overlap_models=True,
            ),
        ]
        with pytest.raises(ConfigurationError, match="model_tables overlap"):
            _finalize_entries(entries, Path.cwd())

    def test_model_paths_overlap_detected_when_only_one_side_opts_in(self):
        """A non-opting entry's model_paths overlap must still be flagged."""
        from pathlib import Path
        entries = [
            DatabaseEntry(
                database_name="db1", database_type="sqlite",
                database_url_sync="sqlite:///./db1.db", default=True,
                model_paths=["shared_models"],
            ),
            DatabaseEntry(
                database_name="db2", database_type="sqlite",
                database_url_sync="sqlite:///./db2.db",
                model_paths=["shared_models"],
                overlap_models=True,
            ),
        ]
        with pytest.raises(ConfigurationError, match="model_paths overlap"):
            _finalize_entries(entries, Path.cwd())

    def test_model_tables_overlap_skipped_when_one_side_none(self):
        from pathlib import Path
        entries = [
            DatabaseEntry(
                database_name="db1", database_type="sqlite",
                database_url_sync="sqlite:///./db1.db", default=True,
                model_paths=["models"], model_tables=["users"],
            ),
            DatabaseEntry(
                database_name="db2", database_type="sqlite",
                database_url_sync="sqlite:///./db2.db",
                model_paths=["other_models"],
            ),
        ]
        result = _finalize_entries(entries, Path.cwd())
        assert "db1" in result.databases
        assert "db2" in result.databases

    def test_model_tables_invalid_name_rejected(self):
        with pytest.raises(ConfigurationError, match="Invalid table name"):
            structure_database_entry(dict(
                database_name="primary", database_type="sqlite",
                database_url_sync="sqlite:///./test.db", default=True,
                model_tables=["bad-name!"],
            ))

    def test_model_tables_invalid_type_rejected(self):
        with pytest.raises(ConfigurationError, match="model_tables must be a list"):
            structure_database_entry(dict(
                database_name="primary", database_type="sqlite",
                database_url_sync="sqlite:///./test.db", default=True,
                model_tables="not-a-list",
            ))

    def test_filter_none_returns_same(self):
        from dbwarden.engine.model_discovery import filter_model_tables_by_name, ModelTable, ModelColumn
        table1 = ModelTable(name="users", columns=[ModelColumn(name="id", type="integer", nullable=False, primary_key=True, unique=False, default=None, foreign_key=None)])
        table2 = ModelTable(name="posts", columns=[ModelColumn(name="id", type="integer", nullable=False, primary_key=True, unique=False, default=None, foreign_key=None)])
        result = filter_model_tables_by_name([table1, table2], None)
        assert len(result) == 2

    def test_filter_picks_named_tables(self):
        from dbwarden.engine.model_discovery import filter_model_tables_by_name, ModelTable, ModelColumn
        table1 = ModelTable(name="users", columns=[ModelColumn(name="id", type="integer", nullable=False, primary_key=True, unique=False, default=None, foreign_key=None)])
        table2 = ModelTable(name="posts", columns=[ModelColumn(name="id", type="integer", nullable=False, primary_key=True, unique=False, default=None, foreign_key=None)])
        result = filter_model_tables_by_name([table1, table2], ["users"])
        assert len(result) == 1
        assert result[0].name == "users"

    def test_validate_all_exist_passes(self):
        from dbwarden.engine.model_discovery import validate_model_tables_exist, ModelTable, ModelColumn
        table1 = ModelTable(name="users", columns=[ModelColumn(name="id", type="integer", nullable=False, primary_key=True, unique=False, default=None, foreign_key=None)])
        validate_model_tables_exist([table1], ["users"], "test_db")

    def test_validate_missing_raises(self):
        from dbwarden.engine.model_discovery import validate_model_tables_exist, ModelTable, ModelColumn, DBWardenConfigError
        table1 = ModelTable(name="users", columns=[ModelColumn(name="id", type="integer", nullable=False, primary_key=True, unique=False, default=None, foreign_key=None)])
        with pytest.raises(DBWardenConfigError, match="unknown"):
            validate_model_tables_exist([table1], ["users", "nonexistent"], "test_db")

    def test_validate_none_skips(self):
        from dbwarden.engine.model_discovery import validate_model_tables_exist, ModelTable, ModelColumn
        table1 = ModelTable(name="users", columns=[ModelColumn(name="id", type="integer", nullable=False, primary_key=True, unique=False, default=None, foreign_key=None)])
        validate_model_tables_exist([table1], None, "test_db")

class TestConfigScanCaching:
    """Regression tests: repeated get_database()/get_multi_db_config() calls
    must not re-run the expensive full-repository AST scan.

    Historically reset_registry() cleared the resolved-source cache on every
    cache miss, so every per-column get_database() call re-scanned the whole
    workspace (a hang in large repositories).
    """

    def test_get_database_does_not_rescan_after_first_call(self, monkeypatch):
        import dbwarden.config.resolve as resolve_mod

        tmp = Path(tempfile.mkdtemp())
        old_cwd = os.getcwd()
        os.chdir(str(tmp))
        try:
            (tmp / ".git").mkdir()
            (tmp / "settings.py").write_text(
                "from dbwarden import database_config\n"
                "database_config(database_name='primary', default=True, "
                "database_type='sqlite', database_url_sync='sqlite:///./test.db')\n"
            )

            scans = {"count": 0}
            original = resolve_mod._full_scan_database_config_calls

            def counting_scan(root):
                scans["count"] += 1
                return original(root)

            monkeypatch.setattr(
                resolve_mod, "_full_scan_database_config_calls", counting_scan
            )

            for _ in range(10):
                config = get_database()
                assert config.database_type == "sqlite"

            assert scans["count"] == 1
        finally:
            os.chdir(old_cwd)
            import shutil
            shutil.rmtree(str(tmp), ignore_errors=True)

    def test_resolve_source_does_not_rescan_after_first_call(self, monkeypatch):
        import dbwarden.config.resolve as resolve_mod

        tmp = Path(tempfile.mkdtemp())
        old_cwd = os.getcwd()
        os.chdir(str(tmp))
        try:
            (tmp / ".git").mkdir()
            (tmp / "settings.py").write_text(
                "from dbwarden import database_config\n"
                "database_config(database_name='primary', default=True, "
                "database_type='sqlite', database_url_sync='sqlite:///./test.db')\n"
            )

            scans = {"count": 0}
            original = resolve_mod._full_scan_database_config_calls

            def counting_scan(root):
                scans["count"] += 1
                return original(root)

            monkeypatch.setattr(
                resolve_mod, "_full_scan_database_config_calls", counting_scan
            )

            source1 = resolve_mod._resolve_source()
            source2 = resolve_mod._resolve_source()
            assert source1 == source2
            assert scans["count"] == 1
        finally:
            os.chdir(old_cwd)
            import shutil
            shutil.rmtree(str(tmp), ignore_errors=True)

    def test_get_multi_db_config_uses_cached_config(self, monkeypatch):
        import dbwarden.config.resolve as resolve_mod

        tmp = Path(tempfile.mkdtemp())
        old_cwd = os.getcwd()
        os.chdir(str(tmp))
        try:
            (tmp / ".git").mkdir()
            (tmp / "settings.py").write_text(
                "from dbwarden import database_config\n"
                "database_config(database_name='primary', default=True, "
                "database_type='sqlite', database_url_sync='sqlite:///./test.db')\n"
            )

            scans = {"count": 0}
            original = resolve_mod._full_scan_database_config_calls

            def counting_scan(root):
                scans["count"] += 1
                return original(root)

            monkeypatch.setattr(
                resolve_mod, "_full_scan_database_config_calls", counting_scan
            )

            mc1 = get_multi_db_config()
            mc2 = get_multi_db_config()
            assert mc1 is mc2
            assert mc2.default == "primary"
            assert scans["count"] == 1
        finally:
            os.chdir(old_cwd)
            import shutil
            shutil.rmtree(str(tmp), ignore_errors=True)
