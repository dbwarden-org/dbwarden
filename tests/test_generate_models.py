import os
import tempfile
from pathlib import Path

from dbwarden.commands.generate_models import _parse_type, _format_default, _generate_table_code


def test_parse_integer():
    assert _parse_type("INTEGER") == "Integer"
    assert _parse_type("BIGINT") == "BigInteger"
    assert _parse_type("SMALLINT") == "SmallInteger"


def test_parse_string():
    assert _parse_type("VARCHAR(100)") == "String(length=100)"
    assert _parse_type("CHAR(10)") == "CHAR(length=10)"
    assert _parse_type("TEXT") == "Text"


def test_parse_enum_values():
    assert _parse_type("ENUM('user','agency')") == "Enum('user','agency')"


def test_parse_boolean():
    assert _parse_type("BOOLEAN") == "Boolean"
    assert _parse_type("TINYINT(1)") == "Boolean"


def test_parse_temporal():
    assert _parse_type("DATE") == "Date"
    assert _parse_type("DATETIME") == "DateTime"
    assert _parse_type("TIMESTAMP") == "DateTime"


def test_parse_numeric():
    result = _parse_type("DECIMAL(10,2)")
    assert "Numeric" in result
    assert "precision=10" in result
    assert "scale=2" in result


def test_parse_clickhouse_nullable():
    assert _parse_type("Nullable(String)") == "String"


def test_parse_postgresql_specific_types():
    assert _parse_type("JSONB", dialect="postgresql") == "JSONB"
    assert _parse_type("UUID", dialect="postgresql") == "UUID(as_uuid=True)"
    assert _parse_type("INTEGER[]", dialect="postgresql") == "ARRAY(Integer)"


def test_parse_clickhouse_types():
    assert _parse_type("Int32", dialect="clickhouse") == "Integer"
    assert _parse_type("UInt64", dialect="clickhouse") == "BigInteger"
    assert _parse_type("Float64", dialect="clickhouse") == "Float"


def test_format_default():
    assert _format_default(None) is None
    assert _format_default("CURRENT_TIMESTAMP") == "func.now()"
    assert _format_default("42") == "42"
    assert _format_default("hello") == "'hello'"


def test_generate_table_code_simple():
    columns = [
        {"name": "id", "type": "INTEGER", "nullable": False, "default": None, "primary_key": True, "unique": False, "foreign_key": None},
        {"name": "name", "type": "VARCHAR(100)", "nullable": True, "default": None, "primary_key": False, "unique": False, "foreign_key": None},
    ]
    code = _generate_table_code("users", columns)
    assert "class Users(Base):" in code
    assert "__tablename__ = 'users'" in code
    assert "primary_key=True" in code
    assert "String(length=100)" in code


def test_generate_table_code_with_mysql_enum_and_char():
    columns = [
        {"name": "recipient_type", "type": "ENUM('user','agency')", "nullable": False, "default": None, "primary_key": False, "unique": False, "foreign_key": None, "dialect": "mysql"},
        {"name": "token", "type": "CHAR(64)", "nullable": False, "default": None, "primary_key": False, "unique": False, "foreign_key": None, "dialect": "mysql"},
    ]
    code = _generate_table_code("notifications_sync_messages", columns)
    assert "Enum('user','agency')" in code
    assert "CHAR(length=64)" in code


def test_parse_mediumtext():
    assert _parse_type("MEDIUMTEXT") == "Text"


def test_generate_table_code_mysql_unsigned():
    columns = [
        {"name": "id", "type": "INTEGER", "nullable": False, "default": None, "primary_key": True, "unique": False, "foreign_key": None, "my_meta": {"my_unsigned": True}},
    ]
    code = _generate_table_code("users", columns)
    assert "class Meta(MyTableMeta):" in code
    assert "class id(MyColumnMeta):" in code
    assert "my = my.field(unsigned=True)" in code


