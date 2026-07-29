import pytest

from dbwarden.engine.model_discovery.type_mapping import (
    _validate_identifier,
    _map_sqlalchemy_type_to_backend,
)


class TestValidateIdentifier:
    def test_valid_identifier(self):
        _validate_identifier("my_table")
        _validate_identifier("_private")
        _validate_identifier("a")
        _validate_identifier("col_123")

    def test_empty_string_raises(self):
        with pytest.raises(ValueError, match="Invalid identifier"):
            _validate_identifier("")

    def test_none_raises(self):
        with pytest.raises(ValueError, match="Invalid identifier"):
            _validate_identifier(None)

    def test_starts_with_digit_raises(self):
        with pytest.raises(ValueError, match="Invalid identifier"):
            _validate_identifier("123abc")

    def test_contains_space_raises(self):
        with pytest.raises(ValueError, match="Invalid identifier"):
            _validate_identifier("my table")

    def test_contains_dash_raises(self):
        with pytest.raises(ValueError, match="Invalid identifier"):
            _validate_identifier("my-table")

    def test_custom_field_name_in_message(self):
        with pytest.raises(ValueError, match="Invalid table_name"):
            _validate_identifier("", field="table_name")


class TestMapSqlalchemyTypeToBackend:
    def test_postgresql_serial(self):
        result = _map_sqlalchemy_type_to_backend("INTEGER", is_primary_key=True, backend="postgresql")
        assert result == "SERIAL"

    def test_postgresql_bigserial(self):
        result = _map_sqlalchemy_type_to_backend("BIGINTEGER", is_primary_key=True, backend="postgresql")
        assert result == "BIGSERIAL"

    def test_postgresql_autoincrement_false_skips_serial(self):
        result = _map_sqlalchemy_type_to_backend("INTEGER", is_primary_key=True, autoincrement=False, backend="postgresql")
        assert result == "INTEGER"

    def test_postgresql_not_primary_key_skips_serial(self):
        result = _map_sqlalchemy_type_to_backend("INTEGER", is_primary_key=False, backend="postgresql")
        assert result == "INTEGER"

    def test_postgresql_bigserial_case_insensitive(self):
        result = _map_sqlalchemy_type_to_backend("biginteger", is_primary_key=True, backend="postgresql")
        assert result == "BIGSERIAL"

    def test_mysql_boolean(self):
        result = _map_sqlalchemy_type_to_backend("BOOLEAN", backend="mysql")
        assert result == "TINYINT(1)"

    def test_mysql_serial(self):
        result = _map_sqlalchemy_type_to_backend("SERIAL", backend="mysql")
        assert result == "BIGINT UNSIGNED"

    def test_mysql_passthrough(self):
        result = _map_sqlalchemy_type_to_backend("VARCHAR(255)", backend="mysql")
        assert result == "VARCHAR(255)"

    def test_mariadb_boolean(self):
        result = _map_sqlalchemy_type_to_backend("BOOLEAN", backend="mariadb")
        assert result == "TINYINT(1)"

    def test_mariadb_serial(self):
        result = _map_sqlalchemy_type_to_backend("SERIAL", backend="mariadb")
        assert result == "BIGINT UNSIGNED"

    def test_postgresql_datetime_mapping(self):
        result = _map_sqlalchemy_type_to_backend("DATETIME", backend="postgresql")
        assert result == "TIMESTAMP"

    def test_postgresql_bytea_mapping(self):
        result = _map_sqlalchemy_type_to_backend("BLOB", backend="postgresql")
        assert result == "BYTEA"

    def test_postgresql_bytea_passthrough(self):
        result = _map_sqlalchemy_type_to_backend("BYTEA", backend="postgresql")
        assert result == "BYTEA"

    def test_postgresql_passthrough(self):
        result = _map_sqlalchemy_type_to_backend("TEXT", backend="postgresql")
        assert result == "TEXT"


class TestGetBackendName:
    def test_get_backend_name_sqlite_fallback(self):
        from dbwarden.engine.model_discovery.type_mapping import _get_backend_name

        result = _get_backend_name("nonexistent_db")
        assert result == "sqlite"

    def test_get_backend_name_from_config(self):
        from unittest.mock import patch
        from dbwarden.engine.model_discovery.type_mapping import _get_backend_name

        mock_config = type("MockConfig", (), {"database_type": "postgresql"})()
        with patch("dbwarden.engine.model_discovery.type_mapping.get_database", return_value=mock_config):
            result = _get_backend_name("my_db")
            assert result == "postgresql"

    def test_snapshot_get_backend_delegates_to_type_mapping(self):
        """Regression: snapshot.utils._get_backend delegates to
        type_mapping._get_backend_name, so both functions return the same
        fallback value when config resolution fails."""
        from dbwarden.engine.snapshot.utils import _get_backend as snap_backend
        from dbwarden.engine.model_discovery.type_mapping import _get_backend_name

        result_snap = snap_backend("nonexistent_db")
        result_tm = _get_backend_name("nonexistent_db")
        assert result_snap == result_tm == "sqlite"


class TestMapSqlalchemyTypeToBackendSqlite:
    def test_sqlite_backend(self):
        result = _map_sqlalchemy_type_to_backend("INTEGER", backend="sqlite")
        assert result == "INTEGER"

    def test_sqlite_backend_translates_uuid(self):
        result = _map_sqlalchemy_type_to_backend("UUID", backend="sqlite")
        assert result == "TEXT"

    def test_unknown_backend_passthrough(self):
        result = _map_sqlalchemy_type_to_backend("INTEGER", backend="unknown")
        assert result == "INTEGER"
