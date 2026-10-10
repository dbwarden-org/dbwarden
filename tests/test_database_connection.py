from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


class TestGetDbConnection:
    @patch("dbwarden.connection.connection.get_database")
    @patch("dbwarden.connection.connection._get_engine")
    @patch("dbwarden.connection.connection.get_logger")
    def test_get_db_connection_success(
        self, mock_get_logger, mock_get_engine, mock_get_db
    ):
        mock_config = MagicMock()
        mock_config.sqlalchemy_url = "sqlite:///test.db"
        mock_config.database_type = "sqlite"
        mock_config.postgres_schema = None
        mock_get_db.return_value = mock_config

        mock_engine = MagicMock()
        mock_connection = MagicMock()
        mock_engine.begin.return_value.__enter__.return_value = mock_connection
        mock_get_engine.return_value = mock_engine

        from dbwarden.connection.connection import get_db_connection

        with get_db_connection("test") as conn:
            assert conn is mock_connection

        assert mock_engine.begin.call_count == 2  # probe + actual connection

    @patch("dbwarden.connection.connection.get_database")
    @patch("dbwarden.connection.connection._get_engine")
    def test_get_db_connection_rolls_back_on_exception(
        self, mock_get_engine, mock_get_db
    ):
        mock_config = MagicMock()
        mock_config.sqlalchemy_url = "sqlite:///test.db"
        mock_config.database_type = "sqlite"
        mock_config.postgres_schema = None
        mock_get_db.return_value = mock_config

        mock_engine = MagicMock()
        mock_engine.begin.return_value.__enter__.return_value = MagicMock()
        mock_get_engine.return_value = mock_engine

        from dbwarden.connection.connection import get_db_connection

        with pytest.raises(ValueError, match="test error"):
            with get_db_connection("test") as conn:
                raise ValueError("test error")

    @patch("dbwarden.connection.connection.get_database")
    @patch("dbwarden.connection.connection._get_engine")
    def test_get_db_connection_passes_sandbox_url(self, mock_get_engine, mock_get_db):
        mock_config = MagicMock()
        mock_config.sqlalchemy_url = "sqlite:///original.db"
        mock_config.database_type = "sqlite"
        mock_config.postgres_schema = None
        mock_get_db.return_value = mock_config

        mock_engine = MagicMock()
        mock_connection = MagicMock()
        mock_engine.begin.return_value.__enter__.return_value = mock_connection
        mock_get_engine.return_value = mock_engine

        from dbwarden.connection.connection import (
            clear_sandbox_override,
            get_db_connection,
            set_sandbox_override,
        )

        set_sandbox_override("sqlite:///sandbox.db", "sqlite")
        try:
            with get_db_connection("test") as conn:
                assert conn is mock_connection
        finally:
            clear_sandbox_override()


class TestGetEngine:
    def test_clickhouse_url_conversion_preserves_credentials(self):
        from dbwarden.connection.connection import _convert_url_to_clickhouse_dialect

        result = _convert_url_to_clickhouse_dialect(
            "clickhouse://clickhouse:clickhouse@localhost:8123/harness"
        )

        assert result == "clickhousedb://clickhouse:clickhouse@localhost:8123/harness"

    @patch("dbwarden.connection.connection.get_database")
    @patch("dbwarden.connection.connection._get_engine")
    def test_get_engine(self, mock_get_engine, mock_get_db):
        mock_config = MagicMock()
        mock_config.sqlalchemy_url = "sqlite:///test.db"
        mock_config.database_type = "sqlite"
        mock_get_db.return_value = mock_config

        from dbwarden.connection.connection import get_engine

        result = get_engine("test")
        mock_get_engine.assert_called_once_with("sqlite:///test.db", "sqlite")
        assert result == mock_get_engine.return_value


class TestSandboxOverride:
    def test_sandbox_override_context_manager(self):
        from unittest.mock import MagicMock, patch

        import dbwarden.connection.connection as conn_mod

        with (
            patch("dbwarden.connection.connection.get_database") as mock_get_db,
            patch("dbwarden.connection.connection._get_engine") as mock_get_engine,
        ):
            mock_config = MagicMock()
            mock_config.sqlalchemy_url = "sqlite:///real.db"
            mock_config.database_type = "sqlite"
            mock_get_db.return_value = mock_config

            # Without sandbox, get_db_connection uses config URL
            with conn_mod.get_db_connection("test") as conn:
                mock_get_engine.assert_called_with("sqlite:///real.db", "sqlite")

            mock_get_engine.reset_mock()

            # With sandbox override, get_db_connection uses sandbox URL
            with conn_mod.sandbox_override("sqlite:///sandbox.db", "sqlite"):
                with conn_mod.get_db_connection("test") as conn:
                    mock_get_engine.assert_called_with("sqlite:///sandbox.db", "sqlite")

            # After sandbox ends, back to config URL
            mock_get_engine.reset_mock()
            with conn_mod.get_db_connection("test") as conn:
                mock_get_engine.assert_called_with("sqlite:///real.db", "sqlite")


