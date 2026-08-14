from dbwarden.sql import split_sql_statements


def test_sql_splitter_preserves_semicolons_in_strings():
    assert split_sql_statements("INSERT INTO t VALUES ('a;b'); SELECT 1;") == [
        "INSERT INTO t VALUES ('a;b')",
        "SELECT 1",
    ]


def test_sql_splitter_preserves_semicolons_in_comments():
    assert split_sql_statements("SELECT 1 /* ; */; -- ;\nSELECT 2") == [
        "SELECT 1 /* ; */",
        "-- ;\nSELECT 2",
    ]