def test_generate_table_code_mysql_charset():
    columns = [
        {"name": "name", "type": "VARCHAR(100)", "nullable": True, "default": None, "primary_key": False, "unique": False, "foreign_key": None, "my_meta": {"my_charset": "utf8", "my_collate": "utf8_unicode_ci"}},
    ]
    code = _generate_table_code("users", columns)
    assert "class Meta(MyTableMeta):" in code
    assert "class name(MyColumnMeta):" in code
    assert "my = my.field(charset='utf8', collate='utf8_unicode_ci')" in code


def test_generate_table_code_mysql_on_update():
    columns = [
        {"name": "updated_at", "type": "DATETIME", "nullable": True, "default": None, "primary_key": False, "unique": False, "foreign_key": None, "my_meta": {"my_on_update": "CURRENT_TIMESTAMP"}},
    ]
    code = _generate_table_code("users", columns)
    assert "class Meta(MyTableMeta):" in code
    assert "class updated_at(MyColumnMeta):" in code
    assert "my = my.field(on_update='CURRENT_TIMESTAMP')" in code


def test_generate_table_code_mysql_all_attrs():
    columns = [
        {"name": "id", "type": "INTEGER", "nullable": False, "default": None, "primary_key": True, "unique": False, "foreign_key": None, "dialect": "mysql", "my_meta": {"my_unsigned": True}},
        {"name": "name", "type": "VARCHAR(100)", "nullable": False, "default": None, "primary_key": False, "unique": False, "foreign_key": None, "dialect": "mysql", "my_meta": {"my_charset": "utf8", "my_collate": "utf8_unicode_ci"}},
        {"name": "updated_at", "type": "DATETIME", "nullable": True, "default": None, "primary_key": False, "unique": False, "foreign_key": None, "dialect": "mysql", "my_meta": {"my_on_update": "CURRENT_TIMESTAMP"}},
    ]
    code = _generate_table_code("users", columns)
    assert "class Meta(MyTableMeta):" in code
    assert "class id(MyColumnMeta):" in code
    assert "my = my.field(unsigned=True)" in code
    assert "class name(MyColumnMeta):" in code
    assert "my = my.field(charset='utf8', collate='utf8_unicode_ci')" in code
    assert "class updated_at(MyColumnMeta):" in code
    assert "my = my.field(on_update='CURRENT_TIMESTAMP')" in code


def test_generate_table_code_with_foreign_key():
    columns = [
        {"name": "id", "type": "INTEGER", "nullable": False, "default": None, "primary_key": True, "unique": False, "foreign_key": None},
        {"name": "user_id", "type": "INTEGER", "nullable": True, "default": None, "primary_key": False, "unique": False, "foreign_key": "users(id)"},
    ]
    code = _generate_table_code("posts", columns)
    assert "ForeignKey('users(id)')" in code


def test_generate_table_code_with_postgresql_meta():
    columns = [
        {
            "name": "email",
            "type": "VARCHAR(255)",
            "nullable": False,
            "default": None,
            "primary_key": False,
            "unique": True,
            "foreign_key": None,
            "comment": "Primary contact email",
            "pg_meta": {"pg_collation": "en_US.UTF-8"},
            "dialect": "postgresql",
        }
    ]
    code = _generate_table_code(
        "users",
        columns,
        object_type="table",
        pg_meta={"comment": "Core user accounts", "pg_indexes": [{"name": "ix_users_email", "columns": ["email"], "unique": True}]},
    )
    assert "class Meta(PGTableMeta):" in code
    assert "comment = 'Core user accounts'" in code
    assert "class email(PGColumnMeta):" in code
    assert "pg = pg.field(collation='en_US.UTF-8')" in code


