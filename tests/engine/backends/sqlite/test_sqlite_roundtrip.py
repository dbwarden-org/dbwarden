"""Round-trip safety for SQLite, against a real database.

Each scenario creates a table in a real SQLite file and then asserts three
things, which together are what "round-trip" has to mean:

1. **No churn.** Models built from the database diff clean against it.
2. **Convergence.** After applying a generated migration, the database matches
   the models it was generated from.
3. **Reversibility.** After applying the rollback, the database is back to the
   schema it started with.

A rebuild rewrites the whole table, so anything reflection fails to see is
silently dropped by step 2 or 3 - which is what these scenarios are for.
"""

import copy
import sqlite3
from types import SimpleNamespace

import pytest

from dbwarden.engine.backends.sqlite.collapse import snapshot_state_entries
from dbwarden.engine.core.model_state import reconstruct_model_table
from dbwarden.engine.snapshot import (
    diff_models_against_snapshot,
    extract_full_schema_snapshot,
    snapshot_diff_to_sql,
)


@pytest.fixture
def sqlite_backend(monkeypatch):
    config = SimpleNamespace(database_type="sqlite", model_paths=None, model_tables=None)
    monkeypatch.setattr(
        "dbwarden.engine.model_discovery.type_mapping.get_database",
        lambda db_name=None: config,
    )
    monkeypatch.setattr("dbwarden.config.get_database", lambda db_name=None: config)
    return config


def _snapshot_of(path):
    return extract_full_schema_snapshot(
        sqlalchemy_url=f"sqlite:///{path}", database_type="sqlite",
    )


def _models_of(snapshot):
    return [reconstruct_model_table(e) for e in snapshot_state_entries(snapshot).values()]


def _comparable(snapshot):
    """The parts of a snapshot a rebuild must reproduce exactly."""
    tables = {
        name: {
            "columns": table.get("columns") or {},
            "primary_key": sorted(table.get("primary_key") or []),
            "sq_table": table.get("sq_table"),
            "sq_index_ddl": table.get("sq_index_ddl"),
        }
        for name, table in (snapshot.get("tables") or {}).items()
    }
    return {
        "tables": tables,
        "indexes": snapshot.get("indexes") or {},
        "constraints": snapshot.get("constraints") or {},
    }


def _retype(table_name, column, new_type):
    def mutate(models):
        for table in models:
            if table.name != table_name:
                continue
            for col in table.columns:
                if col.name == column:
                    col.type = new_type
                    # A model does not carry the database's declared type.
                    col.sq_meta.pop("sq_declared_type", None)
        return models
    return mutate


