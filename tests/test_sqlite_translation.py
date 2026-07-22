import pytest

from dbwarden.engine.sqlite_translation import (
    translate_default_to_sqlite,
    translate_type_to_sqlite,
    _unwrap_clickhouse_type,
)


class TestUnwrapClickhouseType:
    def test_simple_type(self):
        assert _unwrap_clickhouse_type("INTEGER") == "INTEGER"

    def test_nullable(self):
        assert _unwrap_clickhouse_type("NULLABLE(UINT64)") == "UINT64"

    def test_low_cardinality(self):
        assert _unwrap_clickhouse_type("LOWCARDINALITY(STRING)") == "STRING"

    def test_low_cardinality_nullable(self):
        result = _unwrap_clickhouse_type("LOWCARDINALITY(NULLABLE(STRING))")
        assert result == "STRING"

    def test_nullable_low_cardinality(self):
        result = _unwrap_clickhouse_type("NULLABLE(LOWCARDINALITY(STRING))")
        assert result == "STRING"


class TestSqliteTypeTranslation:
    def test_translates_postgres_uuid_to_text(self):
        translated, warning = translate_type_to_sqlite("UUID")
        assert translated == "TEXT"
        assert warning is not None

    def test_translates_clickhouse_nullable_uint64_to_integer(self):
        translated, warning = translate_type_to_sqlite("Nullable(UInt64)")
        assert translated == "INTEGER"
        assert warning is not None

    def test_text_maps_to_text_no_warning(self):
        translated, warning = translate_type_to_sqlite("TEXT")
        assert translated == "TEXT"
        assert warning is None

    def test_array_type_falls_back(self):
        translated, warning = translate_type_to_sqlite("ARRAY(INTEGER)")
        assert translated == "TEXT"

    def test_supported_prefix(self):
        translated, warning = translate_type_to_sqlite("INTEGER(10)")
        assert translated == "INTEGER(10)"
        assert warning is None

    def test_supported_prefix_varchar(self):
        translated, warning = translate_type_to_sqlite("VARCHAR(255)")
        assert translated == "VARCHAR(255)"
        assert warning is None

    def test_decimal_type(self):
        translated, warning = translate_type_to_sqlite("DECIMAL(10,2)")
        assert translated == "NUMERIC"
        assert "Translated" in (warning or "")

    def test_numeric_type(self):
        translated, warning = translate_type_to_sqlite("NUMERIC(10,2)")
        assert translated == "NUMERIC"
        assert "Translated" in (warning or "")

    def test_datetime64_type(self):
        translated, warning = translate_type_to_sqlite("DATETIME64(3)")
        assert translated == "DATETIME"
        assert "Translated" in (warning or "")

    def test_fixedstring_type(self):
        translated, warning = translate_type_to_sqlite("FIXEDSTRING(32)")
        assert translated == "TEXT"
        assert "Translated" in (warning or "")

    def test_array_type_raises_in_strict(self):
        with pytest.raises(ValueError, match="not supported by SQLite"):
            translate_type_to_sqlite("ARRAY(INTEGER)", strict=True)

    def test_unknown_type_raises_in_strict(self):
        with pytest.raises(ValueError, match="not supported by SQLite"):
            translate_type_to_sqlite("GEOGRAPHY", strict=True)

    def test_low_cardinality_nullable(self):
        translated, warning = translate_type_to_sqlite("LowCardinality(Nullable(UInt64))")
        assert translated == "INTEGER"
        assert warning is not None

    def test_falls_back_unknown_type_to_text(self):
        translated, warning = translate_type_to_sqlite("GEOGRAPHY")
        assert translated == "TEXT"
        assert "Falling back to TEXT" in (warning or "")

    def test_unknown_type_raises_in_strict_mode(self):
        with pytest.raises(ValueError, match="not supported by SQLite"):
            translate_type_to_sqlite("GEOGRAPHY", strict=True)


class TestSqliteDefaultTranslation:
    def test_none_default(self):
        translated, warning = translate_default_to_sqlite(None)
        assert translated is None
        assert warning is None

    def test_empty_default(self):
        translated, warning = translate_default_to_sqlite("")
        assert translated is None
        assert warning is None

    def test_current_timestamp(self):
        translated, warning = translate_default_to_sqlite("CURRENT_TIMESTAMP")
        assert translated == "CURRENT_TIMESTAMP"
        assert warning is None

    def test_current_date(self):
        translated, warning = translate_default_to_sqlite("CURRENT_DATE")
        assert translated == "CURRENT_DATE"
        assert warning is None

    def test_current_time(self):
        translated, warning = translate_default_to_sqlite("CURRENT_TIME")
        assert translated == "CURRENT_TIME"
        assert warning is None

    def test_regular_default_passes_through(self):
        translated, warning = translate_default_to_sqlite("42")
        assert translated == "42"
        assert warning is None

    def test_regular_text_default_passes_through(self):
        translated, warning = translate_default_to_sqlite("'active'")
        assert translated == "'active'"
        assert warning is None

    def test_removes_unsupported_default_in_non_strict_mode(self):
        translated, warning = translate_default_to_sqlite("now()")
        assert translated is None
        assert warning is not None

    def test_unsupported_default_raises_in_strict_mode(self):
        with pytest.raises(ValueError, match="not supported by SQLite"):
            translate_default_to_sqlite("gen_random_uuid()", strict=True)
