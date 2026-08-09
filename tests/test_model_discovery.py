import pytest
import tempfile
import os
from pathlib import Path

import dbwarden.engine.model_discovery as model_discovery
from dbwarden.engine.model_discovery import (
    load_model_from_path,
    discover_models_in_directory,
    extract_column_info,
    extract_table_from_model,
    generate_create_table_sql,
    generate_drop_object_sql,
    generate_drop_table_sql,
    ModelColumn,
    ModelTable,
)
from dbwarden.databases.pgsql import PGColumnMeta, PGTableMeta, pg
from dbwarden.databases.clickhouse import CHColumnMeta, CHTableMeta, ChIndexSpec, ch


class TestModelDiscovery:
    """Tests for SQLAlchemy model discovery."""

    def test_load_model_from_python_file(self):
        """Test loading a model from a Python file."""
        with tempfile.TemporaryDirectory() as tmpdir:
            model_content = """
from sqlalchemy import Column, Integer, String
from sqlalchemy.orm import declarative_base

Base = declarative_base()

class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True)
    name = Column(String(100))
"""
            model_path = os.path.join(tmpdir, "models.py")
            with open(model_path, "w") as f:
                f.write(model_content)

            module = load_model_from_path(model_path)

            assert module is not None
            assert hasattr(module, "User")
            assert module.User.__tablename__ == "users"

    def test_load_model_nonexistent_file(self):
        """Test loading from nonexistent file returns None."""
        result = load_model_from_path("/nonexistent/path/models.py")
        assert result is None

    def test_discover_models_in_directory(self):
        """Test discovering model files in a directory."""
        with tempfile.TemporaryDirectory() as tmpdir:
            subdir = os.path.join(tmpdir, "models")
            os.makedirs(subdir)

            with open(os.path.join(subdir, "user.py"), "w") as f:
                f.write("# User model")

            with open(os.path.join(subdir, "post.py"), "w") as f:
                f.write("# Post model")

            with open(os.path.join(subdir, "__init__.py"), "w") as f:
                f.write("")

            files = discover_models_in_directory(subdir)

            assert len(files) == 2
            assert any("user.py" in f for f in files)
            assert any("post.py" in f for f in files)

    def test_discover_models_includes_regular_files(self):
        """Test that regular Python files are included."""
        with tempfile.TemporaryDirectory() as tmpdir:
            subdir = os.path.join(tmpdir, "models")
            os.makedirs(subdir)

            with open(os.path.join(subdir, "__init__.py"), "w") as f:
                f.write("")

            with open(os.path.join(subdir, "user.py"), "w") as f:
                f.write("# User model")

            files = discover_models_in_directory(subdir)

            assert len(files) == 1
            assert "user.py" in files[0]

    def test_discover_models_empty_directory(self):
        """Test discovering in empty directory returns empty list."""
        with tempfile.TemporaryDirectory() as tmpdir:
            files = discover_models_in_directory(tmpdir)
            assert files == []

    def test_extract_table_from_model_preserves_typed_pg_meta(self, monkeypatch):
        from sqlalchemy import Integer, String
        from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
        from dbwarden.databases.pgsql import PGColumnMeta, PGTableMeta

        monkeypatch.setattr(model_discovery.type_mapping, "_get_backend_name", lambda db_name=None: "postgresql")

        class Base(DeclarativeBase):
            pass

        class User(Base):
            __tablename__ = "users"

            id: Mapped[int] = mapped_column(Integer, primary_key=True)
            email: Mapped[str] = mapped_column(String(255), nullable=False)

            class Meta(PGTableMeta):
                pg_fillfactor = 80

                class email(PGColumnMeta):
                    pg = pg.field(storage="extended")

        table = extract_table_from_model(User, db_name="primary")

        assert table is not None
        assert table.pg_table["pg_fillfactor"] == 80
        assert table.columns[1].pg_meta["pg_storage"] == "extended"

    def test_extract_table_from_model_skips_plain_storage(self, monkeypatch):
        from sqlalchemy import Integer, String
        from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
        from dbwarden.databases.pgsql import PGColumnMeta

        monkeypatch.setattr(model_discovery.type_mapping, "_get_backend_name", lambda db_name=None: "postgresql")

        class Base(DeclarativeBase):
            pass

        class User(Base):
            __tablename__ = "users"

            id: Mapped[int] = mapped_column(Integer, primary_key=True)
            email: Mapped[str] = mapped_column(String(255), nullable=False)

            class Meta:
                class email(PGColumnMeta):
                    pg = pg.field(storage="PLAIN")

        table = extract_table_from_model(User, db_name="primary")

        assert table is not None
        assert "pg_storage" not in table.columns[1].pg_meta


class TestModelColumn:
    """Tests for ModelColumn class."""

    def test_model_column_creation(self):
        """Test creating a ModelColumn."""
        col = ModelColumn(
            name="id",
            type="INTEGER",
            nullable=False,
            primary_key=True,
            unique=True,
            default=None,
            foreign_key=None,
        )

        assert col.name == "id"
        assert col.type == "INTEGER"
        assert col.nullable == False
        assert col.primary_key == True

    def test_model_column_to_dict(self):
        """Test ModelColumn to_dict method."""
        col = ModelColumn(
            name="email",
            type="VARCHAR(255)",
            nullable=False,
            primary_key=False,
            unique=True,
            default=None,
            foreign_key=None,
        )

        col_dict = col.to_dict()

        assert col_dict["name"] == "email"
        assert col_dict["type"] == "VARCHAR(255)"
        assert col_dict["unique"] == True

    def test_model_column_to_dict_includes_codec(self):
        col = ModelColumn(
            name="data",
            type="String",
            nullable=False,
            primary_key=False,
            unique=False,
            default=None,
            foreign_key=None,
            codec="ZSTD(3)",
        )

        col_dict = col.to_dict()
        assert col_dict["codec"] == "ZSTD(3)"


class TestModelTable:
    """Tests for ModelTable class."""

    def test_model_table_creation(self):
        """Test creating a ModelTable."""
        columns = [
            ModelColumn("id", "INTEGER", False, True, False, None, None),
            ModelColumn("name", "VARCHAR(100)", True, False, False, None, None),
        ]

        table = ModelTable(name="users", columns=columns)

        assert table.name == "users"
        assert len(table.columns) == 2

    def test_model_table_to_dict(self):
        """Test ModelTable to_dict method."""
        columns = [
            ModelColumn("id", "INTEGER", False, True, False, None, None),
        ]

        table = ModelTable(name="users", columns=columns)
        table_dict = table.to_dict()

        assert table_dict["name"] == "users"
        assert len(table_dict["columns"]) == 1

    def test_model_table_to_dict_includes_clickhouse_options(self):
        columns = [
            ModelColumn("id", "UInt64", False, True, False, None, None),
        ]

        table = ModelTable(
            name="events",
            columns=columns,
            clickhouse_options={"ch_engine": "MergeTree"},
        )

        table_dict = table.to_dict()
        assert table_dict["clickhouse_options"]["ch_engine"] == "MergeTree"


