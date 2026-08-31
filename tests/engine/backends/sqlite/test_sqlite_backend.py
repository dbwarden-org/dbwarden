"""SQLite backend: rendering, rebuild generation, op collapse and reflection."""

import sqlite3

import pytest

from dbwarden.engine.backends.sqlite.collapse import (
    collapse_sqlite_ops,
    model_state_entries,
    snapshot_state_entries,
)
from dbwarden.engine.backends.sqlite.extract import (
    parse_sqlite_column_meta,
    parse_sqlite_table_options,
)
from dbwarden.engine.backends.sqlite.handlers import SqTableHandler
from dbwarden.engine.backends.sqlite.render import (
    quote_sq,
    render_sqlite_column_def,
    render_sqlite_column_type,
    render_sqlite_default,
    render_sqlite_table_suffix,
    sqlite_autoincrement_column,
)
from dbwarden.engine.backends.sqlite.safety import analyze_sqlite_options
from dbwarden.engine.backends.sqlite.sql_build import (
    add_column_requires_rebuild,
    build_sqlite_add_column_sql,
    build_sqlite_create_table_sql,
    build_sqlite_rebuild_sql,
    drop_column_requires_rebuild,
)
from dbwarden.engine.core.models import ModelColumn, ModelTable
from dbwarden.engine.core.protocol import Op


def _col(name, type_="TEXT", *, nullable=True, pk=False, unique=False,
         default=None, sq_meta=None, fk=None, autoincrement=None):
    return ModelColumn(
        name=name, type=type_, nullable=nullable, primary_key=pk, unique=unique,
        default=default, foreign_key=fk, sq_meta=sq_meta or {},
        autoincrement=autoincrement,
    )


def _table(name="users", columns=None, **kwargs):
    return ModelTable(name=name, columns=columns or [], **kwargs)


class TestRender:
    def test_plain_identifier_is_not_quoted(self):
        assert quote_sq("users") == "users"

    def test_reserved_word_and_odd_name_are_quoted(self):
        assert quote_sq("order") == '"order"'
        assert quote_sq("we ird") == '"we ird"'
        assert quote_sq('a"b') == '"a""b"'

    def test_declared_type_is_preserved_outside_strict_tables(self):
        assert render_sqlite_column_type("VARCHAR(64)") == "VARCHAR(64)"

    @pytest.mark.parametrize(
        ("declared", "expected"),
        [
            ("VARCHAR(64)", "TEXT"),
            ("BOOLEAN", "INTEGER"),
            ("DATETIME", "TEXT"),
            ("NUMERIC(10, 2)", "REAL"),
            ("BIGINT", "INTEGER"),
            ("DOUBLE PRECISION", "REAL"),
            ("TEXT", "TEXT"),
        ],
    )
    def test_strict_tables_collapse_types_to_the_accepted_set(self, declared, expected):
        assert render_sqlite_column_type(declared, strict=True) == expected

    def test_generated_column_renders_expression_and_mode(self):
        column = _col("slug", sq_meta={"sq_generated": "lower(title)", "sq_generated_mode": "VIRTUAL"})
        assert render_sqlite_column_def(column) == (
            "slug TEXT GENERATED ALWAYS AS (lower(title)) VIRTUAL"
        )

    def test_generated_column_defaults_to_stored(self):
        column = _col("slug", sq_meta={"sq_generated": "lower(title)"})
        assert render_sqlite_column_def(column).endswith("STORED")

    def test_function_default_is_parenthesised(self):
        assert render_sqlite_default("uuid4()") == "(uuid4())"
        assert render_sqlite_default("CURRENT_TIMESTAMP") == "CURRENT_TIMESTAMP"
        assert render_sqlite_default("5") == "5"

    def test_table_suffix_renders_both_options(self):
        table = _table(sq_table={"sq_without_rowid": True, "sq_strict": True})
        assert render_sqlite_table_suffix(table) == " WITHOUT ROWID, STRICT"

    def test_no_suffix_without_options(self):
        assert render_sqlite_table_suffix(_table()) == ""

    def test_autoincrement_needs_single_integer_key_on_a_rowid_table(self):
        rowid = _table(columns=[_col("id", "INTEGER", pk=True, autoincrement=True)])
        assert sqlite_autoincrement_column(rowid) == "id"

        text_key = _table(columns=[_col("id", "TEXT", pk=True, autoincrement=True)])
        assert sqlite_autoincrement_column(text_key) is None

        without_rowid = _table(
            columns=[_col("id", "INTEGER", pk=True, autoincrement=True)],
            sq_table={"sq_without_rowid": True},
        )
        assert sqlite_autoincrement_column(without_rowid) is None