# (id, DDL, mutation, seed rows)
SCENARIOS = [
    pytest.param(
        "CREATE TABLE users (id INTEGER PRIMARY KEY AUTOINCREMENT,"
        " email VARCHAR(255), age INTEGER);",
        _retype("users", "age", "TEXT"),
        "INSERT INTO users (email, age) VALUES ('a@b.c', 3);",
        id="autoincrement-and-varchar-length",
    ),
    pytest.param(
        'CREATE TABLE "order" ("select" TEXT PRIMARY KEY, "group" INTEGER, "we ird" TEXT);',
        _retype("order", "group", "TEXT"),
        None,
        id="reserved-identifiers",
    ),
    pytest.param(
        "CREATE TABLE cpk (a INTEGER NOT NULL, b TEXT NOT NULL, v INTEGER,"
        " PRIMARY KEY (a, b));",
        _retype("cpk", "v", "TEXT"),
        "INSERT INTO cpk VALUES (1, 'x', 9);",
        id="composite-primary-key",
    ),
    pytest.param(
        "CREATE TABLE wr (k TEXT NOT NULL PRIMARY KEY, v INTEGER) WITHOUT ROWID;",
        _retype("wr", "v", "TEXT"),
        "INSERT INTO wr VALUES ('k', 1);",
        id="without-rowid",
    ),
    pytest.param(
        "CREATE TABLE st (id INTEGER PRIMARY KEY, name TEXT NOT NULL, n TEXT) STRICT;",
        _retype("st", "n", "INTEGER"),
        "INSERT INTO st VALUES (1, 'a', '2');",
        id="strict",
    ),
    pytest.param(
        "CREATE TABLE g (id INTEGER PRIMARY KEY, title TEXT,"
        " slug TEXT GENERATED ALWAYS AS (lower(title)) STORED,"
        " up TEXT AS (upper(title)) VIRTUAL);",
        _retype("g", "title", "VARCHAR(80)"),
        "INSERT INTO g (title) VALUES ('Hi');",
        id="generated-columns",
    ),
    pytest.param(
        "CREATE TABLE d (id INTEGER PRIMARY KEY, s TEXT DEFAULT 'hi there',"
        " n INTEGER DEFAULT 5, t TEXT DEFAULT CURRENT_TIMESTAMP,"
        " e TEXT DEFAULT (hex(randomblob(4))));",
        _retype("d", "n", "TEXT"),
        "INSERT INTO d (id) VALUES (1);",
        id="defaults",
    ),
    pytest.param(
        "CREATE TABLE uc (id INTEGER PRIMARY KEY, email TEXT, code TEXT,"
        " CONSTRAINT uq_uc_email UNIQUE (email),"
        " CONSTRAINT ck_uc_code CHECK (code <> ''));",
        _retype("uc", "code", "VARCHAR(10)"),
        "INSERT INTO uc VALUES (1, 'a@b', 'x');",
        id="named-unique-and-check",
    ),
    pytest.param(
        "CREATE TABLE uu (id INTEGER PRIMARY KEY, email TEXT, v TEXT, UNIQUE (email));",
        _retype("uu", "v", "INTEGER"),
        "INSERT INTO uu VALUES (1, 'a@b', '1');",
        id="unnamed-unique",
    ),
    pytest.param(
        "CREATE TABLE parent (id INTEGER PRIMARY KEY, name TEXT);"
        "CREATE TABLE child (id INTEGER PRIMARY KEY,"
        " pid INTEGER REFERENCES parent(id) ON DELETE CASCADE ON UPDATE RESTRICT,"
        " v TEXT);",
        _retype("child", "v", "INTEGER"),
        "INSERT INTO parent VALUES (1, 'p'); INSERT INTO child VALUES (1, 1, '2');",
        id="foreign-key-actions",
    ),
    pytest.param(
        "CREATE TABLE p2 (a INTEGER NOT NULL, b INTEGER NOT NULL, PRIMARY KEY (a, b));"
        "CREATE TABLE c2 (id INTEGER PRIMARY KEY, x INTEGER, y INTEGER, v TEXT,"
        " FOREIGN KEY (x, y) REFERENCES p2 (a, b));",
        _retype("c2", "v", "INTEGER"),
        None,
        id="multi-column-foreign-key",
    ),
    pytest.param(
        "CREATE TABLE ix (id INTEGER PRIMARY KEY, a TEXT, b TEXT, c TEXT);"
        "CREATE INDEX ix_a ON ix (a);"
        "CREATE UNIQUE INDEX ix_b ON ix (b);"
        "CREATE INDEX ix_part ON ix (c) WHERE c IS NOT NULL;"
        "CREATE INDEX ix_expr ON ix (lower(a));"
        "CREATE INDEX ix_desc ON ix (a DESC, b COLLATE NOCASE);",
        _retype("ix", "a", "VARCHAR(20)"),
        None,
        id="index-shapes",
    ),
    pytest.param(
        "CREATE TABLE co (id INTEGER PRIMARY KEY, code TEXT COLLATE NOCASE, other TEXT);",
        _retype("co", "other", "INTEGER"),
        "INSERT INTO co VALUES (1, 'AbC', '3');",
        id="column-collation",
    ),
    pytest.param(
        "CREATE TABLE tk (k TEXT PRIMARY KEY, v INTEGER);",
        _retype("tk", "v", "TEXT"),
        "INSERT INTO tk VALUES ('a', 1);",
        id="text-primary-key",
    ),
    pytest.param(
        "CREATE TABLE npk (a TEXT, b INTEGER);",
        _retype("npk", "b", "TEXT"),
        "INSERT INTO npk VALUES ('x', 1);",
        id="no-primary-key",
    ),
]


def _build(tmp_path, ddl, seed):
    path = tmp_path / "app.db"
    connection = sqlite3.connect(path)
    connection.executescript(ddl)
    if seed:
        connection.executescript(seed)
    connection.commit()
    connection.close()
    return path


