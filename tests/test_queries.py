from dbwarden.connection import queries
from dbwarden.connection.queries import QueryMethod, DEFAULT_POSTGRES_SCHEMA


def test_postgres_migration_table_query_uses_filename(monkeypatch):
    monkeypatch.setattr(queries, "_get_backend_name", lambda db_name=None: "postgresql")
    sql = queries.get_query(QueryMethod.CREATE_MIGRATIONS_TABLE)
    assert "filename" in sql
    assert "BIGSERIAL" not in sql


def test_sqlite_migration_table_query_uses_filename(monkeypatch):
    monkeypatch.setattr(queries, "_get_backend_name", lambda db_name=None: "sqlite")
    sql = queries.get_query(QueryMethod.CREATE_MIGRATIONS_TABLE)
    assert "filename" in sql
    assert "AUTOINCREMENT" not in sql


def test_query_uses_custom_migration_table(monkeypatch):
    class FakeConfig:
        migration_table = "custom_migrations"

    monkeypatch.setattr(queries, "_get_backend_name", lambda db_name=None: "sqlite")
    monkeypatch.setattr(queries, "get_database", lambda db_name=None: FakeConfig())
    sql = queries.get_query(QueryMethod.GET_MIGRATED_VERSIONS)
    assert "custom_migrations" in sql
    assert "dbwarden_migrations" not in sql


def test_postgres_schema_default_public(monkeypatch):
    monkeypatch.setattr(queries, "_get_backend_name", lambda db_name=None: "postgresql")
    class FakeConfig:
        postgres_schema = None
    monkeypatch.setattr(queries, "get_database", lambda db_name=None: FakeConfig())
    sql = queries.get_query(QueryMethod.CREATE_MIGRATIONS_TABLE)
    assert "public._dbwarden_migrations" in sql


def test_postgres_schema_custom_schema(monkeypatch):
    monkeypatch.setattr(queries, "_get_backend_name", lambda db_name=None: "postgresql")
    class FakeConfig:
        postgres_schema = "myapp"
    monkeypatch.setattr(queries, "get_database", lambda db_name=None: FakeConfig())
    sql = queries.get_query(QueryMethod.CREATE_MIGRATIONS_TABLE)
    assert "myapp._dbwarden_migrations" in sql
    assert "public." not in sql


def test_postgres_seed_query_uses_schema(monkeypatch):
    monkeypatch.setattr(queries, "_get_backend_name", lambda db_name=None: "postgresql")
    class FakeConfig:
        postgres_schema = "custom"
    monkeypatch.setattr(queries, "get_database", lambda db_name=None: FakeConfig())
    sql = queries.get_seed_query(QueryMethod.CREATE_SEEDS_TABLE)
    assert "custom._dbwarden_seeds" in sql
    assert "public." not in sql


def test_postgres_check_table_exists_uses_schema(monkeypatch):
    monkeypatch.setattr(queries, "_get_backend_name", lambda db_name=None: "postgresql")
    class FakeConfig:
        postgres_schema = "myschema"
    monkeypatch.setattr(queries, "get_database", lambda db_name=None: FakeConfig())
    sql = queries.get_query(QueryMethod.CHECK_IF_MIGRATIONS_TABLE_EXISTS)
    assert "myschema" in sql


def test_get_schema_name_default(monkeypatch):
    def _raise(*_a, **_kw):
        raise Exception("no config")
    monkeypatch.setattr(queries, "get_database", _raise)
    assert queries.get_schema_name() == DEFAULT_POSTGRES_SCHEMA


def test_get_schema_name_from_config(monkeypatch):
    class FakeConfig:
        postgres_schema = "tenant"
    monkeypatch.setattr(queries, "get_database", lambda db_name=None: FakeConfig())
    assert queries.get_schema_name() == "tenant"


def test_clickhouse_migration_table_has_engine(monkeypatch):
    monkeypatch.setattr(queries, "_get_backend_name", lambda db_name=None: "clickhouse")
    sql = queries.get_query(QueryMethod.CREATE_MIGRATIONS_TABLE)
    assert "ENGINE = MergeTree()" in sql
    assert "ORDER BY filename" in sql