class TestCreateTable:
    def test_constraints_are_inlined(self):
        table = _table(
            columns=[
                _col("id", "INTEGER", pk=True, nullable=False, autoincrement=True),
                _col("email", "VARCHAR(255)", nullable=False),
            ],
            uniques=[{"columns": ["email"], "name": "uq_users_email"}],
            checks=[{"name": "ck_users_email", "expression": "email <> ''"}],
        )
        sql = build_sqlite_create_table_sql(table)
        assert "id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT" in sql
        assert "CONSTRAINT uq_users_email UNIQUE (email)" in sql
        assert "CONSTRAINT ck_users_email CHECK (email <> '')" in sql

    def test_composite_primary_key_becomes_a_table_constraint(self):
        table = _table(columns=[
            _col("a", "INTEGER", pk=True, nullable=False),
            _col("b", "INTEGER", pk=True, nullable=False),
        ])
        sql = build_sqlite_create_table_sql(table)
        assert "PRIMARY KEY (a, b)" in sql
        assert "a INTEGER NOT NULL PRIMARY KEY" not in sql

    def test_single_column_foreign_key_is_not_emitted_twice(self):
        table = _table(columns=[_col("group_id", "INTEGER", fk="groups(id)")],
                       foreign_keys=[{"columns": ["group_id"],
                                      "referred_table": "groups",
                                      "referred_columns": ["id"]}])
        sql = build_sqlite_create_table_sql(table)
        assert sql.count("groups") == 1
        assert "FOREIGN KEY" not in sql

    def test_multi_column_foreign_key_becomes_a_table_constraint(self):
        table = _table(columns=[_col("a", "INTEGER"), _col("b", "INTEGER")],
                       foreign_keys=[{"columns": ["a", "b"],
                                      "referred_table": "other",
                                      "referred_columns": ["x", "y"],
                                      "on_delete": "CASCADE"}])
        sql = build_sqlite_create_table_sql(table)
        assert "FOREIGN KEY (a, b) REFERENCES other (x, y) ON DELETE CASCADE" in sql

    def test_table_options_are_appended(self):
        table = _table(columns=[_col("k", "TEXT", pk=True, nullable=False)],
                       sq_table={"sq_without_rowid": True})
        assert build_sqlite_create_table_sql(table).endswith(") WITHOUT ROWID;")

    def test_generated_output_is_valid_sqlite(self):
        table = _table(
            columns=[
                _col("id", "INTEGER", pk=True, nullable=False, autoincrement=True),
                _col("title", "TEXT", nullable=False),
                _col("slug", "TEXT", sq_meta={"sq_generated": "lower(title)"}),
            ],
            uniques=[{"columns": ["title"], "name": "uq_t"}],
        )
        connection = sqlite3.connect(":memory:")
        connection.executescript(build_sqlite_create_table_sql(table))