@pytest.mark.parametrize(("ddl", "mutate", "seed"), SCENARIOS)
class TestRoundTrip:
    def test_models_read_back_from_the_database_diff_clean(
        self, sqlite_backend, tmp_path, ddl, mutate, seed,
    ):
        path = _build(tmp_path, ddl, seed)
        snapshot = _snapshot_of(path)
        models = _models_of(snapshot)
        upgrade_ops, _ = diff_models_against_snapshot(
            models, copy.deepcopy(snapshot), db_name=None,
        )
        assert upgrade_ops == []

    def test_migration_converges_and_rolls_back(
        self, sqlite_backend, tmp_path, ddl, mutate, seed,
    ):
        path = _build(tmp_path, ddl, seed)
        original = _snapshot_of(path)

        changed = mutate(_models_of(copy.deepcopy(original)))
        upgrade_ops, rollback_ops = diff_models_against_snapshot(
            changed, copy.deepcopy(original), db_name=None,
        )
        assert upgrade_ops, "the mutation produced no operations"

        upgrade, rollback, _ = snapshot_diff_to_sql(
            upgrade_ops, rollback_ops, db_name=None, enforce_rollback_contract=True,
        )

        connection = sqlite3.connect(path)
        connection.executescript(upgrade)
        connection.commit()
        connection.close()

        # The database now matches the models the migration came from.
        after_upgrade = _snapshot_of(path)
        residual, _ = diff_models_against_snapshot(
            changed, copy.deepcopy(after_upgrade), db_name=None,
        )
        assert residual == []

        connection = sqlite3.connect(path)
        connection.executescript(rollback)
        connection.commit()
        connection.close()

        # And the rollback restores the schema byte for byte.
        assert _comparable(_snapshot_of(path)) == _comparable(original)


def test_a_rebuild_keeps_the_rows(sqlite_backend, tmp_path):
    path = _build(
        tmp_path,
        "CREATE TABLE users (id INTEGER PRIMARY KEY AUTOINCREMENT, email TEXT, age INTEGER);",
        "INSERT INTO users (email, age) VALUES ('a@b.c', 41), ('d@e.f', 12);",
    )
    original = _snapshot_of(path)
    changed = _retype("users", "age", "TEXT")(_models_of(copy.deepcopy(original)))
    upgrade_ops, rollback_ops = diff_models_against_snapshot(
        changed, copy.deepcopy(original), db_name=None,
    )
    upgrade, _, _ = snapshot_diff_to_sql(upgrade_ops, rollback_ops, db_name=None)

    connection = sqlite3.connect(path)
    connection.executescript(upgrade)
    assert connection.execute("SELECT id, email, age FROM users ORDER BY id").fetchall() == [
        (1, "a@b.c", "41"),
        (2, "d@e.f", "12"),
    ]


def test_a_rebuild_keeps_autoincrement(sqlite_backend, tmp_path):
    path = _build(
        tmp_path,
        "CREATE TABLE users (id INTEGER PRIMARY KEY AUTOINCREMENT, v INTEGER);",
        "INSERT INTO users (v) VALUES (1);",
    )
    original = _snapshot_of(path)
    changed = _retype("users", "v", "TEXT")(_models_of(copy.deepcopy(original)))
    upgrade_ops, rollback_ops = diff_models_against_snapshot(
        changed, copy.deepcopy(original), db_name=None,
    )
    upgrade, _, _ = snapshot_diff_to_sql(upgrade_ops, rollback_ops, db_name=None)

    connection = sqlite3.connect(path)
    connection.executescript(upgrade)
    ddl = connection.execute(
        "SELECT sql FROM sqlite_master WHERE name='users'"
    ).fetchone()[0]
    assert "AUTOINCREMENT" in ddl