class TestSQLGeneration:
    """Tests for SQL generation from models."""

    def test_generate_create_table_sql(self):
        """Test generating CREATE TABLE SQL."""
        columns = [
            ModelColumn("id", "INTEGER", False, True, False, None, None),
            ModelColumn("name", "VARCHAR(100)", False, False, False, None, None),
            ModelColumn("email", "VARCHAR(255)", False, False, True, None, None),
        ]

        table = ModelTable(name="users", columns=columns)
        sql = generate_create_table_sql(table)

        assert "CREATE TABLE IF NOT EXISTS users" in sql
        assert "NOT NULL" in sql
        assert "PRIMARY KEY" in sql
        assert "UNIQUE" in sql

    def test_generate_postgresql_create_table_sql_includes_comment(self, monkeypatch):
        monkeypatch.setattr(model_discovery.type_mapping, "_get_backend_name", lambda db_name=None: "postgresql")

        table = ModelTable(
            name="users",
            columns=[ModelColumn("id", "INTEGER", False, True, False, None, None)],
            comment="User accounts",
        )

        sql = generate_create_table_sql(table)

        assert "COMMENT ON TABLE users IS 'User accounts';" in sql

    def test_generate_drop_table_sql(self):
        """Test generating DROP TABLE SQL."""
        sql = generate_drop_table_sql("users")
        assert sql == "DROP TABLE users"

    def test_generate_create_table_with_foreign_key(self):
        """Test generating CREATE TABLE with foreign key."""
        columns = [
            ModelColumn("id", "INTEGER", False, True, False, None, None),
            ModelColumn("user_id", "INTEGER", False, False, False, None, "users(id)"),
        ]

        table = ModelTable(name="posts", columns=columns)
        sql = generate_create_table_sql(table)

        assert "user_id INTEGER NOT NULL REFERENCES users(id)" in sql

    def test_generate_mysql_create_table_sql_with_options(self, monkeypatch):
        monkeypatch.setattr(model_discovery.type_mapping, "_get_backend_name", lambda db_name=None: "mysql")

        columns = [
            ModelColumn("id", "INT", False, True, False, None, None, my_meta={"my_unsigned": True}),
            ModelColumn(
                "updated_at",
                "TIMESTAMP",
                False,
                False,
                False,
                "CURRENT_TIMESTAMP",
                None,
                my_meta={"my_on_update": "CURRENT_TIMESTAMP"},
            ),
            ModelColumn(
                "email",
                "VARCHAR(255)",
                False,
                False,
                True,
                None,
                None,
                my_meta={"my_charset": "utf8mb4", "my_collate": "utf8mb4_unicode_ci"},
            ),
        ]

        table = ModelTable(
            name="users",
            columns=columns,
            my_table={
                "my_engine": "InnoDB",
                "my_charset": "utf8mb4",
                "my_collate": "utf8mb4_unicode_ci",
                "my_row_format": "DYNAMIC",
                "my_auto_increment": 10,
            },
        )
        sql = generate_create_table_sql(table)

        assert "id INT UNSIGNED NOT NULL PRIMARY KEY" in sql
        assert "updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP" in sql
        assert "email VARCHAR(255) NOT NULL UNIQUE CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci" in sql
        assert "ENGINE=InnoDB" in sql
        assert "DEFAULT CHARSET=utf8mb4" in sql
        assert "COLLATE=utf8mb4_unicode_ci" in sql
        assert "ROW_FORMAT=DYNAMIC" in sql
        assert "AUTO_INCREMENT=10" in sql

    def test_generate_mysql_add_column_sql_with_options(self, monkeypatch):
        monkeypatch.setattr(model_discovery.type_mapping, "_get_backend_name", lambda db_name=None: "mysql")

        column = ModelColumn(
            "updated_at",
            "TIMESTAMP",
            False,
            False,
            False,
            "CURRENT_TIMESTAMP",
            None,
            my_meta={"my_on_update": "CURRENT_TIMESTAMP"},
        )
        sql = model_discovery.generate_add_column_sql("users", column, db_name="primary")

        assert "ALTER TABLE users ADD COLUMN updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP" in sql

    def test_generate_clickhouse_add_column_sql_includes_codec(self, monkeypatch):
        monkeypatch.setattr(model_discovery.type_mapping, "_get_backend_name", lambda db_name=None: "clickhouse")

        column = ModelColumn(
            "payload",
            "String",
            False,
            False,
            False,
            None,
            None,
            ch_meta={"ch_type": "String", "ch_codec": "ZSTD(3)"},
        )

        sql = model_discovery.generate_add_column_sql("events", column, db_name="primary")

        assert "CODEC(ZSTD(3))" in sql

    def test_generate_clickhouse_add_column_sql_for_bool_omits_not_null(self, monkeypatch):
        monkeypatch.setattr(model_discovery.type_mapping, "_get_backend_name", lambda db_name=None: "clickhouse")

        column = ModelColumn(
            "is_merge",
            "Bool",
            False,
            False,
            False,
            None,
            None,
            ch_meta={"ch_default_expression": "FALSE"},
        )

        sql = model_discovery.generate_add_column_sql("commits", column, db_name="primary")

        assert sql == "ALTER TABLE commits ADD COLUMN is_merge Bool DEFAULT FALSE"
        assert "NOT NULL" not in sql

    def test_generate_postgresql_create_table_sql_uses_enum_type_name(self, monkeypatch):
        monkeypatch.setattr(model_discovery.type_mapping, "_get_backend_name", lambda db_name=None: "postgresql")

        columns = [
            ModelColumn(
                "resource",
                "enum",
                False,
                False,
                False,
                None,
                None,
                pg_meta={"pg_type": {"kind": "enum", "type_name": "keypermissionresource", "values": ["USER_PROFILE"]}},
            ),
        ]

        table = ModelTable(name="key_permissions", columns=columns)
        sql = generate_create_table_sql(table)

        assert "resource keypermissionresource NOT NULL" in sql
        assert "resource enum" not in sql

    def test_generate_clickhouse_create_table_sql_with_options(self, monkeypatch):
        monkeypatch.setattr(model_discovery.type_mapping, "_get_backend_name", lambda db_name=None: "clickhouse")

        columns = [
            ModelColumn("region", "String", False, True, False, None, None),
            ModelColumn("event_time", "DateTime", False, False, False, None, None),
        ]

        table = ModelTable(
            name="events",
            columns=columns,
            clickhouse_options={
                "ch_engine": ("ReplacingMergeTree", "version_column"),
                "ch_order_by": ["region", "event_time"],
                "ch_primary_key": "region",
                "ch_partition_by": "toYYYYMM(event_time)",
                "ch_sample_by": "intHash64(region)",
                "ch_ttl": ["event_time + INTERVAL 1 MONTH DELETE"],
            },
        )

        sql = generate_create_table_sql(table)

        assert "ENGINE = ReplacingMergeTree(version_column)" in sql
        assert "ORDER BY (region, event_time)" in sql
        assert "PRIMARY KEY region" in sql
        assert "PARTITION BY toYYYYMM(event_time)" in sql
        assert "SAMPLE BY intHash64(region)" in sql
        assert "TTL event_time + INTERVAL 1 MONTH DELETE" in sql

    def test_generate_clickhouse_create_table_sql_with_comments(self, monkeypatch):
        monkeypatch.setattr(model_discovery.type_mapping, "_get_backend_name", lambda db_name=None: "clickhouse")

        columns = [
            ModelColumn("id", "UInt64", False, True, False, None, None),
            ModelColumn("url", "String", False, False, False, None, None, comment="Visited URL"),
            ModelColumn("viewed_at", "DateTime", False, False, False, None, None, comment="Timestamp of the page view"),
        ]

        table = ModelTable(
            name="page_views",
            columns=columns,
            comment="Page view events",
        )

        sql = generate_create_table_sql(table)

        assert "COMMENT 'Page view events'" in sql
        assert "url String" in sql
        assert "NOT NULL" not in sql
        assert "viewed_at DateTime" in sql

    def test_generate_clickhouse_create_table_sql_with_composite_primary_key(self, monkeypatch):
        monkeypatch.setattr(model_discovery.type_mapping, "_get_backend_name", lambda db_name=None: "clickhouse")

        columns = [
            ModelColumn("region", "String", False, False, False, None, None),
            ModelColumn("event_time", "DateTime", False, False, False, None, None),
        ]

        table = ModelTable(
            name="events",
            columns=columns,
            clickhouse_options={
                "ch_order_by": ["region", "event_time"],
                "ch_primary_key": ["region", "event_time"],
            },
        )

        sql = generate_create_table_sql(table)

        assert "PRIMARY KEY (region, event_time)" in sql

    def test_generate_clickhouse_create_table_sql_with_codec(self, monkeypatch):
        monkeypatch.setattr(model_discovery.type_mapping, "_get_backend_name", lambda db_name=None: "clickhouse")

        columns = [
            ModelColumn("data", "String", False, False, False, None, None, codec="ZSTD(3)"),
        ]

        table = ModelTable(name="events", columns=columns)
        sql = generate_create_table_sql(table)

        assert "data String" in sql
        assert "CODEC(ZSTD(3))" in sql
        assert "ENGINE = MergeTree()" in sql

    def test_generate_clickhouse_create_table_sql_with_default_expression(self, monkeypatch):
        monkeypatch.setattr(model_discovery.type_mapping, "_get_backend_name", lambda db_name=None: "clickhouse")

        columns = [
            ModelColumn("event_time", "DateTime", False, False, False, None, None,
                        ch_meta={"ch_default_expression": "now()"}),
        ]

        table = ModelTable(name="events", columns=columns)
        sql = generate_create_table_sql(table)

        assert "event_time DateTime DEFAULT now()" in sql

    def test_generate_clickhouse_create_table_sql_with_materialized(self, monkeypatch):
        monkeypatch.setattr(model_discovery.type_mapping, "_get_backend_name", lambda db_name=None: "clickhouse")

        columns = [
            ModelColumn("full_name", "String", False, False, False, None, None,
                        ch_meta={"ch_materialized": "concat(first, ' ', last)"}),
        ]

        table = ModelTable(name="users", columns=columns)
        sql = generate_create_table_sql(table)

        assert "full_name String MATERIALIZED concat(first, ' ', last)" in sql

    def test_generate_clickhouse_create_table_sql_with_alias(self, monkeypatch):
        monkeypatch.setattr(model_discovery.type_mapping, "_get_backend_name", lambda db_name=None: "clickhouse")

        columns = [
            ModelColumn("area", "Float64", False, False, False, None, None,
                        ch_meta={"ch_alias": "width * height"}),
        ]

        table = ModelTable(name="metrics", columns=columns)
        sql = generate_create_table_sql(table)

        assert "area Float64 ALIAS width * height" in sql

    def test_generate_clickhouse_create_table_sql_with_ephemeral(self, monkeypatch):
        monkeypatch.setattr(model_discovery.type_mapping, "_get_backend_name", lambda db_name=None: "clickhouse")

        columns = [
            ModelColumn("computed", "Int32", False, False, False, None, None,
                        ch_meta={"ch_ephemeral": "4 + 5"}),
        ]

        table = ModelTable(name="test_table", columns=columns)
        sql = generate_create_table_sql(table)

        assert "computed Int32 EPHEMERAL 4 + 5" in sql

    def test_generate_clickhouse_create_table_sql_with_column_ttl(self, monkeypatch):
        monkeypatch.setattr(model_discovery.type_mapping, "_get_backend_name", lambda db_name=None: "clickhouse")

        columns = [
            ModelColumn("payload", "String", False, False, False, None, None,
                        ch_meta={"ch_ttl": "created_at + INTERVAL 30 DAY"}),
        ]

        table = ModelTable(name="events", columns=columns)
        sql = generate_create_table_sql(table)

        assert "payload String TTL created_at + INTERVAL 30 DAY" in sql

    def test_generate_clickhouse_create_table_sql_with_default_and_codec(self, monkeypatch):
        monkeypatch.setattr(model_discovery.type_mapping, "_get_backend_name", lambda db_name=None: "clickhouse")

        columns = [
            ModelColumn("event_time", "DateTime", False, False, False, None, None,
                        codec="ZSTD(3)",
                        ch_meta={"ch_default_expression": "now()", "ch_ttl": "event_time + INTERVAL 90 DAY"}),
        ]

        table = ModelTable(name="events", columns=columns)
        sql = generate_create_table_sql(table)

        assert "event_time DateTime DEFAULT now()" in sql
        assert "CODEC(ZSTD(3))" in sql
        assert "TTL event_time + INTERVAL 90 DAY" in sql

    def test_generate_clickhouse_create_table_sql_comment_before_codec(self, monkeypatch):
        monkeypatch.setattr(model_discovery.type_mapping, "_get_backend_name", lambda db_name=None: "clickhouse")

        columns = [
            ModelColumn("url", "String", False, False, False, None, None,
                        codec="ZSTD(3)", comment="Visited URL"),
        ]

        table = ModelTable(name="page_views", columns=columns)
        sql = generate_create_table_sql(table)

        assert "url String COMMENT 'Visited URL' CODEC(ZSTD(3))" in sql

    def test_generate_clickhouse_create_table_sql_key_columns_are_not_null(self, monkeypatch):
        monkeypatch.setattr(model_discovery.type_mapping, "_get_backend_name", lambda db_name=None: "clickhouse")

        columns = [
            ModelColumn("event_date", "Date", False, False, False, None, None,
                        ch_meta={"ch_type": "Nullable(Date)"}),
            ModelColumn("id", "Int64", False, False, False, None, None,
                        ch_meta={"ch_type": "Nullable(Int64)"}),
            ModelColumn("payload", "String", False, False, False, None, None,
                        ch_meta={"ch_type": "Nullable(String)"}),
        ]

        table = ModelTable(
            name="events",
            columns=columns,
            clickhouse_options={
                "ch_order_by": ["event_date", "id"],
                "ch_partition_by": "toYYYYMM(event_date)",
            },
        )
        sql = generate_create_table_sql(table)

        assert "event_date Date NOT NULL" in sql
        assert "id Int64 NOT NULL" in sql
        assert "payload Nullable(String)" in sql
        assert "Nullable(event_date)" not in sql
        assert "Nullable(id)" not in sql

    def test_generate_clickhouse_add_column_sql_comment_before_codec(self, monkeypatch):
        monkeypatch.setattr(model_discovery.type_mapping, "_get_backend_name", lambda db_name=None: "clickhouse")

        column = ModelColumn("url", "String", False, False, False, None, None,
                             codec="ZSTD(3)", comment="Visited URL")

        sql = model_discovery.generate_add_column_sql("page_views", column, db_name="primary")

        assert "url String COMMENT 'Visited URL' CODEC(ZSTD(3))" in sql

    def test_generate_clickhouse_add_column_sql_with_default_expression(self, monkeypatch):
        monkeypatch.setattr(model_discovery.type_mapping, "_get_backend_name", lambda db_name=None: "clickhouse")

        column = ModelColumn("status", "String", False, False, False, None, None,
                             ch_meta={"ch_default_expression": "'active'"})

        sql = model_discovery.generate_add_column_sql("users", column, db_name="primary")

        assert "DEFAULT 'active'" in sql

    def test_generate_clickhouse_add_column_sql_with_materialized(self, monkeypatch):
        monkeypatch.setattr(model_discovery.type_mapping, "_get_backend_name", lambda db_name=None: "clickhouse")

        column = ModelColumn("full_name", "String", False, False, False, None, None,
                             ch_meta={"ch_materialized": "concat(first, ' ', last)"})

        sql = model_discovery.generate_add_column_sql("users", column, db_name="primary")

        assert "MATERIALIZED concat(first, ' ', last)" in sql

    def test_generate_clickhouse_create_table_sql_with_projection(self, monkeypatch):
        monkeypatch.setattr(model_discovery.type_mapping, "_get_backend_name", lambda db_name=None: "clickhouse")

        columns = [
            ModelColumn("author", "String", False, False, False, None, None),
            ModelColumn("created_at", "DateTime", False, False, False, None, None),
        ]

        table = ModelTable(
            name="posts",
            columns=columns,
            clickhouse_options={
                "ch_order_by": ["author", "created_at"],
                "ch_projections": [
                    {"name": "by_author", "query": "SELECT * ORDER BY author"}
                ],
            },
        )

        sql = generate_create_table_sql(table)

        assert "PROJECTION by_author (SELECT * ORDER BY author)" in sql

    def test_generate_clickhouse_materialized_view_sql(self, monkeypatch):
        monkeypatch.setattr(model_discovery.type_mapping, "_get_backend_name", lambda db_name=None: "clickhouse")

        columns = [
            ModelColumn("group_col", "String", False, True, False, None, None),
            ModelColumn("total", "UInt64", False, False, False, None, None),
        ]

        table = ModelTable(
            name="mv_name",
            columns=columns,
            clickhouse_options={
                "ch_select_statement": "SELECT group_col, count() AS total FROM source_table GROUP BY group_col",
                "ch_engine": "SummingMergeTree",
                "ch_order_by": ["group_col"],
            },
            object_type="materialized_view",
        )

        sql = generate_create_table_sql(table)

        assert "CREATE MATERIALIZED VIEW IF NOT EXISTS mv_name" in sql
        assert "ENGINE = SummingMergeTree()" in sql
        assert "ORDER BY (group_col)" in sql
        assert "AS SELECT group_col, count() AS total FROM source_table GROUP BY group_col" in sql

    def test_generate_drop_object_sql_for_materialized_view(self):
        table = ModelTable(
            name="mv_name",
            columns=[],
            object_type="materialized_view",
        )

        assert generate_drop_object_sql(table) == "DROP VIEW IF EXISTS mv_name"

    def test_generate_replicated_clickhouse_engine_with_zookeeper(self, monkeypatch):
        monkeypatch.setattr(model_discovery.type_mapping, "_get_backend_name", lambda db_name=None: "clickhouse")

        columns = [
            ModelColumn("id", "UInt64", False, True, False, None, None),
            ModelColumn("data", "String", False, False, False, None, None),
        ]

        table = ModelTable(
            name="replicated_table",
            columns=columns,
            clickhouse_options={
                "ch_engine": "ReplicatedMergeTree",
                "ch_zookeeper_path": "'/clickhouse/tables/shard1'",
                "ch_replica_name": "'{replica}'",
                "ch_order_by": ["id"],
            },
        )

        sql = generate_create_table_sql(table)

        assert "ENGINE = ReplicatedMergeTree('/clickhouse/tables/shard1', '{replica}')" in sql

    def test_generate_replicated_clickhouse_engine_with_zookeeper_tuple(self, monkeypatch):
        monkeypatch.setattr(model_discovery.type_mapping, "_get_backend_name", lambda db_name=None: "clickhouse")

        table = ModelTable(
            name="replicated_table",
            columns=[ModelColumn("id", "UInt64", False, True, False, None, None)],
            clickhouse_options={
                "ch_engine": ("ReplicatedReplacingMergeTree", "ver_col"),
                "ch_zookeeper_path": "'/zk/path'",
                "ch_replica_name": "'{replica}'",
                "ch_order_by": ["id"],
            },
        )

        sql = generate_create_table_sql(table)

        assert "ENGINE = ReplicatedReplacingMergeTree('/zk/path', '{replica}', ver_col)" in sql

    def test_generate_dictionary_sql(self, monkeypatch):
        monkeypatch.setattr(model_discovery.type_mapping, "_get_backend_name", lambda db_name=None: "clickhouse")

        columns = [
            ModelColumn("code", "String", False, True, False, None, None),
            ModelColumn("name", "String", False, False, False, None, None),
        ]

        table = ModelTable(
            name="country_codes",
            columns=columns,
            clickhouse_options={
                "ch_dictionary": True,
                "ch_dict_layout": "FLAT()",
                "ch_dict_source": "CLICKHOUSE(HOST 'localhost' TABLE 'source')",
                "ch_dict_lifetime": "MIN 0 MAX 3600",
                "ch_dict_primary_key": "code",
            },
            object_type="dictionary",
        )

        sql = generate_create_table_sql(table)

        assert "CREATE DICTIONARY IF NOT EXISTS country_codes" in sql
        assert "PRIMARY KEY code" in sql
        assert "SOURCE(CLICKHOUSE(HOST 'localhost' TABLE 'source'))" in sql
        assert "LIFETIME(MIN 0 MAX 3600)" in sql
        assert "LAYOUT(FLAT())" in sql

    def test_generate_drop_dictionary_sql(self):
        table = ModelTable(
            name="country_codes",
            columns=[],
            object_type="dictionary",
        )

        assert generate_drop_object_sql(table) == "DROP DICTIONARY country_codes"

    def test_generate_clickhouse_create_table_sql_with_settings(self, monkeypatch):
        monkeypatch.setattr(model_discovery.type_mapping, "_get_backend_name", lambda db_name=None: "clickhouse")

        columns = [
            ModelColumn("id", "UInt64", False, True, False, None, None),
        ]

        table = ModelTable(
            name="events",
            columns=columns,
            clickhouse_options={
                "ch_engine": "MergeTree",
                "ch_order_by": ["id"],
                "ch_settings": {"index_granularity": "8192", "min_rows_for_wide_part": "0"},
            },
        )

        sql = generate_create_table_sql(table)

        assert "SETTINGS index_granularity=8192, min_rows_for_wide_part=0" in sql


