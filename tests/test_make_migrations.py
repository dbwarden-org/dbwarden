import os
import tempfile
import json
from pathlib import Path

from dbwarden.commands.make_migrations import (
    RenameIntent,
    _check_recreate_rename_conflict,
    _resolve_clickhouse_recreate_ops,
    _parse_rename_flags,
    _parse_rename_table_flags,
    _validate_table_rename_intents,
    _format_table_rename_warning,
    build_migration_plan,
    generate_migration_sql,
    make_migrations_cmd,
)
from dbwarden.config import set_dev_mode
from dbwarden.engine.model_discovery import ModelColumn, ModelTable
from dbwarden.engine.migration_name import Change


def _write_migration(directory: str, name: str, content: str) -> None:
    with open(os.path.join(directory, name), "w", encoding="utf-8") as f:
        f.write(content)


def test_parse_rename_flags_with_dots_in_column_name():
    result = _parse_rename_flags(["users.json_field:json_data"])
    assert len(result) == 1
    assert result[0] == RenameIntent("users", "json_field", "json_data")


def test_parse_rename_table_flags_with_dashes():
    result = _parse_rename_table_flags(["my-table:our-table"])
    assert len(result) == 1
    assert result[0] == {"old_table": "my-table", "new_table": "our-table"}


def test_format_table_rename_warning_zero_percent():
    msg = _format_table_rename_warning([("a", "b", 0.0)])
    assert "0%" in msg


def test_format_table_rename_warning_multiple():
    msg = _format_table_rename_warning([("a", "b", 0.9), ("c", "d", 0.75)])
    assert "a" in msg
    assert "b" in msg
    assert "c" in msg
    assert "d" in msg


def test_generate_migration_sql_with_table_rename_and_column_rename():
    set_dev_mode(True)
    with tempfile.TemporaryDirectory() as tmpdir:
            _write_migration(
                tmpdir,
                "0001_create.sql",
                """-- upgrade

CREATE TABLE IF NOT EXISTS users (
    id INTEGER NOT NULL PRIMARY KEY,
    name VARCHAR NOT NULL
)

-- rollback

DROP TABLE users
""",
            )
            accounts = ModelTable(
                name="accounts",
                columns=[
                    ModelColumn("id", "INTEGER", False, True, False, None, None),
                    ModelColumn("full_name", "VARCHAR", True, False, False, None, None),
                ],
            )
            upgrade_sql, rollback_sql, changes = generate_migration_sql(
                [accounts],
                migrations_dir=tmpdir,
                confirmed_table_intents={("users", "accounts")},
                table_resolved_from_map={("users", "accounts"): "rename_flag"},
            )
            assert any(c.operation == "rename_table" for c in changes)
            assert "ALTER TABLE users RENAME TO accounts;" in upgrade_sql


def test_generate_migration_sql_table_rename_no_snapshot():
    table = ModelTable(
        name="accounts",
        columns=[
            ModelColumn("id", "INTEGER", False, True, False, None, None),
        ],
    )
    upgrade_sql, rollback_sql, changes = generate_migration_sql(
        [table],
        confirmed_table_intents={("users", "accounts")},
        table_resolved_from_map={("users", "accounts"): "prompt"},
    )
    assert any(c.operation == "rename_table" for c in changes)


def test_generate_migration_sql_orders_referenced_tables_before_foreign_keys():
    parent = ModelTable(
        name="orders",
        columns=[ModelColumn("id", "INTEGER", False, True, False, None, None)],
    )
    child = ModelTable(
        name="order_items",
        columns=[
            ModelColumn("id", "INTEGER", False, True, False, None, None),
            ModelColumn("order_id", "INTEGER", False, False, False, None, None),
        ],
        foreign_keys=[
            {
                "columns": ["order_id"],
                "referred_table": "orders",
                "referred_columns": ["id"],
            }
        ],
    )

    upgrade_sql, _, _ = generate_migration_sql([child, parent])

    assert upgrade_sql.index("CREATE TABLE IF NOT EXISTS orders") < upgrade_sql.index(
        "CREATE TABLE IF NOT EXISTS order_items"
    )


def test_generate_migration_sql_skips_duplicate_create_from_pending_migration():
    with tempfile.TemporaryDirectory() as tmpdir:
        _write_migration(
            tmpdir,
            "0002_auto_generated.sql",
            """-- upgrade

CREATE TABLE IF NOT EXISTS uploads (
    upload_id UUID NOT NULL PRIMARY KEY,
    user_id VARCHAR NOT NULL,
    filename VARCHAR NOT NULL
)

-- rollback

DROP TABLE uploads
""",
        )

        table = ModelTable(
            name="uploads",
            columns=[
                ModelColumn("upload_id", "UUID", False, True, False, None, None),
                ModelColumn("user_id", "VARCHAR", False, False, False, None, None),
                ModelColumn("filename", "VARCHAR", False, False, False, None, None),
            ],
        )

        upgrade_sql, rollback_sql, _ = generate_migration_sql(
            [table], migrations_dir=tmpdir
        )

        assert upgrade_sql.strip() == ""
        assert rollback_sql.strip() == ""