def test_generate_table_code_with_mysql_meta():
    columns = [
        {
            "name": "id",
            "type": "INT",
            "nullable": False,
            "default": None,
            "primary_key": True,
            "unique": False,
            "foreign_key": None,
            "comment": "Primary key",
            "my_meta": {"my_unsigned": True},
            "dialect": "mysql",
        },
        {
            "name": "updated_at",
            "type": "TIMESTAMP",
            "nullable": False,
            "default": None,
            "primary_key": False,
            "unique": False,
            "foreign_key": None,
            "my_meta": {"my_on_update": "CURRENT_TIMESTAMP"},
            "dialect": "mysql",
        },
    ]
    code = _generate_table_code(
        "users",
        columns,
        object_type="table",
        my_meta={"my_engine": "InnoDB", "my_charset": "utf8mb4", "my_collate": "utf8mb4_unicode_ci"},
    )
    assert "class Meta(MyTableMeta):" in code
    assert "my_engine = 'InnoDB'" in code
    assert "class id(MyColumnMeta):" in code
    assert "my = my.field(unsigned=True)" in code
    assert "my = my.field(on_update='CURRENT_TIMESTAMP')" in code


def test_write_models_postgresql_emits_dialect_imports_and_meta():
    with tempfile.TemporaryDirectory() as tmpdir:
        from dbwarden.commands.generate_models import _write_models

        tables = [
            {
                "name": "users",
                "columns": [
                    {
                        "name": "id",
                        "type": "UUID",
                        "nullable": False,
                        "default": None,
                        "primary_key": True,
                        "unique": False,
                        "foreign_key": None,
                        "autoincrement": False,
                        "dialect": "postgresql",
                    },
                    {
                        "name": "payload",
                        "type": "JSONB",
                        "nullable": True,
                        "default": None,
                        "primary_key": False,
                        "unique": False,
                        "foreign_key": None,
                        "autoincrement": False,
                        "dialect": "postgresql",
                        "comment": "Payload",
                        "pg_meta": {"pg_collation": "en_US.UTF-8"},
                    },
                ],
                "ch_options": None,
                "object_type": "table",
                "dialect": "postgresql",
                "pg_meta": {"comment": "Users table"},
            }
        ]
        _write_models(tmpdir, tables, single_file=True)
        content = Path(tmpdir, "models.py").read_text()
        assert "from sqlalchemy.dialects.postgresql import JSONB, UUID" in content
        assert "class Meta(PGTableMeta):" in content
        assert "comment = 'Users table'" in content
        assert "from dbwarden.databases.pgsql import pg" in content
        assert 'pg = pg.field(collation=' in content


def test_write_models_mysql_emits_meta_imports():
    with tempfile.TemporaryDirectory() as tmpdir:
        from dbwarden.commands.generate_models import _write_models

        tables = [
            {
                "name": "users",
                "columns": [
                    {
                        "name": "id",
                        "type": "INT",
                        "nullable": False,
                        "default": None,
                        "primary_key": True,
                        "unique": False,
                        "foreign_key": None,
                        "autoincrement": True,
                        "dialect": "mysql",
                        "my_meta": {"my_unsigned": True},
                    },
                ],
                "ch_options": None,
                "object_type": "table",
                "dialect": "mysql",
                "my_meta": {"my_engine": "InnoDB", "my_charset": "utf8mb4"},
            }
        ]
        _write_models(tmpdir, tables, single_file=True)
        content = Path(tmpdir, "models.py").read_text()
        assert "from dbwarden.databases.mysql import my, MyColumnMeta, MyTableMeta" in content
        assert "from dbwarden.databases.mysql import my" in content
        assert "class Meta(MyTableMeta):" in content