class TestColumnExtraction:
    """Tests for column information extraction."""

    def test_extract_column_from_sqlalchemy_column(self):
        """Test extracting column info from SQLAlchemy column."""
        from sqlalchemy import Column, Integer, String

        col_obj = Column("id", Integer, primary_key=True)

        col = extract_column_info(col_obj)

        assert col is not None
        assert col.name == "id"
        assert col.primary_key == True

    def test_extract_column_nullable(self):
        """Test extracting nullable column."""
        from sqlalchemy import Column, String

        col_obj = Column("name", String(100), nullable=True)

        col = extract_column_info(col_obj)

        assert col is not None
        assert col.nullable == True

    def test_extract_column_not_nullable(self):
        """Test extracting non-nullable column."""
        from sqlalchemy import Column, String

        col_obj = Column("name", String(100), nullable=False)

        col = extract_column_info(col_obj)

        assert col is not None
        assert col.nullable == False

    def test_extract_column_translates_jsonb_to_text_for_sqlite(self, monkeypatch):
        from sqlalchemy import Column
        from sqlalchemy.dialects.postgresql import JSONB

        monkeypatch.setattr(model_discovery.type_mapping, "_get_backend_name", lambda db_name=None: "sqlite")

        col_obj = Column("payload", JSONB)

        col = extract_column_info(col_obj)

        assert col is not None
        assert col.type == "TEXT"

    def test_extract_jsonb_column_sets_pg_type_for_postgres(self, monkeypatch):
        from sqlalchemy import Column
        from sqlalchemy.dialects.postgresql import JSONB

        monkeypatch.setattr(model_discovery.type_mapping, "_get_backend_name", lambda db_name=None: "postgresql")

        col_obj = Column("payload", JSONB)

        col = extract_column_info(col_obj)

        assert col is not None
        assert col.type == "jsonb"
        assert col.pg_meta.get("pg_type") == {"kind": "jsonb"}

    def test_extract_column_falls_back_unknown_type_to_text_for_sqlite(self, monkeypatch):
        from sqlalchemy import Column
        from sqlalchemy.types import UserDefinedType

        monkeypatch.setattr(model_discovery.type_mapping, "_get_backend_name", lambda db_name=None: "sqlite")

        class Geography(UserDefinedType):
            def get_col_spec(self, **kw):
                return "GEOGRAPHY"

        col_obj = Column("location", Geography())

        col = extract_column_info(col_obj)

        assert col is not None
        assert col.type == "TEXT"

    def test_extract_column_uses_clickhouse_type_and_codec_hints(self, monkeypatch):
        from sqlalchemy import Column, String

        monkeypatch.setattr(model_discovery.type_mapping, "_get_backend_name", lambda db_name=None: "clickhouse")

        col_obj = Column(
            "payload",
            String,
            info={
                "clickhouse_type": "LowCardinality(String)",
                "ch_codec": "ZSTD(3)",
            },
        )

        col = extract_column_info(col_obj)

        assert col is not None
        assert col.type == "LowCardinality(String)"
        assert col.codec == "ZSTD(3)"
        assert col.ch_meta.get("ch_codec") == "ZSTD(3)"
        assert col.ch_meta.get("ch_type") == "LowCardinality(String)"

    def test_extract_column_preserves_clickhouse_user_defined_type(self, monkeypatch):
        from sqlalchemy import Column
        from sqlalchemy.types import UserDefinedType

        monkeypatch.setattr(model_discovery.type_mapping, "_get_backend_name", lambda db_name=None: "clickhouse")

        class LowCardinalityString(UserDefinedType):
            def get_col_spec(self, **kw):
                return "LowCardinality(String)"

        col_obj = Column("payload", LowCardinalityString())

        col = extract_column_info(col_obj)

        assert col is not None
        assert col.type == "LowCardinality(String)"