def test_generate_migration_sql_uses_pending_migrations_as_schema_source():
    with tempfile.TemporaryDirectory() as tmpdir:
        _write_migration(
            tmpdir,
            "0001_create_users.sql",
            """-- upgrade

CREATE TABLE IF NOT EXISTS users (
    id INTEGER NOT NULL PRIMARY KEY
)

-- rollback

DROP TABLE users
""",
        )

        table = ModelTable(
            name="users",
            columns=[
                ModelColumn("id", "INTEGER", False, True, False, None, None),
                ModelColumn("email", "VARCHAR(255)", False, False, True, None, None),
            ],
        )

        upgrade_sql, rollback_sql, _ = generate_migration_sql(
            [table], migrations_dir=tmpdir
        )

        assert "ALTER TABLE users ADD COLUMN email" in upgrade_sql
        assert "CREATE TABLE IF NOT EXISTS users" not in upgrade_sql
        assert "ALTER TABLE users DROP COLUMN email" in rollback_sql


def test_build_migration_plan_includes_operations_and_checksum():
    plan = build_migration_plan(
        migration_id="primary__0001_add_users_age",
        changes=[Change(operation="add_column", table="users", target="age")],
        upgrade_sql="ALTER TABLE users ADD COLUMN age INTEGER",
    )

    assert plan["migration_id"] == "primary__0001_add_users_age"
    assert plan["required_flags"] == []
    assert plan["operations"] == [
        {
            "type": "add_column",
            "table": "users",
            "column": "age",
            "severity": "INFO",
        }
    ]
    assert isinstance(plan["checksum"], str)
    assert plan["checksum"]


def test_make_migrations_writes_plan_file_next_to_sql():
    set_dev_mode(False)
    with tempfile.TemporaryDirectory() as tmpdir:
        old_cwd = os.getcwd()
        os.chdir(tmpdir)
        try:
            Path("dbwarden.py").write_text(
                "from dbwarden import database_config\n\n"
                "database_config(database_name='primary', default=True, database_type='sqlite', database_url_sync='sqlite:///./app.db', model_paths=['models'])\n",
                encoding="utf-8",
            )
            Path("migrations/primary").mkdir(parents=True)
            Path("models").mkdir(parents=True)
            Path("models/user.py").write_text(
                "from sqlalchemy import Column, Integer, String\n"
                "from sqlalchemy.orm import declarative_base\n\n"
                "Base = declarative_base()\n\n"
                "class User(Base):\n"
                "    __tablename__ = 'users'\n"
                "    id = Column(Integer, primary_key=True)\n"
                "    email = Column(String(255), nullable=False, unique=True)\n",
                encoding="utf-8",
            )

            make_migrations_cmd(database="primary")

            sql_files = sorted(Path("migrations/primary").glob("*.sql"))
            plan_files = sorted(Path("migrations/primary").glob("*.plan.json"))

            assert len(sql_files) == 1
            assert len(plan_files) == 1
            assert plan_files[0].name == sql_files[0].with_suffix(".plan.json").name

            plan = json.loads(plan_files[0].read_text(encoding="utf-8"))
            assert plan["migration_id"] == sql_files[0].stem
            assert plan["operations"]
            assert plan["checksum"]
        finally:
            os.chdir(old_cwd)


def test_parse_rename_flags_valid():
    result = _parse_rename_flags(["users.name:full_name", "posts.title:headline"])
    assert len(result) == 2
    assert result[0] == RenameIntent("users", "name", "full_name")
    assert result[1] == RenameIntent("posts", "title", "headline")


def test_parse_rename_flags_invalid_format():
    import pytest
    with pytest.raises(ValueError, match="Invalid --rename format"):
        _parse_rename_flags(["badformat"])


def test_parse_rename_flags_missing_new_name():
    import pytest
    with pytest.raises(ValueError, match="Invalid --rename format"):
        _parse_rename_flags(["users.name:"])


def test_build_plan_operation_includes_resolved_from():
    plan = build_migration_plan(
        migration_id="test__0001_rename",
        changes=[
            Change(operation="rename_column", table="users", target="email",
                   resolved_from="rename_flag"),
        ],
        upgrade_sql="ALTER TABLE users RENAME COLUMN name TO email",
    )
    op = plan["operations"][0]
    assert op["resolved_from"] == "rename_flag"
    assert op["type"] == "rename_column"
    assert op["new_name"] == "email"