class TestAddColumn:
    def test_plain_column_uses_alter_table(self):
        sql = build_sqlite_add_column_sql("users", _col("nickname", "TEXT"))
        assert sql == "ALTER TABLE users ADD COLUMN nickname TEXT"

    @pytest.mark.parametrize(
        ("column", "fragment"),
        [
            (_col("id", "INTEGER", pk=True), "primary key"),
            (_col("email", unique=True), "UNIQUE"),
            (_col("email", nullable=False), "NOT NULL"),
            (_col("created", default="now()"), "non-constant DEFAULT"),
            (_col("slug", sq_meta={"sq_generated": "lower(x)"}), "STORED generated"),
        ],
    )
    def test_columns_sqlite_cannot_add_are_reported(self, column, fragment):
        reason = add_column_requires_rebuild(column)
        assert reason is not None and fragment in reason

    def test_virtual_generated_column_can_be_added(self):
        column = _col("slug", sq_meta={"sq_generated": "lower(x)", "sq_generated_mode": "VIRTUAL"})
        assert add_column_requires_rebuild(column) is None

    def test_nullable_column_with_constant_default_can_be_added(self):
        assert add_column_requires_rebuild(_col("n", "INTEGER", default="0")) is None


class TestDropColumn:
    ENTRY = {
        "name": "users",
        "columns": {
            "id": {"type": "INTEGER", "primary_key": True},
            "email": {"type": "TEXT"},
            "title": {"type": "TEXT"},
            "slug": {"type": "TEXT", "sq_column": {"sq_generated": "lower(title)"}},
            "code": {"type": "TEXT"},
            "expr": {"type": "TEXT"},
            "spare": {"type": "TEXT"},
        },
        "primary_key": ["id"],
        "indexes": [
            {"name": "ix_email", "columns": ["email"]},
            {"name": "ix_expr", "columns": [], "expression": "lower(expr)"},
        ],
        "uniques": [{"name": "uq_code", "columns": ["code"]}],
        "checks": [],
        "foreign_keys": [],
    }

    def test_unconstrained_column_drops_directly(self):
        assert drop_column_requires_rebuild("spare", self.ENTRY) is None

    def test_primary_key_column_needs_a_rebuild(self):
        assert "primary key" in drop_column_requires_rebuild("id", self.ENTRY)

    def test_indexed_column_needs_a_rebuild(self):
        assert "indexed" in drop_column_requires_rebuild("email", self.ENTRY)

    def test_column_in_an_index_expression_needs_a_rebuild(self):
        assert "index expression" in drop_column_requires_rebuild("expr", self.ENTRY)

    def test_constrained_column_needs_a_rebuild(self):
        assert "table constraint" in drop_column_requires_rebuild("code", self.ENTRY)

    def test_column_used_by_a_generated_column_needs_a_rebuild(self):
        reason = drop_column_requires_rebuild("title", self.ENTRY)
        assert "generated column 'slug'" in reason