class TestMetaBasedClickHouse:
    """Tests for CH extraction via class Meta(CHTableMeta): no __table_args__ fallback."""

    def test_extract_table_from_model_uses_meta(self, monkeypatch):
        from sqlalchemy import Column, Integer, String, MetaData, Table
        from dbwarden.schema._base import DBWardenMeta

        monkeypatch.setattr(model_discovery.type_mapping, "_get_backend_name", lambda db_name=None: "clickhouse")

        class Event:
            __tablename__ = "events"
            __table__ = Table(
                "events",
                MetaData(),
                Column("region", String, primary_key=True),
            )

        dw_meta = DBWardenMeta()
        dw_meta.backend_table = {
            "ch_engine": "SummingMergeTree",
            "ch_order_by": ["region"],
        }
        Event.__dbwarden_meta__ = dw_meta
        Event.__dbwarden_meta_applied__ = True

        table = extract_table_from_model(Event, db_name="primary")

        assert table is not None
        assert table.clickhouse_options.get("ch_engine") == "SummingMergeTree"
        assert table.clickhouse_options.get("ch_order_by") == ["region"]

    def test_extract_table_from_model_detects_materialized_view(self, monkeypatch):
        from sqlalchemy import Column, MetaData, String, Table
        from dbwarden.schema._base import DBWardenMeta

        monkeypatch.setattr(model_discovery.type_mapping, "_get_backend_name", lambda db_name=None: "clickhouse")

        class EventView:
            __tablename__ = "mv_name"
            __table__ = Table(
                "mv_name",
                MetaData(),
                Column("region", String, primary_key=True),
            )

        dw_meta = DBWardenMeta()
        dw_meta.backend_table = {
            "ch_select_statement": "SELECT region FROM source_table",
        }
        EventView.__dbwarden_meta__ = dw_meta
        EventView.__dbwarden_meta_applied__ = True

        table = extract_table_from_model(EventView, db_name="primary")

        assert table is not None
        assert table.object_type == "materialized_view"

    def test_extract_table_from_model_rejects_invalid_primary_key_prefix(self, monkeypatch):
        from sqlalchemy import Column, MetaData, String, Table
        from dbwarden.schema._base import DBWardenMeta

        monkeypatch.setattr(model_discovery.type_mapping, "_get_backend_name", lambda db_name=None: "clickhouse")

        class Event:
            __tablename__ = "events"
            __table__ = Table(
                "events",
                MetaData(),
                Column("region", String),
                Column("event_time", String),
            )

        dw_meta = DBWardenMeta()
        dw_meta.backend_table = {
            "ch_order_by": ["region", "event_time"],
            "ch_primary_key": ["event_time"],
        }
        Event.__dbwarden_meta__ = dw_meta
        Event.__dbwarden_meta_applied__ = True

        table = extract_table_from_model(Event, db_name="primary")

        assert table is None