def test_a_rebuild_keeps_foreign_key_actions(sqlite_backend, tmp_path):
    path = _build(
        tmp_path,
        "CREATE TABLE parent (id INTEGER PRIMARY KEY);"
        "CREATE TABLE child (id INTEGER PRIMARY KEY,"
        " pid INTEGER REFERENCES parent(id) ON DELETE CASCADE, v TEXT);",
        None,
    )
    original = _snapshot_of(path)
    changed = _retype("child", "v", "INTEGER")(_models_of(copy.deepcopy(original)))
    upgrade_ops, rollback_ops = diff_models_against_snapshot(
        changed, copy.deepcopy(original), db_name=None,
    )
    upgrade, _, _ = snapshot_diff_to_sql(upgrade_ops, rollback_ops, db_name=None)

    connection = sqlite3.connect(path)
    connection.executescript(upgrade)
    actions = connection.execute("PRAGMA foreign_key_list(child)").fetchall()
    assert actions and actions[0][6] == "CASCADE"


def test_a_rebuild_keeps_an_expression_index(sqlite_backend, tmp_path):
    path = _build(
        tmp_path,
        "CREATE TABLE t (id INTEGER PRIMARY KEY, a TEXT, v TEXT);"
        "CREATE INDEX ix_expr ON t (lower(a));",
        None,
    )
    original = _snapshot_of(path)
    changed = _retype("t", "v", "INTEGER")(_models_of(copy.deepcopy(original)))
    upgrade_ops, rollback_ops = diff_models_against_snapshot(
        changed, copy.deepcopy(original), db_name=None,
    )
    upgrade, _, _ = snapshot_diff_to_sql(upgrade_ops, rollback_ops, db_name=None)

    connection = sqlite3.connect(path)
    connection.executescript(upgrade)
    indexes = connection.execute(
        "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='t'"
    ).fetchall()
    assert ("ix_expr",) in indexes


def test_a_narrowing_change_a_strict_table_rejects_fails_loudly(sqlite_backend, tmp_path):
    """SQLite enforces STRICT during the copy; the migration must not paper over it."""
    path = _build(
        tmp_path,
        "CREATE TABLE st (id INTEGER PRIMARY KEY, n REAL) STRICT;",
        "INSERT INTO st VALUES (1, 1.5);",
    )
    original = _snapshot_of(path)
    changed = _retype("st", "n", "INTEGER")(_models_of(copy.deepcopy(original)))
    upgrade_ops, rollback_ops = diff_models_against_snapshot(
        changed, copy.deepcopy(original), db_name=None,
    )
    upgrade, _, _ = snapshot_diff_to_sql(upgrade_ops, rollback_ops, db_name=None)

    connection = sqlite3.connect(path)
    with pytest.raises(sqlite3.IntegrityError, match="cannot store REAL value"):
        connection.executescript(upgrade)


class TestGeneratedModelsAreImportable:
    """`generate-models` must not write a class with an empty body.

    Column metadata that carries nothing renderable - an extracted key with no
    `class Meta` equivalent - emitted `class id(SqColumnMeta):` followed by
    nothing. The file is then not Python: loading it raises "expected an
    indented block after class definition", so dbwarden could not read back the
    models it had just written.
    """

    @staticmethod
    def _render(columns, sq_meta=None):
        from dbwarden.engine.backends.sqlite.generate_models import _render_sqlite_meta

        return _render_sqlite_meta(columns, sq_meta)

    @staticmethod
    def _parses(lines):
        import ast
        import textwrap

        source = "class Model:\n" + "\n".join(lines) if lines else "class Model:\n    pass"
        ast.parse(textwrap.dedent(source))
        return True

    def test_unrenderable_column_meta_emits_nothing(self):
        lines = self._render([{"name": "id", "sq_meta": {"sq_declared_type": "INTEGER"}}])
        assert lines == []

    def test_a_table_with_no_renderable_meta_emits_nothing(self):
        assert self._render([{"name": "id"}], {}) == []

    def test_renderable_column_meta_still_emits_a_class(self):
        lines = self._render([{"name": "slug", "comment": "the slug"}])
        assert any("class slug(SqColumnMeta):" in line for line in lines)
        assert any("comment = 'the slug'" in line for line in lines)
        assert self._parses(lines)

    def test_a_mix_only_emits_the_columns_that_have_content(self):
        lines = self._render([
            {"name": "id", "sq_meta": {"sq_declared_type": "INTEGER"}},
            {"name": "slug", "comment": "the slug"},
        ])
        assert not any("class id(SqColumnMeta):" in line for line in lines)
        assert any("class slug(SqColumnMeta):" in line for line in lines)
        assert self._parses(lines)