class TestRebuildSql:
    def _tables(self):
        before = _table(columns=[
            _col("id", "INTEGER", pk=True, nullable=False, autoincrement=True),
            _col("email", "VARCHAR(255)"),
            _col("age", "INTEGER"),
        ], indexes=[{"name": "ix_email", "columns": ["email"]}])
        after = _table(columns=[
            _col("id", "INTEGER", pk=True, nullable=False, autoincrement=True),
            _col("email", "VARCHAR(255)", nullable=False),
            _col("age", "TEXT"),
        ], indexes=[{"name": "ix_email", "columns": ["email"]}])
        return before, after

    def test_rebuild_follows_create_copy_drop_rename(self):
        before, after = self._tables()
        sql = build_sqlite_rebuild_sql(before, after)
        order = [
            sql.index("CREATE TABLE users__dbw_new"),
            sql.index("INSERT INTO users__dbw_new"),
            sql.index("DROP TABLE users"),
            sql.index("ALTER TABLE users__dbw_new RENAME TO users"),
            sql.index("CREATE INDEX IF NOT EXISTS ix_email"),
        ]
        assert order == sorted(order)

    def test_staging_table_is_not_created_conditionally(self):
        before, after = self._tables()
        sql = build_sqlite_rebuild_sql(before, after)
        assert "CREATE TABLE users__dbw_new" in sql
        assert "CREATE TABLE IF NOT EXISTS users__dbw_new" not in sql

    def test_generated_columns_are_not_copied(self):
        before, after = self._tables()
        after.columns.append(
            _col("slug", "TEXT", sq_meta={"sq_generated": "lower(email)"})
        )
        sql = build_sqlite_rebuild_sql(before, after)
        insert_line = next(line for line in sql.splitlines() if line.startswith("INSERT"))
        assert "slug" not in insert_line

    def test_rebuild_runs_against_sqlite_and_preserves_rows(self):
        before, after = self._tables()
        connection = sqlite3.connect(":memory:")
        connection.executescript(
            "CREATE TABLE users (id INTEGER PRIMARY KEY AUTOINCREMENT,"
            " email VARCHAR(255), age INTEGER);"
            "CREATE INDEX ix_email ON users (email);"
            "INSERT INTO users (email, age) VALUES ('a@b.c', 41);"
        )
        connection.executescript(build_sqlite_rebuild_sql(before, after))
        assert connection.execute("SELECT id, email, age FROM users").fetchall() == [
            (1, "a@b.c", "41"),
        ]
        indexes = connection.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='users'"
        ).fetchall()
        assert ("ix_email",) in indexes

    def test_reverse_rebuild_restores_the_original_shape(self):
        before, after = self._tables()
        connection = sqlite3.connect(":memory:")
        connection.executescript(
            "CREATE TABLE users (id INTEGER PRIMARY KEY AUTOINCREMENT,"
            " email VARCHAR(255), age INTEGER);"
            "CREATE INDEX ix_email ON users (email);"
        )
        connection.executescript(build_sqlite_rebuild_sql(before, after))
        connection.executescript(build_sqlite_rebuild_sql(after, before))
        schema = connection.execute(
            "SELECT sql FROM sqlite_master WHERE name='users'"
        ).fetchone()[0]
        assert "age INTEGER" in schema
        assert "email VARCHAR(255)," in schema


class TestCollapse:
    SNAPSHOT = {
        "tables": {
            "users": {
                "columns": {
                    "id": {"type": "INTEGER", "primary_key": True, "nullable": False},
                    "age": {"type": "INTEGER", "nullable": True},
                },
                "primary_key": ["id"],
            },
        },
        "indexes": {},
        "constraints": {},
    }

    def _entries(self):
        from_entries = snapshot_state_entries(self.SNAPSHOT)
        to_entries = model_state_entries([
            _table(columns=[
                _col("id", "INTEGER", pk=True, nullable=False),
                _col("age", "TEXT"),
            ])
        ])
        return from_entries, to_entries

    def test_unsupported_ops_become_one_rebuild_per_table(self):
        from_entries, to_entries = self._entries()
        upgrade, rollback = collapse_sqlite_ops(
            [
                {"type": "alter_column_type", "table": "users", "column": "age"},
                {"type": "alter_column_nullable", "table": "users", "column": "age"},
            ],
            [
                {"type": "alter_column_type", "table": "users", "column": "age"},
                {"type": "alter_column_nullable", "table": "users", "column": "age"},
            ],
            from_entries=from_entries,
            to_entries=to_entries,
        )
        assert [op["type"] for op in upgrade] == ["recreate_sq_table"]
        assert [op["type"] for op in rollback] == ["recreate_sq_table"]
        assert "column type change" in upgrade[0]["reason"]

    def test_rollback_rebuild_runs_in_the_opposite_direction(self):
        from_entries, to_entries = self._entries()
        upgrade, rollback = collapse_sqlite_ops(
            [{"type": "alter_column_type", "table": "users", "column": "age"}],
            [{"type": "alter_column_type", "table": "users", "column": "age"}],
            from_entries=from_entries, to_entries=to_entries,
        )
        assert upgrade[0]["from_table"] == rollback[0]["to_table"]
        assert upgrade[0]["to_table"] == rollback[0]["from_table"]

    def test_supported_operations_are_left_alone(self):
        from_entries, to_entries = self._entries()
        ops = [
            {"type": "rename_column", "table": "users", "column": "age"},
            {"type": "add_index", "table": "users", "columns": ["age"]},
        ]
        upgrade, _ = collapse_sqlite_ops(
            ops, [], from_entries=from_entries, to_entries=to_entries,
        )
        assert [op["type"] for op in upgrade] == ["rename_column", "add_index"]

    def test_constraints_of_a_new_table_are_dropped_not_rebuilt(self):
        from_entries, to_entries = self._entries()
        upgrade, _ = collapse_sqlite_ops(
            [
                {"type": "create_table", "table": "users"},
                {"type": "add_unique_constraint", "table": "users", "columns": ["age"]},
            ],
            [],
            from_entries=from_entries, to_entries=to_entries,
        )
        assert [op["type"] for op in upgrade] == ["create_table"]

    def test_ops_are_left_alone_when_the_table_shape_is_unknown(self):
        upgrade, _ = collapse_sqlite_ops(
            [{"type": "alter_column_type", "table": "ghost", "column": "age"}],
            [],
            from_entries={}, to_entries={},
        )
        assert [op["type"] for op in upgrade] == ["alter_column_type"]