class TestClickHouseTypeMapping:
    def test_clickhouse_type_extraction_from_info(self, monkeypatch):
        """CH type hints in column.info are extracted correctly."""
        from sqlalchemy import Column, String
        monkeypatch.setattr(model_discovery.type_mapping, "_get_backend_name", lambda db_name=None: "clickhouse")
        col_obj = Column(
            "payload",
            String,
            info={"clickhouse_type": "LowCardinality(String)"},
        )
        col = extract_column_info(col_obj)
        assert col.type == "LowCardinality(String)"

    def test_extract_column_ch_meta_info(self, monkeypatch):
        from sqlalchemy import Column, String
        monkeypatch.setattr(model_discovery.type_mapping, "_get_backend_name", lambda db_name=None: "clickhouse")
        col_obj = Column(
            "payload",
            String,
            info={
                "ch_codec": "ZSTD(3)",
                "ch_type": "LowCardinality(String)",
            },
        )
        col = extract_column_info(col_obj)
        assert col.ch_meta.get("ch_codec") == "ZSTD(3)"
        assert col.ch_meta.get("ch_type") == "LowCardinality(String)"

    def test_model_column_ch_type_hints(self):
        col = ModelColumn(
            name="payload",
            type="String",
            nullable=False,
            primary_key=False,
            unique=False,
            default=None,
            foreign_key=None,
        )
        col.ch_meta = {"ch_codec": "ZSTD(3)"}
        d = col.to_dict()
        assert d.get("ch_meta") == {"ch_codec": "ZSTD(3)"}

    def test_model_column_to_dict_with_ch_meta(self):
        col = ModelColumn(
            name="payload",
            type="String",
            nullable=False,
            primary_key=False,
            unique=False,
            default=None,
            foreign_key=None,
        )
        col.ch_meta = {"ch_codec": "ZSTD(3)", "ch_default": "now()"}
        d = col.to_dict()
        assert d["ch_meta"] == {"ch_codec": "ZSTD(3)", "ch_default": "now()"}

    def test_engine_spec_from_engine_string(self):
        from dbwarden.databases.clickhouse.engine import ChEngineSpec
        spec = ChEngineSpec.from_engine_string("ReplicatedMergeTree('/clickhouse/tables/{shard}', '{replica}')")
        assert spec.name == "ReplicatedMergeTree"
        assert len(spec.args) == 0  # zk_path and replica extracted separately
        assert spec.zookeeper_path == "/clickhouse/tables/{shard}"
        assert spec.replica_name == "{replica}"

    def test_engine_spec_from_engine_string_no_params(self):
        from dbwarden.databases.clickhouse.engine import ChEngineSpec
        spec = ChEngineSpec.from_engine_string("MergeTree")
        assert spec.name == "MergeTree"
        assert spec.args == ()

    def test_engine_spec_from_engine_string_with_args(self):
        from dbwarden.databases.clickhouse.engine import ChEngineSpec
        spec = ChEngineSpec.from_engine_string("Distributed('my_cluster', 'default', 'hits')")
        assert spec.name == "Distributed"
        assert spec.args == ("'my_cluster'", "'default'", "'hits'")