def test_write_models_single_file():
    with tempfile.TemporaryDirectory() as tmpdir:
        old_cwd = os.getcwd()
        os.chdir(tmpdir)
        try:
            from dbwarden.config import set_dev_mode
            from dbwarden.commands.generate_models import _write_models

            set_dev_mode(False)
            tables = [
                {
                    "name": "users",
                    "columns": [
                        {"name": "id", "type": "INTEGER", "nullable": False, "default": None, "primary_key": True, "unique": False, "foreign_key": None, "autoincrement": True},
                        {"name": "email", "type": "VARCHAR(255)", "nullable": True, "default": None, "primary_key": False, "unique": True, "foreign_key": None},
                    ],
                    "ch_options": None,
                    "object_type": "table",
                }
            ]
            _write_models(tmpdir, tables, single_file=True)
            model_path = Path(tmpdir, "models.py")
            assert model_path.exists()
            content = model_path.read_text()
            assert "class Users(Base):" in content
            assert "__tablename__ = 'users'" in content
        finally:
            os.chdir(old_cwd)


def test_write_models_per_table():
    with tempfile.TemporaryDirectory() as tmpdir:
        old_cwd = os.getcwd()
        os.chdir(tmpdir)
        try:
            from dbwarden.config import set_dev_mode
            from dbwarden.commands.generate_models import _write_models

            set_dev_mode(False)
            tables = [
                {
                    "name": "users",
                    "columns": [
                        {"name": "id", "type": "INTEGER", "nullable": False, "default": None, "primary_key": True, "unique": False, "foreign_key": None, "autoincrement": True},
                    ],
                    "ch_options": None,
                    "object_type": "table",
                },
                {
                    "name": "posts",
                    "columns": [
                        {"name": "id", "type": "INTEGER", "nullable": False, "default": None, "primary_key": True, "unique": False, "foreign_key": None, "autoincrement": True},
                    ],
                    "ch_options": None,
                    "object_type": "table",
                },
            ]
            _write_models(tmpdir, tables, single_file=False)
            assert Path(tmpdir, "users.py").exists()
            assert Path(tmpdir, "posts.py").exists()
            users_content = Path(tmpdir, "users.py").read_text()
            assert "Base = declarative_base()" in users_content
        finally:
            os.chdir(old_cwd)


def test_generate_models_with_db_connection():
    with tempfile.TemporaryDirectory() as tmpdir:
        old_cwd = os.getcwd()
        os.chdir(tmpdir)
        try:
            from dbwarden.config import set_dev_mode
            from dbwarden.connection.connection import get_db_connection
            from dbwarden.commands.generate_models import generate_models_cmd
            from sqlalchemy import text

            set_dev_mode(False)
            db_path = f"sqlite:///./{Path(tmpdir).name}.db"
            Path(tmpdir, "dbwarden.py").write_text(
                "from dbwarden import database_config\n\n"
                f"database_config(database_name='primary', default=True, database_type='sqlite', database_url_sync='{db_path}')\n",
                encoding="utf-8",
            )

            with get_db_connection(None) as conn:
                conn.execute(text("CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT NOT NULL, email TEXT UNIQUE)"))
                conn.execute(text("CREATE TABLE posts (id INTEGER PRIMARY KEY, user_id INTEGER, title TEXT, FOREIGN KEY (user_id) REFERENCES users(id))"))

            generate_models_cmd(
                output=tmpdir,
                tables=None,
                exclude_tables=None,
                clickhouse_engines=False,
                relationships=False,
                dialect=None,
                single_file=True,
                database=None,
            )

            model_file = Path(tmpdir, "models.py")
            assert model_file.exists()
            content = model_file.read_text()
            assert "class Users(Base):" in content or "class User(Base):" in content
            assert "class Posts(Base):" in content
            assert "ForeignKey" in content
        finally:
            os.chdir(old_cwd)