def test_build_plan_operation_omits_resolved_from_when_none():
    plan = build_migration_plan(
        migration_id="test__0001_add",
        changes=[
            Change(operation="add_column", table="users", target="age"),
        ],
        upgrade_sql="ALTER TABLE users ADD COLUMN age INTEGER",
    )
    assert "resolved_from" not in plan["operations"][0]


def test_parse_rename_table_flags_valid():
    result = _parse_rename_table_flags(["users:accounts", "posts:articles"])
    assert len(result) == 2
    assert result[0] == {"old_table": "users", "new_table": "accounts"}
    assert result[1] == {"old_table": "posts", "new_table": "articles"}


def test_parse_rename_table_flags_invalid_format():
    import pytest
    with pytest.raises(ValueError, match="Invalid --rename-table format"):
        _parse_rename_table_flags(["badformat"])


def test_parse_rename_table_flags_empty_parts():
    import pytest
    with pytest.raises(ValueError, match="Invalid --rename-table format"):
        _parse_rename_table_flags(["users:"])


def test_validate_table_rename_intents_old_not_in_snapshot():
    import pytest
    with pytest.raises(ValueError, match="does not exist"):
        _validate_table_rename_intents(
            [{"old_table": "ghost", "new_table": "phantom"}],
            {"tables": {"users": {"columns": {}}}},
            {"phantom"},
        )


def test_validate_table_rename_intents_new_already_in_snapshot():
    import pytest
    with pytest.raises(ValueError, match="already exists"):
        _validate_table_rename_intents(
            [{"old_table": "users", "new_table": "accounts"}],
            {"tables": {"users": {"columns": {}}, "accounts": {"columns": {}}}},
            {"accounts"},
        )


def test_validate_table_rename_intents_old_still_in_models():
    import pytest
    with pytest.raises(ValueError, match="still present"):
        _validate_table_rename_intents(
            [{"old_table": "users", "new_table": "accounts"}],
            {"tables": {"users": {"columns": {}}}},
            {"users", "accounts"},
        )


def test_validate_table_rename_intents_new_not_in_models():
    import pytest
    with pytest.raises(ValueError, match="not found in models"):
        _validate_table_rename_intents(
            [{"old_table": "users", "new_table": "accounts"}],
            {"tables": {"users": {"columns": {}}}},
            set(),
        )


def test_validate_table_rename_intents_valid():
    _validate_table_rename_intents(
        [{"old_table": "users", "new_table": "accounts"}],
        {"tables": {"users": {"columns": {}}}},
        {"accounts"},
    )


def test_format_table_rename_warning():
    msg = _format_table_rename_warning([("users", "accounts", 0.78)])
    assert "users" in msg
    assert "accounts" in msg
    assert "78%" in msg
    assert "--rename-table" in msg


def test_build_plan_operation_rename_table():
    plan = build_migration_plan(
        migration_id="test__0001_rename_table",
        changes=[
            Change(operation="rename_table", table="users", target="accounts",
                   resolved_from="rename_flag"),
        ],
        upgrade_sql="ALTER TABLE users RENAME TO accounts",
    )
    op = plan["operations"][0]
    assert op["type"] == "rename_table"
    assert op["new_name"] == "accounts"
    assert op["old_table"] == "users"
    assert op["resolved_from"] == "rename_flag"


def test_resolve_clickhouse_recreate_ops_requires_flag():
    import pytest
    upgrade = [{"type": "recreate_ch_table", "table": "events"}]
    rollback = [{"type": "recreate_ch_table", "table": "events"}]
    with pytest.raises(ValueError, match="--clickhouse-engine-recreate"):
        _resolve_clickhouse_recreate_ops(upgrade, rollback, False, None)


def test_resolve_clickhouse_recreate_ops_non_tty_preserves_old():
    upgrade = [{"type": "recreate_ch_table", "table": "events"}]
    rollback = [{"type": "recreate_ch_table", "table": "events"}]
    _resolve_clickhouse_recreate_ops(upgrade, rollback, True, None)
    assert upgrade[0]["drop_old_after_swap"] is False
    assert rollback[0]["drop_old_after_swap"] is False


def test_resolve_clickhouse_recreate_ops_explicit_drop():
    upgrade = [{"type": "recreate_ch_table", "table": "events"}]
    rollback = [{"type": "recreate_ch_table", "table": "events"}]
    _resolve_clickhouse_recreate_ops(upgrade, rollback, True, True)
    assert upgrade[0]["drop_old_after_swap"] is True
    assert rollback[0]["drop_old_after_swap"] is True