def test_clickhouse_delete_uses_alter(monkeypatch):
    monkeypatch.setattr(queries, "_get_backend_name", lambda db_name=None: "clickhouse")
    sql = queries.get_query(QueryMethod.DELETE_VERSION)
    assert "ALTER TABLE" in sql
    assert "DELETE" in sql


def test_clickhouse_optimize_migrations_table(monkeypatch):
    monkeypatch.setattr(queries, "_get_backend_name", lambda db_name=None: "clickhouse")
    sql = queries.get_query(QueryMethod.OPTIMIZE_MIGRATIONS_TABLE)
    assert "OPTIMIZE TABLE" in sql
    assert "FINAL" in sql


def test_clickhouse_get_table_names(monkeypatch):
    monkeypatch.setattr(queries, "_get_backend_name", lambda db_name=None: "clickhouse")
    sql = queries.get_query(QueryMethod.GET_TABLE_NAMES)
    assert "system.tables" in sql
    assert "database = currentDatabase()" in sql


def test_mysql_migration_table_has_engine(monkeypatch):
    monkeypatch.setattr(queries, "_get_backend_name", lambda db_name=None: "mysql")
    sql = queries.get_query(QueryMethod.CREATE_MIGRATIONS_TABLE)
    assert "TIMESTAMP DEFAULT CURRENT_TIMESTAMP" in sql
    assert "filename VARCHAR(500) UNIQUE" in sql


def test_mysql_check_table_exists(monkeypatch):
    monkeypatch.setattr(queries, "_get_backend_name", lambda db_name=None: "mysql")
    sql = queries.get_query(QueryMethod.CHECK_IF_MIGRATIONS_TABLE_EXISTS)
    assert "information_schema.tables" in sql
    assert "DATABASE()" in sql
    assert "{migration_table}" not in sql
    assert "_dbwarden_migrations" in sql


def test_mariadb_behaves_like_mysql(monkeypatch):
    monkeypatch.setattr(queries, "_get_backend_name", lambda db_name=None: "mariadb")
    sql = queries.get_query(QueryMethod.CREATE_MIGRATIONS_TABLE)
    assert "TIMESTAMP DEFAULT CURRENT_TIMESTAMP" in sql
    assert "filename VARCHAR(500) UNIQUE" in sql


def test_get_migration_table_name_default(monkeypatch):
    def _raise(*_a, **_kw):
        raise Exception("no config")
    monkeypatch.setattr(queries, "get_database", _raise)
    assert queries.get_migration_table_name() == queries.DEFAULT_MIGRATION_TABLE


def test_get_migration_table_name_custom(monkeypatch):
    class FakeConfig:
        migration_table = "my_migrations"
    monkeypatch.setattr(queries, "get_database", lambda db_name=None: FakeConfig())
    assert queries.get_migration_table_name() == "my_migrations"


def test_get_seed_table_name_default(monkeypatch):
    def _raise(*_a, **_kw):
        raise Exception("no config")
    monkeypatch.setattr(queries, "get_database", _raise)
    assert queries.get_seed_table_name() == queries.DEFAULT_SEEDS_TABLE


def test_get_seed_table_name_custom(monkeypatch):
    class FakeConfig:
        seed_table = "my_seeds"
    monkeypatch.setattr(queries, "get_database", lambda db_name=None: FakeConfig())
    assert queries.get_seed_table_name() == "my_seeds"


def test_clickhouse_get_table_columns_uses_params(monkeypatch):
    monkeypatch.setattr(queries, "_get_backend_name", lambda db_name=None: "clickhouse")
    sql = queries.get_query(QueryMethod.GET_TABLE_COLUMNS)
    assert "system.columns" in sql
    assert ":table_name" in sql


def test_get_query_method_missing_from_backend_returns_empty(monkeypatch):
    monkeypatch.setattr(queries, "_get_backend_name", lambda db_name=None: "sqlite")
    sql = queries.get_query(QueryMethod.OPTIMIZE_MIGRATIONS_TABLE)
    assert sql == ""


def test_get_seed_query_method_missing_from_backend_returns_empty(monkeypatch):
    monkeypatch.setattr(queries, "_get_backend_name", lambda db_name=None: "sqlite")
    sql = queries.get_seed_query(QueryMethod.OPTIMIZE_SEEDS_TABLE)
    assert sql == ""