def test_generate_models_with_tables_filter():
    with tempfile.TemporaryDirectory() as tmpdir:
        old_cwd = os.getcwd()
        os.chdir(tmpdir)
        try:
            from dbwarden.config import set_dev_mode
            from dbwarden.connection.connection import get_db_connection
            from dbwarden.commands.generate_models import generate_models_cmd
            from sqlalchemy import text

            set_dev_mode(False)
            db_path = f"sqlite:///./{Path(tmpdir).name}.db"
            Path(tmpdir, "dbwarden.py").write_text(
                "from dbwarden import database_config\n\n"
                f"database_config(database_name='primary', default=True, database_type='sqlite', database_url_sync='{db_path}')\n",
                encoding="utf-8",
            )

            with get_db_connection(None) as conn:
                conn.execute(text("CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT)"))
                conn.execute(text("CREATE TABLE posts (id INTEGER PRIMARY KEY, title TEXT)"))

            generate_models_cmd(
                output=tmpdir,
                tables="users",
                exclude_tables=None,
                clickhouse_engines=False,
                relationships=False,
                dialect=None,
                single_file=True,
                database=None,
            )

            content = Path(tmpdir, "models.py").read_text()
            assert "class Users(Base):" in content or "class User(Base):" in content
            assert "posts" not in content.lower() or "Posts" not in content
        finally:
            os.chdir(old_cwd)