class TestSqTableHandler:
    def test_table_option_change_is_detected(self):
        handler = SqTableHandler()
        snap = handler.canonicalize(handler.extract({
            "tables": {"t": {"sq_table": {"sq_strict": True}, "columns": {}}},
        }))
        model = handler.canonicalize(handler.model_spec_from_tables([
            _table("t", sq_table={"sq_strict": False, "sq_without_rowid": True}),
        ]))
        upgrade, _ = handler.diff(snap, model)
        changed = {(op.upgrade_attrs["key"], op.upgrade_attrs["to_value"]) for op in upgrade}
        assert changed == {("sq_strict", False), ("sq_without_rowid", True)}

    def test_generated_expression_change_is_detected(self):
        handler = SqTableHandler()
        snap = handler.canonicalize(handler.extract({
            "tables": {"t": {"columns": {"slug": {"sq_column": {"sq_generated": "lower(a)"}}}}},
        }))
        model = handler.canonicalize(handler.model_spec_from_tables([
            _table("t", columns=[_col("slug", sq_meta={"sq_generated": "upper(a)"})]),
        ]))
        upgrade, _ = handler.diff(snap, model)
        assert [op.object_type for op in upgrade] == ["alter_sq_column_meta"]

    def test_implicit_stored_mode_is_not_a_change(self):
        handler = SqTableHandler()
        snap = handler.canonicalize(handler.extract({
            "tables": {"t": {"columns": {
                "slug": {"sq_column": {"sq_generated": "lower(a)", "sq_generated_mode": "STORED"}},
            }}},
        }))
        model = handler.canonicalize(handler.model_spec_from_tables([
            _table("t", columns=[_col("slug", sq_meta={"sq_generated": "lower(a)"})]),
        ]))
        assert handler.diff(snap, model) == ([], [])

    def test_emit_is_a_no_op_on_another_backend(self, monkeypatch):
        monkeypatch.setattr("dbwarden.engine.snapshot._get_backend", lambda db_name=None: "postgresql")
        handler = SqTableHandler()
        op = Op(object_type="alter_sq_table", upgrade_attrs={"table": "t", "key": "sq_strict"})
        assert handler.emit(op) == []