class TestModelStateQueries:
    def test_sqlite_create_model_state_table(self, monkeypatch):
        monkeypatch.setattr(queries, "_get_backend_name", lambda db_name=None: "sqlite")
        sql = queries.get_query(QueryMethod.CREATE_MODEL_STATE_TABLE)
        assert "CREATE TABLE" in sql
        assert "_dbwarden_model_state" in sql
        assert "model_state TEXT" in sql
        assert "CHECK (id = 1)" in sql

    def test_sqlite_upsert_model_state(self, monkeypatch):
        monkeypatch.setattr(queries, "_get_backend_name", lambda db_name=None: "sqlite")
        sql = queries.get_query(QueryMethod.UPSERT_MODEL_STATE)
        assert "INSERT OR REPLACE" in sql
        assert ":state" in sql
        assert ":fmt" in sql

    def test_sqlite_get_model_state(self, monkeypatch):
        monkeypatch.setattr(queries, "_get_backend_name", lambda db_name=None: "sqlite")
        sql = queries.get_query(QueryMethod.GET_MODEL_STATE)
        assert "SELECT model_state" in sql
        assert "WHERE id = 1" in sql

    def test_postgres_create_model_state_table(self, monkeypatch):
        monkeypatch.setattr(queries, "_get_backend_name", lambda db_name=None: "postgresql")
        class FakeConfig:
            postgres_schema = None
        monkeypatch.setattr(queries, "get_database", lambda db_name=None: FakeConfig())
        sql = queries.get_query(QueryMethod.CREATE_MODEL_STATE_TABLE)
        assert "public._dbwarden_model_state" in sql
        assert "JSONB" in sql

    def test_postgres_upsert_model_state(self, monkeypatch):
        monkeypatch.setattr(queries, "_get_backend_name", lambda db_name=None: "postgresql")
        class FakeConfig:
            postgres_schema = None
        monkeypatch.setattr(queries, "get_database", lambda db_name=None: FakeConfig())
        sql = queries.get_query(QueryMethod.UPSERT_MODEL_STATE)
        assert "ON CONFLICT" in sql
        assert ":state::jsonb" in sql

    def test_postgres_get_model_state(self, monkeypatch):
        monkeypatch.setattr(queries, "_get_backend_name", lambda db_name=None: "postgresql")
        class FakeConfig:
            postgres_schema = None
        monkeypatch.setattr(queries, "get_database", lambda db_name=None: FakeConfig())
        sql = queries.get_query(QueryMethod.GET_MODEL_STATE)
        assert "FROM public._dbwarden_model_state" in sql

    def test_mysql_create_model_state_table(self, monkeypatch):
        monkeypatch.setattr(queries, "_get_backend_name", lambda db_name=None: "mysql")
        sql = queries.get_query(QueryMethod.CREATE_MODEL_STATE_TABLE)
        assert "model_state JSON" in sql
        assert "INT PRIMARY KEY" in sql

    def test_mysql_upsert_model_state(self, monkeypatch):
        monkeypatch.setattr(queries, "_get_backend_name", lambda db_name=None: "mysql")
        sql = queries.get_query(QueryMethod.UPSERT_MODEL_STATE)
        assert "ON DUPLICATE KEY" in sql

    def test_clickhouse_create_model_state_table(self, monkeypatch):
        monkeypatch.setattr(queries, "_get_backend_name", lambda db_name=None: "clickhouse")
        sql = queries.get_query(QueryMethod.CREATE_MODEL_STATE_TABLE)
        assert "ReplacingMergeTree" in sql
        assert "model_state String" in sql
        assert "ORDER BY id" in sql

    def test_clickhouse_upsert_model_state(self, monkeypatch):
        monkeypatch.setattr(queries, "_get_backend_name", lambda db_name=None: "clickhouse")
        sql = queries.get_query(QueryMethod.UPSERT_MODEL_STATE)
        assert "INSERT INTO" in sql
        assert ":state" in sql

    def test_clickhouse_get_model_state(self, monkeypatch):
        monkeypatch.setattr(queries, "_get_backend_name", lambda db_name=None: "clickhouse")
        sql = queries.get_query(QueryMethod.GET_MODEL_STATE)
        assert "SELECT model_state" in sql
        assert "WHERE id = 1" in sql