def test_resolve_clickhouse_recreate_ops_tty_prompt_yes(monkeypatch):
    upgrade = [{"type": "recreate_ch_table", "table": "events"}]
    rollback = [{"type": "recreate_ch_table", "table": "events"}]
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda _prompt: "y")
    _resolve_clickhouse_recreate_ops(upgrade, rollback, True, None)
    assert upgrade[0]["drop_old_after_swap"] is True


def test_check_recreate_rename_conflict_raises():
    ops = [{"type": "recreate_ch_table", "table": "events"}]
    intents = {("events", "events_new")}
    import pytest
    with pytest.raises(ValueError, match="rename and an engine change"):
        _check_recreate_rename_conflict(ops, intents)


def test_check_recreate_rename_conflict_ok():
    ops = [{"type": "recreate_ch_table", "table": "events"}]
    intents: set[tuple[str, str]] = set()
    _check_recreate_rename_conflict(ops, intents)  # no raise


def test_generate_migration_sql_with_table_rename():
    with tempfile.TemporaryDirectory() as tmpdir:
        table = ModelTable(
            name="accounts",
            columns=[
                ModelColumn("id", "INTEGER", False, True, False, None, None),
            ],
        )
        upgrade_sql, rollback_sql, changes = generate_migration_sql(
            [table],
            confirmed_table_intents={("users", "accounts")},
            table_resolved_from_map={("users", "accounts"): "rename_flag"},
        )
        assert any(
            c.operation == "rename_table" and c.target == "accounts"
            for c in changes
        )


class TestPGDomainSequenceSQL:
    def test_build_domain_sql_basic(self):
        from dbwarden.commands.make_migrations import _build_domain_sql
        domain = {"name": "positive_int", "type": "integer", "not_null": True, "check": "VALUE > 0"}
        sql = _build_domain_sql(domain)
        assert sql == "CREATE DOMAIN positive_int AS integer NOT NULL CHECK (VALUE > 0);"

    def test_build_domain_sql_with_default_and_schema(self):
        from dbwarden.commands.make_migrations import _build_domain_sql
        domain = {
            "name": "my_email", "type": "citext", "schema": "app",
            "default": "'nobody@example.com'", "not_null": False, "check": "VALUE ~* '^.+@.+$'",
        }
        sql = _build_domain_sql(domain)
        assert sql == "CREATE DOMAIN app.my_email AS citext DEFAULT 'nobody@example.com' CHECK (VALUE ~* '^.+@.+$');"

    def test_build_domain_sql_no_options(self):
        from dbwarden.commands.make_migrations import _build_domain_sql
        domain = {"name": "simple_type", "type": "text"}
        sql = _build_domain_sql(domain)
        assert sql == "CREATE DOMAIN simple_type AS text;"

    def test_build_sequence_sql_basic(self):
        from dbwarden.commands.make_migrations import _build_sequence_sql
        seq = {"name": "order_number_seq", "start": 1000, "increment": 1}
        sql = _build_sequence_sql(seq)
        assert "CREATE SEQUENCE IF NOT EXISTS order_number_seq" in sql
        assert "INCREMENT BY 1" in sql
        assert "START WITH 1000" in sql
        assert "NO CYCLE" in sql

    def test_build_sequence_sql_with_schema_and_cycle(self):
        from dbwarden.commands.make_migrations import _build_sequence_sql
        seq = {
            "name": "global_id_seq", "schema": "app",
            "start": 1, "increment": 1, "minvalue": 1, "maxvalue": 999999,
            "cycle": True, "owned_by": "app.users.id",
        }
        sql = _build_sequence_sql(seq)
        assert "CREATE SEQUENCE IF NOT EXISTS app.global_id_seq" in sql
        assert "MINVALUE 1" in sql
        assert "MAXVALUE 999999" in sql
        assert "CYCLE" in sql
        assert "OWNED BY app.users.id" in sql

    def test_build_sequence_sql_minimal(self):
        from dbwarden.commands.make_migrations import _build_sequence_sql
        seq = {"name": "simple_seq"}
        sql = _build_sequence_sql(seq)
        assert sql == "CREATE SEQUENCE IF NOT EXISTS simple_seq NO CYCLE;"

    def test_drop_domain_sql(self):
        from dbwarden.commands.make_migrations import _drop_domain_sql
        assert _drop_domain_sql({"name": "positive_int"}) == "DROP DOMAIN IF EXISTS positive_int;"
        assert _drop_domain_sql({"name": "my_email", "schema": "app"}) == "DROP DOMAIN IF EXISTS app.my_email;"

    def test_drop_sequence_sql(self):
        from dbwarden.commands.make_migrations import _drop_sequence_sql
        assert _drop_sequence_sql({"name": "seq"}) == "DROP SEQUENCE IF EXISTS seq;"
        assert _drop_sequence_sql({"name": "seq", "schema": "app"}) == "DROP SEQUENCE IF EXISTS app.seq;"