class TestChTypeMapper:
    def test_map_sa_integer_types(self):
        from dbwarden.engine.model_discovery import _render_ch_type_from_sa
        from sqlalchemy import Integer, BigInteger, SmallInteger
        assert _render_ch_type_from_sa(Integer(), "INTEGER") == "Int32"
        assert _render_ch_type_from_sa(BigInteger(), "BIGINT") == "Int64"
        assert _render_ch_type_from_sa(SmallInteger(), "SMALLINT") == "Int16"

    def test_map_sa_string_types(self):
        from dbwarden.engine.model_discovery import _render_ch_type_from_sa
        from sqlalchemy import String, Text, VARCHAR
        assert _render_ch_type_from_sa(String(255), "VARCHAR(255)") == "String"
        assert _render_ch_type_from_sa(Text(), "TEXT") == "String"

    def test_map_sa_float_types(self):
        from dbwarden.engine.model_discovery import _render_ch_type_from_sa
        from sqlalchemy import Float, REAL
        assert _render_ch_type_from_sa(Float(precision=24), "FLOAT(24)") == "Float32"
        assert _render_ch_type_from_sa(Float(precision=53), "FLOAT(53)") == "Float64"
        # REAL has no precision attr → defaults to Float64
        assert _render_ch_type_from_sa(Float(), "FLOAT") == "Float64"

    def test_map_sa_numeric_to_decimal(self):
        from dbwarden.engine.model_discovery import _render_ch_type_from_sa
        from sqlalchemy import Numeric
        assert _render_ch_type_from_sa(Numeric(10, 2), "NUMERIC(10, 2)") == "Decimal(10, 2)"
        assert _render_ch_type_from_sa(Numeric(), "NUMERIC") == "Decimal(38, 0)"

    def test_map_sa_boolean(self):
        from dbwarden.engine.model_discovery import _render_ch_type_from_sa
        from sqlalchemy import Boolean
        assert _render_ch_type_from_sa(Boolean(), "BOOLEAN") == "Bool"

    def test_map_sa_date_time(self):
        from dbwarden.engine.model_discovery import _render_ch_type_from_sa
        from sqlalchemy import DateTime, Date
        assert _render_ch_type_from_sa(Date(), "DATE") == "Date"
        assert _render_ch_type_from_sa(DateTime(), "DATETIME") == "DateTime"
        # Timezone-aware → DateTime64(3)
        assert _render_ch_type_from_sa(DateTime(timezone=True), "DATETIME") == "DateTime64(3)"

    def test_map_sa_array(self):
        from dbwarden.engine.model_discovery import _render_ch_type_from_sa
        from sqlalchemy import ARRAY, String
        arr = ARRAY(String(255))
        result = _render_ch_type_from_sa(arr, "VARCHAR(255)[]")
        assert result == "Array(String)"

    def test_map_sa_binary(self):
        from dbwarden.engine.model_discovery import _render_ch_type_from_sa
        from sqlalchemy import LargeBinary
        assert _render_ch_type_from_sa(LargeBinary(), "BLOB") == "String"

    def test_map_sa_uuid(self):
        from dbwarden.engine.model_discovery import _render_ch_type_from_sa
        from sqlalchemy.dialects.postgresql import UUID
        assert _render_ch_type_from_sa(UUID(), "UUID") == "UUID"

    def test_map_sa_json(self):
        from dbwarden.engine.model_discovery import _render_ch_type_from_sa
        from sqlalchemy import JSON
        assert _render_ch_type_from_sa(JSON(), "JSON") == "JSON"

    def test_map_sa_enum_small(self):
        from dbwarden.engine.model_discovery import _render_ch_type_from_sa
        import enum
        from sqlalchemy import Enum as SAEnum
        small_enum = SAEnum("red", "green", "blue", name="color")
        result = _render_ch_type_from_sa(small_enum, "ENUM")
        assert result.startswith("Enum8(")
        assert "'red'" in result
        assert "'green'" in result

    def test_map_sa_enum_large(self):
        from dbwarden.engine.model_discovery import _render_ch_type_from_sa
        from sqlalchemy import Enum as SAEnum
        values = [str(i) for i in range(200)]
        large_enum = SAEnum(*values, name="big")
        result = _render_ch_type_from_sa(large_enum, "ENUM")
        assert result.startswith("Enum16(")

    def test_map_sa_time_and_interval_fallback(self):
        from dbwarden.engine.model_discovery import _render_ch_type_from_sa
        from sqlalchemy import Time, Interval
        assert _render_ch_type_from_sa(Time(), "TIME") == "String"
        assert _render_ch_type_from_sa(Interval(), "INTERVAL") == "String"