class TestClickHouseGenerateModels:
    def test_render_ch_meta_simple(self):
        from dbwarden.commands.generate_models import _render_ch_meta
        meta_lines = _render_ch_meta(
            columns=[],
            options={"ch_engine": "MergeTree", "ch_order_by": ["id"]},
            object_type="table",
        )
        assert any("ch_engine = 'MergeTree'" in line for line in meta_lines)
        assert any("ch_order_by = ['id']" in line for line in meta_lines)

    def test_render_ch_meta_with_ch_settings(self):
        from dbwarden.commands.generate_models import _render_ch_meta
        meta_lines = _render_ch_meta(
            columns=[],
            options={"ch_engine": "MergeTree", "ch_order_by": ["id"], "ch_settings": {"allow_nullable_key": 1}},
            object_type="table",
        )
        assert any("ch_settings = {'allow_nullable_key': 1}" in line for line in meta_lines)

    def test_render_ch_meta_with_engine_spec(self):
        from dbwarden.commands.generate_models import _render_ch_meta
        from dbwarden.databases.clickhouse.engine import ChEngineSpec
        engine = ChEngineSpec(name="ReplicatedMergeTree", args=("/zk/path", "{replica}"))
        meta_lines = _render_ch_meta(
            columns=[],
            options={"ch_engine_raw": engine, "ch_order_by": ["id"]},
            object_type="table",
        )
        assert any("ch_engine = ChEngineSpec(" in line for line in meta_lines)
        assert any("ReplicatedMergeTree" in line for line in meta_lines)

    def test_generate_table_code_clickhouse_engine(self):
        columns = [
            {"name": "id", "type": "UInt64", "nullable": False, "default": None, "primary_key": True, "unique": False, "foreign_key": None},
            {"name": "name", "type": "String", "nullable": True, "default": None, "primary_key": False, "unique": False, "foreign_key": None},
        ]
        code = _generate_table_code(
            "events",
            columns,
            object_type="table",
            ch_options={
                "ch_engine": "MergeTree",
                "ch_order_by": ["id"],
            },
        )
        assert "class Events(Base):" in code
        assert "__tablename__ = 'events'" in code
        assert "class Meta(CHTableMeta):" in code
        assert "ch_engine = 'MergeTree'" in code

    def test_generate_table_code_clickhouse_column_ch_meta(self):
        columns = [
            {"name": "id", "type": "UInt64", "nullable": False, "default": None, "primary_key": True, "unique": False, "foreign_key": None},
            {
                "name": "payload",
                "type": "String",
                "nullable": True,
                "default": None,
                "primary_key": False,
                "unique": False,
                "foreign_key": None,
                "ch_meta": {"ch_codec": "ZSTD(3)", "ch_type": "String"},
            },
        ]
        code = _generate_table_code(
            "events",
            columns,
            object_type="table",
            ch_options={
                "ch_engine": "MergeTree",
                "ch_order_by": ["id"],
            },
        )
        assert "ch = ch.field(codec='ZSTD(3)')" in code

    def test_parse_clickhouse_nullable_type(self):
        assert _parse_type("Nullable(String)") == "String"
        assert _parse_type("Nullable(Int32)") == "Integer"

    def test_generate_table_code_clickhouse_high_precision_datetime(self):
        columns = [
            {"name": "id", "type": "UInt64", "nullable": False, "default": None, "primary_key": True, "unique": False, "foreign_key": None},
            {"name": "ts", "type": "DateTime64(3)", "nullable": False, "default": None, "primary_key": False, "unique": False, "foreign_key": None},
        ]
        code = _generate_table_code(
            "events",
            columns,
            object_type="table",
        )
        assert "DateTime" in code

    def test_parse_clickhouse_types_extra(self):
        assert _parse_type("Int8", dialect="clickhouse") == "SmallInteger"
        assert _parse_type("Int16", dialect="clickhouse") == "SmallInteger"
        assert _parse_type("Int64", dialect="clickhouse") == "BigInteger"
        assert _parse_type("Float32", dialect="clickhouse") == "Float"
        assert _parse_type("String", dialect="clickhouse") == "String"
        assert _parse_type("Date", dialect="clickhouse") == "Date"
        assert _parse_type("DateTime", dialect="clickhouse") == "DateTime"

    def test_write_models_clickhouse_emits_meta_imports(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            from dbwarden.commands.generate_models import _write_models

            tables = [
                {
                    "name": "events",
                    "columns": [
                        {"name": "id", "type": "UInt64", "nullable": False, "default": None, "primary_key": True, "unique": False, "foreign_key": None, "autoincrement": False},
                    ],
                    "ch_options": {"ch_engine": "MergeTree", "ch_order_by": ["id"]},
                    "object_type": "table",
                }
            ]
            _write_models(tmpdir, tables, single_file=True)
            content = Path(tmpdir, "models.py").read_text()
            assert "CHColumnMeta" in content
            assert "CHTableMeta" in content
            assert "from dbwarden.databases.clickhouse import CHColumnMeta, CHTableMeta" in content

    def test_write_models_clickhouse_ch_engine_spec_import(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            from dbwarden.commands.generate_models import _write_models
            from dbwarden.databases.clickhouse.engine import ChEngineSpec

            tables = [
                {
                    "name": "events",
                    "columns": [
                        {"name": "id", "type": "UInt64", "nullable": False, "default": None, "primary_key": True, "unique": False, "foreign_key": None, "autoincrement": False},
                    ],
                    "ch_options": {"ch_engine_raw": ChEngineSpec("MergeTree"), "ch_order_by": ["id"]},
                    "object_type": "table",
                }
            ]
            _write_models(tmpdir, tables, single_file=True)
            content = Path(tmpdir, "models.py").read_text()
            assert "ChEngineSpec" in content
            assert "from dbwarden.databases.clickhouse import CHColumnMeta, CHTableMeta, ChEngineSpec" in content

    def test_generate_table_code_ch_materialized_view(self):
        columns = [
            {"name": "group_col", "type": "String", "nullable": False, "default": None, "primary_key": False, "unique": False, "foreign_key": None},
            {"name": "total", "type": "UInt64", "nullable": False, "default": None, "primary_key": False, "unique": False, "foreign_key": None},
        ]
        code = _generate_table_code(
            "mv_name",
            columns,
            object_type="materialized_view",
            ch_options={
                "ch_object_type": "materialized_view",
                "ch_select_statement": "SELECT group_col, count() AS total FROM source GROUP BY group_col",
                "ch_engine": "SummingMergeTree",
                "ch_order_by": ["group_col"],
            },
        )
        assert "class MvName(MaterializedView):" in code
        assert "class Meta(CHViewMeta):" in code
        assert "ch = materialized_view(" in code
        assert "select='SELECT group_col, count() AS total FROM source GROUP BY group_col'" in code
        assert "engine=ChEngineSpec('SummingMergeTree')" in code
        assert "order_by=['group_col']" in code
        assert "__tablename__ = 'mv_name'" in code

    def test_generate_table_code_ch_materialized_view_to_target(self):
        # Mode B: MV writes into an existing target table via TO [db.]table.
        columns = [
            {"name": "group_col", "type": "String", "nullable": False, "default": None, "primary_key": False, "unique": False, "foreign_key": None},
            {"name": "total", "type": "UInt64", "nullable": False, "default": None, "primary_key": False, "unique": False, "foreign_key": None},
        ]
        code = _generate_table_code(
            "mv_to_target",
            columns,
            object_type="materialized_view",
            ch_options={
                "ch_object_type": "materialized_view",
                "ch_select_statement": "SELECT group_col, count() AS total FROM source GROUP BY group_col",
                "ch_to_table": "mv_target",
            },
        )
        assert "class MvToTarget(MaterializedView):" in code
        assert "class Meta(CHViewMeta):" in code
        assert "ch = materialized_view(" in code
        assert "select='SELECT group_col, count() AS total FROM source GROUP BY group_col'" in code
        assert "to='mv_target'" in code
        # Engine/order_by/partition_by must not be emitted when a TO target exists.
        assert "engine=" not in code
        assert "order_by=" not in code
        assert "partition_by=" not in code

    def test_write_models_clickhouse_materialized_view_imports_single_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            from dbwarden.commands.generate_models import _write_models

            tables = [
                {
                    "name": "mv_events",
                    "columns": [
                        {"name": "group_col", "type": "String", "nullable": False, "default": None, "primary_key": False, "unique": False, "foreign_key": None},
                    ],
                    "ch_options": {
                        "ch_object_type": "materialized_view",
                        "ch_select_statement": "SELECT group_col FROM source GROUP BY group_col",
                        "ch_engine": "MergeTree",
                        "ch_order_by": ["group_col"],
                    },
                    "object_type": "materialized_view",
                }
            ]
            _write_models(tmpdir, tables, single_file=True)
            content = Path(tmpdir, "models.py").read_text()
            assert "from dbwarden.databases.clickhouse import CHColumnMeta, CHViewMeta, ChEngineSpec, MaterializedView, materialized_view" in content
            assert "CHTableMeta" not in content

    def test_write_models_clickhouse_materialized_view_imports_per_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            from dbwarden.commands.generate_models import _write_models

            tables = [
                {
                    "name": "mv_events",
                    "columns": [
                        {"name": "group_col", "type": "String", "nullable": False, "default": None, "primary_key": False, "unique": False, "foreign_key": None},
                    ],
                    "ch_options": {
                        "ch_object_type": "materialized_view",
                        "ch_select_statement": "SELECT group_col FROM source GROUP BY group_col",
                        "ch_engine": "MergeTree",
                        "ch_order_by": ["group_col"],
                    },
                    "object_type": "materialized_view",
                }
            ]
            _write_models(tmpdir, tables, single_file=False)
            content = Path(tmpdir, "mv_events.py").read_text()
            assert "from dbwarden.databases.clickhouse import CHColumnMeta, CHViewMeta, ChEngineSpec, MaterializedView, materialized_view" in content
            assert "CHTableMeta" not in content


class TestPkFallbackInference:
    def test_unique_not_null_becomes_pk(self):
        from dbwarden.commands.generate_models import _infer_primary_key
        result = _infer_primary_key(
            set(),
            [{"column_names": ["id_novedad"]}],
            [{"name": "id_novedad", "nullable": False},
             {"name": "titulo", "nullable": False}],
        )
        assert result == {"id_novedad"}

    def test_unique_nullable_becomes_pk(self):
        from dbwarden.commands.generate_models import _infer_primary_key
        result = _infer_primary_key(
            set(),
            [{"column_names": ["slug"]}],
            [{"name": "slug", "nullable": True},
             {"name": "name", "nullable": False}],
        )
        assert result == {"slug"}

    def test_id_column_fallback(self):
        from dbwarden.commands.generate_models import _infer_primary_key
        result = _infer_primary_key(
            set(),
            [],
            [{"name": "id", "nullable": True},
             {"name": "name", "nullable": False}],
        )
        assert result == {"id"}

    def test_suffix_id_fallback(self):
        from dbwarden.commands.generate_models import _infer_primary_key
        result = _infer_primary_key(
            set(),
            [],
            [{"name": "property_id", "nullable": False},
             {"name": "name", "nullable": False}],
        )
        assert result == {"property_id"}

    def test_first_non_nullable_column_fallback(self):
        from dbwarden.commands.generate_models import _infer_primary_key
        result = _infer_primary_key(
            set(),
            [],
            [{"name": "a", "nullable": True},
             {"name": "b", "nullable": False},
             {"name": "c", "nullable": True}],
        )
        assert result == {"b"}

    def test_first_column_last_resort(self):
        from dbwarden.commands.generate_models import _infer_primary_key
        result = _infer_primary_key(
            set(),
            [],
            [{"name": "numero_raw", "nullable": True}],
        )
        assert result == {"numero_raw"}

    def test_multi_column_unique_becomes_composite_pk(self):
        from dbwarden.commands.generate_models import _infer_primary_key
        result = _infer_primary_key(
            set(),
            [{"column_names": ["ip_from", "ip_to"]}],
            [{"name": "ip_from", "nullable": True},
             {"name": "ip_to", "nullable": True},
             {"name": "country_code", "nullable": True}],
        )
        assert result == {"ip_from", "ip_to"}

    def test_existing_pk_is_unchanged(self):
        from dbwarden.commands.generate_models import _infer_primary_key
        result = _infer_primary_key(
            {"id"},
            [{"column_names": ["slug"]}],
            [{"name": "id", "nullable": False},
             {"name": "slug", "nullable": False}],
        )
        assert result == {"id"}

    def test_prefers_unique_not_null_over_id(self):
        from dbwarden.commands.generate_models import _infer_primary_key
        result = _infer_primary_key(
            set(),
            [{"column_names": ["code"]}],
            [{"name": "id", "nullable": True},
             {"name": "code", "nullable": False}],
        )
        assert result == {"code"}

    def test_empty_returns_empty(self):
        from dbwarden.commands.generate_models import _infer_primary_key
        result = _infer_primary_key(set(), [], [])
        assert result == set()


class TestGeneratedImportsAreComplete:
    """A generated model file has to import everything it references.

    Reverse engineering a column whose default is `now()` writes
    `default=func.now()`, but `func` was filtered back out of the import line.
    The artifact then raised `NameError: name 'func' is not defined` the moment
    dbwarden loaded it - so `generate-models` produced a file that its own
    `diff` could not read.
    """

    @staticmethod
    def _imports(columns):
        from dbwarden.commands.generate_models.imports import (
            _render_imports,
            _resolve_imports,
        )

        return _render_imports(_resolve_imports(columns, has_relationships=False))

    def test_func_survives_for_a_now_default(self):
        imports = self._imports(
            [{"name": "created_at", "type": "timestamp", "default": "func.now()"}],
        )
        assert "func" in imports

    def test_func_survives_for_current_timestamp(self):
        imports = self._imports(
            [{"name": "created_at", "type": "timestamp", "default": "CURRENT_TIMESTAMP"}],
        )
        assert "func" in imports

    def test_column_is_always_imported_once(self):
        imports = self._imports([{"name": "id", "type": "integer"}])
        assert "Column" in imports

    def test_no_func_import_without_a_generated_default(self):
        imports = self._imports([{"name": "code", "type": "varchar(32)"}])
        assert "func" not in imports