class TestConvertUrl:
    @pytest.mark.parametrize(
        "scheme",
        ["http", "https", "clickhouse", "clickhousedb", "clickhouse+http", "clickhouse+https"],
    )
    def test_preserves_url_fields(self, scheme):
        from sqlalchemy.engine import make_url

        from dbwarden.connection.connection import _convert_url_to_clickhouse_dialect

        source = make_url(
            f"{scheme}://user%40name:p%40ss%3Aword%2F%25@[::1]:8443/db"
            "?verify=false&connect_timeout=7&session_id=a&session_id=b"
        )
        result = make_url(_convert_url_to_clickhouse_dialect(source.render_as_string(hide_password=False)))
        assert result.drivername == "clickhousedb"
        for field in ("username", "password", "host", "port", "database"):
            assert getattr(result, field) == getattr(source, field)
        secure = scheme.endswith("https")
        assert dict(result.query) == dict(source.query, **({"secure": "true"} if secure else {}))

    def test_https_defaults_and_no_native_protocol_inference(self):
        from sqlalchemy.engine import make_url

        from dbwarden.connection.connection import _convert_url_to_clickhouse_dialect

        secure = make_url(_convert_url_to_clickhouse_dialect("https://host/db?secure=false"))
        assert secure.port == 8443
        assert secure.query["secure"] == "true"
        plus_secure = make_url(_convert_url_to_clickhouse_dialect("clickhouse+https://user:p%40ss@host/db"))
        assert plus_secure.port == 8443
        assert plus_secure.query == {"secure": "true"}
        assert plus_secure.password == "p@ss"
        assert make_url(_convert_url_to_clickhouse_dialect("host:9000/db")).query == {}

    @pytest.mark.parametrize("discovery", [False, True])
    def test_introspection_uses_shared_conversion_and_disposes(self, discovery):
        from sqlalchemy.engine import make_url

        from dbwarden.engine.model_discovery.helpers import extract_tables_from_database
        from dbwarden.engine.snapshot.extract import extract_full_schema_snapshot

        url = "https://user:p%40ss@host/db?verify=false"
        with patch("sqlalchemy.create_engine") as create, patch("sqlalchemy.inspect") as inspect, patch(
            "dbwarden.engine.snapshot.extract._extract_clickhouse_schema_snapshot", return_value={"tables": {}}
        ) as extract:
            inspect.return_value.get_table_names.return_value = ["events"]
            inspect.return_value.get_columns.return_value = [{"name": "ID"}]
            if discovery:
                assert extract_tables_from_database(url, "clickhouse") == {"events": {"id"}}
            else:
                assert extract_full_schema_snapshot("db", url, "clickhouse") == {"tables": {}}
                extract.assert_called_once()
            parsed = make_url(create.call_args.args[0])
            assert parsed.drivername == "clickhousedb"
            assert parsed.password == "p@ss"
            assert parsed.query == {"secure": "true", "verify": "false"}
            create.return_value.dispose.assert_called_once()

    def test_convert_clickhouse_http_url(self):
        from dbwarden.connection.connection import _convert_url_to_clickhouse_dialect

        result = _convert_url_to_clickhouse_dialect("https://user:pass@host:8443/db")
        assert result.startswith("clickhousedb://user:pass@host:8443/db")

    def test_convert_clickhouse_no_password(self):
        from dbwarden.connection.connection import _convert_url_to_clickhouse_dialect

        result = _convert_url_to_clickhouse_dialect("http://user@host:8123/db")
        assert "password" not in result
        assert "clickhousedb://user@" in result

    def test_convert_clickhouse_plain_url(self):
        from dbwarden.connection.connection import _convert_url_to_clickhouse_dialect

        result = _convert_url_to_clickhouse_dialect(
            "clickhousedb://user:pass@host:8443/db"
        )
        assert result.startswith("clickhousedb://")


class TestResetConnectionLogging:
    def test_reset_connection_logging(self):
        import dbwarden.connection.connection as conn_mod

        conn_mod._connection_init_logged = True
        conn_mod.reset_connection_logging()
        assert conn_mod._connection_init_logged is False


@pytest.mark.parametrize(
    "url",
    ["sqlite:///app.db", "sqlite:///file:app.db?uri=true", "sqlite+pysqlite:///app.db"],
)
def test_relative_sqlite_engines_stay_with_their_project(tmp_path, monkeypatch, url):
    from sqlalchemy import text

    from dbwarden.connection.connection import _get_engine, dispose_engine

    engines = []
    for name in ("first", "second"):
        directory = tmp_path / name
        directory.mkdir()
        monkeypatch.chdir(directory)
        engine = _get_engine(url, "sqlite")
        engines.append(engine)
        with engine.begin() as connection:
            assert (
                connection.execute(
                    text("SELECT count(*) FROM sqlite_master WHERE name='owned'")
                ).scalar_one()
                == 0
            )
            connection.execute(text("CREATE TABLE owned(value TEXT)"))
            connection.execute(text("INSERT INTO owned VALUES(:name)"), {"name": name})
    assert engines[0] is not engines[1]
    for name, engine in zip(("first", "second"), engines):
        with engine.connect() as connection:
            assert (
                connection.execute(text("SELECT value FROM owned")).scalar_one() == name
            )
        monkeypatch.chdir(tmp_path / name)
        dispose_engine(url, "sqlite")