class TestReflection:
    def test_table_options_are_read_from_the_stored_ddl(self):
        assert parse_sqlite_table_options(
            "CREATE TABLE t (k TEXT PRIMARY KEY) STRICT, WITHOUT ROWID"
        ) == {"sq_strict": True, "sq_without_rowid": True}

    def test_a_column_named_strict_is_not_a_strict_table(self):
        assert parse_sqlite_table_options("CREATE TABLE t (strict INT)") == {}

    def test_generated_columns_are_read_one_by_one(self):
        ddl = (
            "CREATE TABLE t (a TEXT, "
            "b TEXT GENERATED ALWAYS AS (lower(a)) STORED, "
            "c TEXT AS (upper(a)) VIRTUAL, "
            "d TEXT COLLATE NOCASE)"
        )
        assert parse_sqlite_column_meta(ddl) == {
            "a": {"sq_declared_type": "TEXT"},
            "b": {"sq_declared_type": "TEXT", "sq_generated": "lower(a)"},
            "c": {
                "sq_declared_type": "TEXT",
                "sq_generated": "upper(a)",
                "sq_generated_mode": "VIRTUAL",
            },
            "d": {"sq_declared_type": "TEXT", "sq_collate": "NOCASE"},
        }

    def test_declared_type_is_captured_as_written(self):
        ddl = 'CREATE TABLE t (a VARCHAR(255), b NUMERIC(10, 2) NOT NULL, c DOUBLE PRECISION)'
        meta = parse_sqlite_column_meta(ddl)
        assert meta["a"]["sq_declared_type"] == "VARCHAR(255)"
        assert meta["b"]["sq_declared_type"] == "NUMERIC(10, 2)"
        assert meta["c"]["sq_declared_type"] == "DOUBLE PRECISION"

    def test_autoincrement_is_read_from_the_declaration(self):
        ddl = "CREATE TABLE t (id INTEGER PRIMARY KEY AUTOINCREMENT, a TEXT)"
        meta = parse_sqlite_column_meta(ddl)
        assert meta["id"]["sq_autoincrement"] is True
        assert "sq_autoincrement" not in meta.get("a", {})

    def test_table_constraints_are_not_read_as_columns(self):
        ddl = "CREATE TABLE t (a TEXT, CONSTRAINT u UNIQUE (a), CHECK (a <> ''))"
        assert set(parse_sqlite_column_meta(ddl)) == {"a"}

    def test_reflection_matches_a_live_database(self):
        connection = sqlite3.connect(":memory:")
        connection.executescript(
            "CREATE TABLE t (a TEXT, b TEXT GENERATED ALWAYS AS (lower(a)) STORED);"
            "CREATE TABLE w (k TEXT PRIMARY KEY) WITHOUT ROWID;"
        )
        ddl_t = connection.execute(
            "SELECT sql FROM sqlite_master WHERE name='t'"
        ).fetchone()[0]
        ddl_w = connection.execute(
            "SELECT sql FROM sqlite_master WHERE name='w'"
        ).fetchone()[0]
        assert parse_sqlite_column_meta(ddl_t)["b"]["sq_generated"] == "lower(a)"
        assert parse_sqlite_table_options(ddl_w) == {"sq_without_rowid": True}


class TestSafety:
    def test_table_option_change_warns_about_the_rebuild(self):
        issues = analyze_sqlite_options(
            {"sq_table": {}, "columns": {}},
            _table(sq_table={"sq_strict": True}),
        )
        assert [issue.change_type for issue in issues] == ["sq_strict"]
        assert issues[0].severity == "WARNING"
        assert "copies every row" in issues[0].message

    def test_generated_expression_change_warns(self):
        issues = analyze_sqlite_options(
            {"columns": {"slug": {"sq_column": {"sq_generated": "lower(a)"}}}},
            _table(columns=[_col("slug", sq_meta={"sq_generated": "upper(a)"})]),
        )
        assert [issue.change_type for issue in issues] == ["change_sq_generated"]

    def test_no_issues_when_nothing_changed(self):
        table = _table(columns=[_col("slug", sq_meta={"sq_generated": "lower(a)"})])
        assert analyze_sqlite_options(
            {"columns": {"slug": {"sq_column": {"sq_generated": "lower(a)"}}}}, table,
        ) == []