class TestChTypeMapperWithInfo:
    def test_map_sa_type_with_low_cardinality(self, monkeypatch):
        from dbwarden.engine.model_discovery import _map_sa_type_to_clickhouse
        from sqlalchemy import Column, String
        monkeypatch.setattr("dbwarden.engine.model_discovery.type_mapping._get_backend_name", lambda db_name=None: "clickhouse")
        col = Column("name", String(255), info={"ch_low_cardinality": True})
        assert _map_sa_type_to_clickhouse(col) == "LowCardinality(String)"

    def test_map_sa_type_with_nullable(self, monkeypatch):
        from dbwarden.engine.model_discovery import _map_sa_type_to_clickhouse
        from sqlalchemy import Column, Integer
        monkeypatch.setattr("dbwarden.engine.model_discovery.type_mapping._get_backend_name", lambda db_name=None: "clickhouse")
        col = Column("age", Integer, info={"ch_nullable": True})
        assert _map_sa_type_to_clickhouse(col) == "Nullable(Int32)"

    def test_map_sa_type_with_both_wrappers(self, monkeypatch):
        from dbwarden.engine.model_discovery import _map_sa_type_to_clickhouse
        from sqlalchemy import Column, String
        monkeypatch.setattr("dbwarden.engine.model_discovery.type_mapping._get_backend_name", lambda db_name=None: "clickhouse")
        col = Column("name", String(255), info={"ch_low_cardinality": True, "ch_nullable": True})
        result = _map_sa_type_to_clickhouse(col)
        # Nullable must be applied before LowCardinality: LowCardinality(Nullable(...))
        assert result == "LowCardinality(Nullable(String))"
class TestPGViewMetaExtraction:
    def test_extract_table_from_model_with_pg_view_meta(self, monkeypatch):
        from sqlalchemy import Column, Integer, MetaData, String, Table
        from dbwarden.databases.pgsql import PgViewSpec
        from dbwarden.schema._base import DBWardenMeta

        monkeypatch.setattr(model_discovery.type_mapping, "_get_backend_name", lambda db_name=None: "postgresql")

        class ActiveUsers:
            __tablename__ = "active_users"
            __table__ = Table(
                "active_users", MetaData(),
                Column("id", Integer, primary_key=True),
                Column("name", String(100)),
            )

        dw_meta = DBWardenMeta()
        dw_meta.backend_table = PgViewSpec(
            query="SELECT id, name FROM users WHERE active = true",
            materialized=False,
        )
        ActiveUsers.__dbwarden_meta__ = dw_meta
        ActiveUsers.__dbwarden_meta_applied__ = True

        table = extract_table_from_model(ActiveUsers, db_name="primary")

        assert table is not None
        assert table.object_type == "view"
        assert table.pg_view_definition == "SELECT id, name FROM users WHERE active = true"
        assert table.pg_view_materialized is False

    def test_extract_table_from_model_with_pg_matview_meta(self, monkeypatch):
        from sqlalchemy import Column, Integer, MetaData, String, Table
        from dbwarden.databases.pgsql import PgViewSpec
        from dbwarden.schema._base import DBWardenMeta

        monkeypatch.setattr(model_discovery.type_mapping, "_get_backend_name", lambda db_name=None: "postgresql")

        class UserSummary:
            __tablename__ = "user_summary"
            __table__ = Table(
                "user_summary", MetaData(),
                Column("id", Integer, primary_key=True),
                Column("total", Integer),
            )

        dw_meta = DBWardenMeta()
        dw_meta.backend_table = PgViewSpec(
            query="SELECT id, COUNT(*) as total FROM users GROUP BY id",
            materialized=True,
        )
        UserSummary.__dbwarden_meta__ = dw_meta
        UserSummary.__dbwarden_meta_applied__ = True

        table = extract_table_from_model(UserSummary, db_name="primary")

        assert table is not None
        assert table.object_type == "materialized_view"
        assert table.pg_view_materialized is True
        assert table.pg_view_definition == "SELECT id, COUNT(*) as total FROM users GROUP BY id"

    def test_extract_table_from_model_view_rejects_table_features(self, monkeypatch):
        from sqlalchemy import Column, Integer, MetaData, String, Table
        from dbwarden.databases.pgsql import PgViewSpec, PgIndexSpec
        from dbwarden.schema._base import DBWardenMeta

        monkeypatch.setattr(model_discovery.type_mapping, "_get_backend_name", lambda db_name=None: "postgresql")

        class BadView:
            __tablename__ = "bad_view"
            __table__ = Table(
                "bad_view", MetaData(),
                Column("id", Integer, primary_key=True),
            )

        dw_meta = DBWardenMeta()
        dw_meta.backend_table = PgViewSpec(
            query="SELECT id FROM users",
            materialized=False,
        )
        dw_meta.backend_table.pg_indexes = [PgIndexSpec(columns=["id"], name="bad_idx")]
        BadView.__dbwarden_meta__ = dw_meta
        BadView.__dbwarden_meta_applied__ = True

        table = extract_table_from_model(BadView, db_name="primary")

        assert table is not None
        assert table.object_type == "view"
        assert len(table.indexes) == 0
        assert len(table.foreign_keys) == 0
        assert len(table.uniques) == 0
        assert len(table.checks) == 0
        assert len(table.excludes) == 0


class TestPGSchemaQualifiedSQL:
    def test_generate_create_table_sql_pg_with_schema(self, monkeypatch):
        monkeypatch.setattr(model_discovery.type_mapping, "_get_backend_name", lambda db_name=None: "postgresql")

        columns = [
            ModelColumn("id", "INTEGER", False, True, False, None, None),
        ]
        table = ModelTable(name="users", columns=columns, schema="app")
        sql = generate_create_table_sql(table)

        assert "IF NOT EXISTS app.users" in sql or "CREATE TABLE app.users" in sql

    def test_generate_create_table_sql_pg_schema_with_reserved_name(self, monkeypatch):
        monkeypatch.setattr(model_discovery.type_mapping, "_get_backend_name", lambda db_name=None: "postgresql")

        columns = [
            ModelColumn("id", "INTEGER", False, True, False, None, None),
        ]
        table = ModelTable(name="user", columns=columns, schema="app")
        sql = generate_create_table_sql(table)

        assert '"user"' in sql

    def test_generate_create_table_sql_pg_comment_with_schema(self, monkeypatch):
        monkeypatch.setattr(model_discovery.type_mapping, "_get_backend_name", lambda db_name=None: "postgresql")

        columns = [
            ModelColumn("id", "INTEGER", False, True, False, None, None),
        ]
        table = ModelTable(name="users", columns=columns, schema="app", comment="table comment")
        sql = generate_create_table_sql(table)

        assert "COMMENT ON TABLE app.users" in sql

    def test_generate_add_column_sql_pg_with_schema(self, monkeypatch):
        monkeypatch.setattr(model_discovery.type_mapping, "_get_backend_name", lambda db_name=None: "postgresql")

        column = ModelColumn("email", "VARCHAR(255)", True, False, False, None, None)
        sql = model_discovery.generate_add_column_sql("users", column, db_name="primary", schema="app")

        assert "ALTER TABLE app.users ADD COLUMN email" in sql

    def test_generate_drop_object_sql_pg_view(self, monkeypatch):
        table = ModelTable(
            name="active_users", columns=[ModelColumn("id", "INTEGER", False, True, False, None, None)],
            schema="app", object_type="view",
        )
        sql = generate_drop_object_sql(table)

        assert "DROP VIEW IF EXISTS app.active_users" in sql

    def test_generate_drop_object_sql_pg_matview(self, monkeypatch):
        table = ModelTable(
            name="user_summary", columns=[ModelColumn("id", "INTEGER", False, True, False, None, None)],
            schema="app",
            object_type="materialized_view", pg_view_materialized=True,
        )
        sql = generate_drop_object_sql(table)

        assert "DROP MATERIALIZED VIEW IF EXISTS app.user_summary" in sql

    def test_generate_drop_object_sql_regular_view_not_matview(self, monkeypatch):
        table = ModelTable(
            name="regular_view", columns=[ModelColumn("id", "INTEGER", False, True, False, None, None)],
            schema="app",
            object_type="materialized_view", pg_view_materialized=False,
        )
        sql = generate_drop_object_sql(table)

        assert "DROP VIEW IF EXISTS app.regular_view" in sql

    def test_generate_create_view_sql_schema_qualified(self, monkeypatch):
        monkeypatch.setattr(model_discovery.type_mapping, "_get_backend_name", lambda db_name=None: "postgresql")
        table = ModelTable(
            name="active_users", columns=[ModelColumn("id", "INTEGER", False, True, False, None, None)],
            schema="app",
            object_type="view",
            pg_view_definition="SELECT id, name FROM users WHERE active = true",
        )
        sql = generate_create_table_sql(table)

        assert "app.active_users" in sql
        assert "CREATE OR REPLACE VIEW" in sql

    def test_generate_create_matview_sql_with_schema(self, monkeypatch):
        monkeypatch.setattr(model_discovery.type_mapping, "_get_backend_name", lambda db_name=None: "postgresql")
        table = ModelTable(
            name="user_summary", columns=[ModelColumn("id", "INTEGER", False, True, False, None, None)],
            schema="analytics",
            object_type="materialized_view",
            pg_view_materialized=True,
            pg_view_definition="SELECT id, COUNT(*) as total FROM users GROUP BY id",
        )
        sql = generate_create_table_sql(table)

        assert "analytics.user_summary" in sql
        assert "CREATE MATERIALIZED VIEW" in sql


class TestPGSchemaExtraction:
    def test_extract_table_from_model_with_pg_schema(self, monkeypatch):
        from sqlalchemy import Integer, String
        from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
        from dbwarden.databases.pgsql import PGTableMeta

        monkeypatch.setattr(model_discovery.type_mapping, "_get_backend_name", lambda db_name=None: "postgresql")

        class Base(DeclarativeBase):
            pass

        class User(Base):
            __tablename__ = "users"
            id: Mapped[int] = mapped_column(Integer, primary_key=True)
            email: Mapped[str] = mapped_column(String(255), nullable=False)
            class Meta(PGTableMeta):
                pg_schema = "app"

        table = extract_table_from_model(User, db_name="primary")
        assert table is not None
        assert table.schema == "app"

    def test_extract_table_from_model_without_pg_schema(self, monkeypatch):
        from sqlalchemy import Integer, String
        from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
        from dbwarden.databases.pgsql import PGTableMeta

        monkeypatch.setattr(model_discovery.type_mapping, "_get_backend_name", lambda db_name=None: "postgresql")

        class Base(DeclarativeBase):
            pass

        class User(Base):
            __tablename__ = "users"
            id: Mapped[int] = mapped_column(Integer, primary_key=True)
            email: Mapped[str] = mapped_column(String(255), nullable=False)
            class Meta(PGTableMeta):
                pg_fillfactor = 80

        table = extract_table_from_model(User, db_name="primary")
        assert table is not None
        assert table.schema is None

    def test_extract_table_from_model_with_pg_schema_from_pg_view_meta(self, monkeypatch):
        from sqlalchemy import Column, Integer, MetaData, String, Table
        from dbwarden.databases.pgsql import PgViewSpec
        from dbwarden.schema._base import DBWardenMeta

        monkeypatch.setattr(model_discovery.type_mapping, "_get_backend_name", lambda db_name=None: "postgresql")

        class ActiveUsers:
            __tablename__ = "active_users"
            __table__ = Table(
                "active_users", MetaData(),
                Column("id", Integer, primary_key=True),
                Column("name", String(100)),
            )

        dw_meta = DBWardenMeta()
        dw_meta.backend_table = PgViewSpec(
            query="SELECT id, name FROM users WHERE active = true",
            materialized=False,
            schema="analytics",
        )
        ActiveUsers.__dbwarden_meta__ = dw_meta
        ActiveUsers.__dbwarden_meta_applied__ = True

        table = extract_table_from_model(ActiveUsers, db_name="primary")
        assert table is not None
        assert table.schema == "analytics"
