"""Regressions for spurious PostgreSQL migration operations.

Every case here comes from one bug report: adding a single new table produced a
126-operation migration that dropped and recreated constraints and indexes
across 41 unrelated tables, and emitted `ALTER TABLE t DROP COLUMN CONSTRAINT`.
"""

import copy

import pytest

from dbwarden.commands.make_migrations.snapshot_merge import (
    _merge_pending_migrations_into_snapshot,
)
from dbwarden.engine.backends.postgresql.extract import pg_index_sort_option
from dbwarden.engine.backends.postgresql.handlers import ColumnHandler, ConstraintHandler
from dbwarden.engine.backends.postgresql.sql_build import _build_pg_meta_sql
from dbwarden.engine.core.models import ModelColumn, ModelTable
from dbwarden.engine.snapshot import _normalize_default


def _col(name, type_="varchar", *, nullable=True, pk=False, default=None, pg_meta=None):
    return ModelColumn(
        name=name, type=type_, nullable=nullable, primary_key=pk, unique=False,
        default=default, foreign_key=None, pg_meta=pg_meta or {},
    )


def _write_migration(tmp_path, name, upgrade):
    path = tmp_path / name
    path.write_text(f"-- upgrade\n\n{upgrade}\n\n-- rollback\n\n-- none\n")
    return str(tmp_path)


class TestAddConstraintIsNotAColumn:
    """`ALTER TABLE t ADD CONSTRAINT ...` was read as a column named CONSTRAINT."""

    def test_add_constraint_does_not_become_a_column(self, tmp_path):
        migrations_dir = _write_migration(
            tmp_path, "0001_init.sql",
            "CREATE TABLE users (\n    id SERIAL PRIMARY KEY,\n    email VARCHAR(255)\n);\n\n"
            "ALTER TABLE users ADD CONSTRAINT uq_users_email UNIQUE (email);",
        )
        snapshot = {"tables": {}}
        _merge_pending_migrations_into_snapshot(snapshot, migrations_dir)

        columns = snapshot["tables"]["users"]["columns"]
        assert set(columns) == {"id", "email"}
        assert "CONSTRAINT" not in columns
        assert "constraint" not in columns

    @pytest.mark.parametrize(
        "statement",
        [
            "ALTER TABLE users ADD CONSTRAINT uq_users_email UNIQUE (email);",
            "ALTER TABLE users ADD PRIMARY KEY (id);",
            "ALTER TABLE users ADD FOREIGN KEY (org_id) REFERENCES orgs (id);",
            "ALTER TABLE users ADD UNIQUE (email);",
            "ALTER TABLE users ADD CHECK (email <> '');",
            "ALTER TABLE users ADD EXCLUDE USING gist (room WITH =);",
        ],
    )
    def test_no_table_level_addition_is_read_as_a_column(self, tmp_path, statement):
        migrations_dir = _write_migration(tmp_path, "0001_init.sql", statement)
        snapshot = {"tables": {}}
        _merge_pending_migrations_into_snapshot(snapshot, migrations_dir)
        assert snapshot["tables"] == {}

    def test_a_real_added_column_is_still_recorded(self, tmp_path):
        migrations_dir = _write_migration(
            tmp_path, "0001_init.sql",
            "ALTER TABLE users ADD COLUMN nickname VARCHAR(50);",
        )
        snapshot = {"tables": {}}
        _merge_pending_migrations_into_snapshot(snapshot, migrations_dir)
        assert set(snapshot["tables"]["users"]["columns"]) == {"nickname"}

    def test_a_column_added_without_the_keyword_is_still_recorded(self, tmp_path):
        migrations_dir = _write_migration(
            tmp_path, "0001_init.sql", "ALTER TABLE users ADD nickname VARCHAR(50);",
        )
        snapshot = {"tables": {}}
        _merge_pending_migrations_into_snapshot(snapshot, migrations_dir)
        assert set(snapshot["tables"]["users"]["columns"]) == {"nickname"}


class TestUnnamedUniqueConstraintIsNotRenamed:
    """A model constraint dbwarden named itself must not rename the database's."""

    @staticmethod
    def _snapshot():
        # Rebuilt per call: canonicalize() edits the snapshot in place.
        return {
            "constraints": {
                "users.users_email_key": {
                    "type": "unique", "name": "users_email_key",
                    "table": "users", "columns": ["email"],
                },
            },
        }

    def _diff(self, model_table):
        handler = ConstraintHandler()
        snap = handler.canonicalize(handler.extract(self._snapshot()))
        model = handler.canonicalize(handler.model_spec_from_tables([model_table]))
        return handler.diff(snap, model)

    def test_auto_named_constraint_produces_no_operations(self):
        table = ModelTable(
            name="users",
            columns=[_col("email")],
            uniques=[{"columns": ["email"]}],
        )
        upgrade, rollback = self._diff(table)
        assert upgrade == []
        assert rollback == []

    def test_column_level_unique_produces_no_operations(self):
        # `mapped_column(unique=True)` reaches the diff as an unnamed unique.
        table = ModelTable(
            name="users",
            columns=[_col("email")],
            uniques=[{"columns": ["email"], "deferrable": False, "initially_deferred": False}],
        )
        upgrade, _ = self._diff(table)
        assert upgrade == []

    def test_an_explicit_name_still_renames(self):
        table = ModelTable(
            name="users",
            columns=[_col("email")],
            uniques=[{"columns": ["email"], "name": "uq_users_email"}],
        )
        upgrade, _ = self._diff(table)
        assert [op.object_type for op in upgrade] == ["rename_unique_constraint"]
        assert upgrade[0].upgrade_attrs["old_name"] == "users_email_key"
        assert upgrade[0].upgrade_attrs["new_name"] == "uq_users_email"

    def test_a_genuinely_new_constraint_is_still_added(self):
        table = ModelTable(
            name="users",
            columns=[_col("email"), _col("tenant")],
            uniques=[{"columns": ["email"]}, {"columns": ["tenant"]}],
        )
        upgrade, _ = self._diff(table)
        assert [op.object_type for op in upgrade] == ["add_unique_constraint"]
        assert upgrade[0].upgrade_attrs["columns"] == ["tenant"]


class TestDefaultsWithCasts:
    """PostgreSQL reports defaults with a cast; the model spells them without."""

    @pytest.mark.parametrize(
        ("reported", "expected"),
        [
            ("'queued'::character varying", "queued"),
            ("'queued'::text", "queued"),
            ("'{}'::jsonb", "{}"),
            ("0::numeric", "0"),
            ("'a'::text::character varying", "a"),
        ],
    )
    def test_trailing_cast_is_ignored(self, reported, expected):
        assert _normalize_default(reported) == expected

    def test_a_cast_inside_an_expression_is_kept(self):
        assert _normalize_default("nextval('users_id_seq'::regclass)") == (
            "nextval('users_id_seq'::regclass)"
        )

    def test_unchanged_default_produces_no_operation(self):
        handler = ColumnHandler()
        snapshot = {
            "tables": {
                "invoices": {
                    "columns": {
                        "dgi_status": {
                            "type": "varchar",
                            "nullable": False,
                            "primary_key": False,
                            "default": "'queued'::character varying",
                        },
                    },
                    "primary_key": [],
                },
            },
        }
        model = ModelTable(
            name="invoices",
            columns=[_col("dgi_status", "varchar", nullable=False, default="'queued'")],
        )
        upgrade, _ = handler.diff(
            handler.canonicalize(handler.extract(snapshot)),
            handler.canonicalize(handler.model_spec_from_tables([model])),
        )
        assert [op.object_type for op in upgrade] == []


class TestStorageIsNotReset:
    """A model that says nothing about STORAGE must not reset the column."""

    def test_no_operation_when_the_model_is_silent(self):
        """The op itself must not be emitted, not merely render no SQL."""
        handler = ColumnHandler()
        snapshot = {
            "tables": {
                "sales": {
                    "columns": {
                        "notes": {
                            "type": "text", "nullable": True, "primary_key": False,
                            "default": None,
                            "pg_column": {"storage": "MAIN"},
                        },
                    },
                    "primary_key": [],
                },
            },
        }
        table = ModelTable(name="sales", columns=[_col("notes", "text")])
        upgrade, _ = handler.diff(
            handler.canonicalize(handler.extract(snapshot)),
            handler.canonicalize(handler.model_spec_from_tables([table])),
        )
        assert [op.object_type for op in upgrade] == []

    def test_an_explicit_model_preference_is_still_diffed(self):
        handler = ColumnHandler()
        snapshot = {
            "tables": {
                "sales": {
                    "columns": {
                        "notes": {
                            "type": "text", "nullable": True, "primary_key": False,
                            "default": None,
                        },
                    },
                    "primary_key": [],
                },
            },
        }
        table = ModelTable(
            name="sales",
            columns=[_col("notes", "text", pg_meta={"pg_storage": "MAIN"})],
        )
        upgrade, _ = handler.diff(
            handler.canonicalize(handler.extract(snapshot)),
            handler.canonicalize(handler.model_spec_from_tables([table])),
        )
        assert [op.object_type for op in upgrade] == ["alter_pg_column_meta"]

    def test_no_statement_when_the_model_has_no_preference(self):
        stmts = _build_pg_meta_sql(
            "sales", "subtotal", "numeric", "numeric",
            to_pg_column={},
            from_pg_column={"storage": "MAIN"},
            backend="postgresql",
        )
        assert stmts == []

    def test_no_statement_for_compression_either(self):
        stmts = _build_pg_meta_sql(
            "sales", "notes", "text", "text",
            to_pg_column={},
            from_pg_column={"compression": "lz4"},
            backend="postgresql",
        )
        assert stmts == []

    def test_an_explicit_preference_is_still_applied(self):
        stmts = _build_pg_meta_sql(
            "sales", "subtotal", "numeric", "numeric",
            to_pg_column={"pg_storage": "MAIN"},
            from_pg_column={},
            backend="postgresql",
        )
        assert len(stmts) == 1
        assert "SET STORAGE MAIN" in stmts[0].upgrade_sql


class TestIndexSortOptions:
    """Only non-default sort options belong in the snapshot."""

    @pytest.mark.parametrize(
        ("is_asc", "nulls_first", "expected"),
        [
            (True, False, None),          # ASC NULLS LAST is the default
            (False, True, "DESC"),        # DESC NULLS FIRST is the default
            (True, True, "NULLS FIRST"),
            (False, False, "DESC NULLS LAST"),
            (None, None, None),
        ],
    )
    def test_default_sort_options_are_not_recorded(self, is_asc, nulls_first, expected):
        assert pg_index_sort_option(is_asc, nulls_first) == expected


class TestForeignKeyMatching:
    """A foreign key that only differs in spelling must not be recreated."""

    @staticmethod
    def _snapshot():
        # Rebuilt per call: canonicalize() edits the snapshot in place.
        return {
            "tables": {
                "users": {"columns": {"id": {"type": "integer"}}, "primary_key": ["id"]},
                "orders": {"columns": {"user_id": {"type": "integer"}}, "primary_key": []},
            },
            "constraints": {
                "orders.orders_user_id_fkey": {
                    "type": "foreign_key", "name": "orders_user_id_fkey",
                    "table": "orders", "columns": ["user_id"],
                    "referenced_table": "users", "referenced_columns": ["id"],
                    "on_delete": "CASCADE", "on_update": "NO ACTION",
                    "deferrable": False,
                },
            },
        }

    def _diff(self, foreign_keys):
        handler = ConstraintHandler()
        snapshot = self._snapshot()
        # The add half checks that the referenced table exists in the snapshot.
        handler._snapshot = snapshot
        table = ModelTable(
            name="orders", columns=[_col("user_id", "integer")],
            foreign_keys=foreign_keys,
        )
        snap = handler.canonicalize(handler.extract(snapshot))
        model = handler.canonicalize(handler.model_spec_from_tables([table]))
        return handler.diff(snap, model)

    def test_lowercase_action_matches(self):
        upgrade, _ = self._diff([{
            "columns": ["user_id"], "referred_table": "users",
            "referred_columns": ["id"], "on_delete": "cascade",
        }])
        assert upgrade == []

    def test_omitted_action_matches_no_action(self):
        upgrade, _ = self._diff([{
            "columns": ["user_id"], "referred_table": "users",
            "referred_columns": ["id"], "on_delete": "CASCADE",
        }])
        assert upgrade == []

    def test_a_real_action_change_is_still_detected(self):
        upgrade, _ = self._diff([{
            "columns": ["user_id"], "referred_table": "users",
            "referred_columns": ["id"], "on_delete": "SET NULL",
        }])
        assert {op.object_type for op in upgrade} == {"drop_foreign_key", "add_foreign_key"}


class TestCanonicalizeDoesNotMutateTheSnapshot:
    """extract() hands back the snapshot's own dicts; canonicalize must copy."""

    def test_snapshot_constraints_survive_a_diff(self):
        snapshot = {
            "constraints": {
                "orders.orders_user_id_fkey": {
                    "type": "foreign_key", "name": "orders_user_id_fkey",
                    "table": "orders", "columns": ["user_id"],
                    "referenced_table": "users", "referenced_columns": ["id"],
                    "on_delete": "CASCADE", "on_update": "NO ACTION",
                },
                "users.users_email_key": {
                    "type": "unique", "name": "users_email_key",
                    "table": "users", "columns": ["email"],
                },
            },
        }
        before = copy.deepcopy(snapshot)
        handler = ConstraintHandler()
        handler.canonicalize(handler.extract(snapshot))
        assert snapshot == before


class TestColumnLevelUniqueIsSeen:
    """`unique=True` was only read when the model also declared a class Meta."""

    @staticmethod
    def _extract(model_cls):
        from dbwarden.engine.model_discovery.extraction import extract_table_from_model

        return extract_table_from_model(model_cls)

    def test_unique_column_without_a_meta_class(self):
        from sqlalchemy import Integer, String
        from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

        class Base(DeclarativeBase):
            pass

        class Plain(Base):
            __tablename__ = "dbw_plain_unique"

            id: Mapped[int] = mapped_column(Integer, primary_key=True)
            email: Mapped[str] = mapped_column(String(255), unique=True)

        table = self._extract(Plain)
        assert table is not None
        assert [u["columns"] for u in table.uniques] == [["email"]]

    def test_a_database_constraint_is_not_dropped(self):
        """The whole point: the model must carry the constraint the DB has."""
        from sqlalchemy import Integer, String
        from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

        class Base(DeclarativeBase):
            pass

        class Plain(Base):
            __tablename__ = "dbw_users"

            id: Mapped[int] = mapped_column(Integer, primary_key=True)
            email: Mapped[str] = mapped_column(String(255), unique=True)

        snapshot = {
            "constraints": {
                "dbw_users.dbw_users_email_key": {
                    "type": "unique", "name": "dbw_users_email_key",
                    "table": "dbw_users", "columns": ["email"],
                },
            },
        }
        handler = ConstraintHandler()
        upgrade, _ = handler.diff(
            handler.canonicalize(handler.extract(snapshot)),
            handler.canonicalize(handler.model_spec_from_tables([self._extract(Plain)])),
        )
        assert upgrade == []


class TestSerialAutoincrement:
    """An autoincrementing INTEGER key is rendered SERIAL before the diff runs."""

    def _diff(self, model_type):
        handler = ColumnHandler()
        snapshot = {
            "tables": {
                "t": {
                    "columns": {
                        "id": {
                            "type": "integer", "nullable": False, "primary_key": True,
                            "autoincrement": True, "default": "nextval('t_id_seq'::regclass)",
                        },
                    },
                    "primary_key": ["id"],
                },
            },
        }
        column = ModelColumn(
            name="id", type=model_type, nullable=False, primary_key=True,
            unique=False, default=None, foreign_key=None, autoincrement="auto",
        )
        table = ModelTable(name="t", columns=[column])
        return handler.diff(
            handler.canonicalize(handler.extract(snapshot)),
            handler.canonicalize(handler.model_spec_from_tables([table])),
        )

    @pytest.mark.parametrize("model_type", ["SERIAL", "BIGSERIAL", "INTEGER"])
    def test_no_autoincrement_change_for_an_integer_key(self, model_type):
        upgrade, _ = self._diff(model_type)
        assert [op.object_type for op in upgrade if "autoincrement" in op.object_type] == []


class TestUnnamedCheckConstraintIsNotRecreated:
    """A CHECK the model did not name keeps the database's name.

    PostgreSQL names an inline `CHECK (age >= 0)` itself - `t_age_check` - while
    dbwarden's generated name is index-based, `ck_t_0`. Comparing by name meant
    every unnamed check was dropped and recreated on every run, and reordering
    the model's checks renamed them. They are matched by expression instead,
    with PostgreSQL's casts and parentheses normalized away.
    """

    @staticmethod
    def _snapshot():
        return {
            "constraints": {
                "t.t_age_check": {
                    "type": "check", "name": "t_age_check", "table": "t",
                    "expression": "age >= 0",
                },
                "t.t_email_check": {
                    "type": "check", "name": "t_email_check", "table": "t",
                    "expression": "email <> ''::text",
                },
            },
        }

    def _diff(self, checks):
        handler = ConstraintHandler()
        table = ModelTable(
            name="t",
            columns=[_col("age", "integer"), _col("email", "text")],
            checks=checks,
        )
        snap = handler.canonicalize(handler.extract(self._snapshot()))
        model = handler.canonicalize(handler.model_spec_from_tables([table]))
        upgrade, _ = handler.diff(snap, model)
        return [op.object_type for op in upgrade if "check" in op.object_type]

    def test_matching_unnamed_checks_produce_no_operations(self):
        assert self._diff([
            {"expression": "age >= 0"}, {"expression": "email <> ''"},
        ]) == []

    def test_a_cast_in_the_stored_expression_is_ignored(self):
        assert self._diff([{"expression": "email <> ''"}]) == ["drop_check_constraint"], (
            "only the check the model dropped should be dropped"
        )

    def test_reordering_checks_produces_no_operations(self):
        assert self._diff([
            {"expression": "email <> ''"}, {"expression": "age >= 0"},
        ]) == []

    def test_a_changed_expression_is_still_detected(self):
        operations = self._diff([
            {"expression": "age >= 18"}, {"expression": "email <> ''"},
        ])
        assert set(operations) == {"drop_check_constraint", "add_check_constraint"}

    def test_a_new_check_is_still_added(self):
        operations = self._diff([
            {"expression": "age >= 0"}, {"expression": "email <> ''"},
            {"expression": "age < 200"},
        ])
        assert operations == ["add_check_constraint"]

    def test_an_explicitly_named_check_is_still_renamed(self):
        operations = self._diff([
            {"name": "ck_my_age", "expression": "age >= 0"},
            {"expression": "email <> ''"},
        ])
        assert set(operations) == {"drop_check_constraint", "add_check_constraint"}

    @pytest.mark.parametrize(
        ("stored", "declared"),
        [
            ("(age >= 0)", "age >= 0"),
            ("((age >= 0))", "age >= 0"),
            ("email <> ''::text", "email <> ''"),
            ("(email <> ''::character varying)", "email <> ''"),
            ("age >= 0", "age  >=  0"),
        ],
    )
    def test_expression_normalization(self, stored, declared):
        from dbwarden.engine.backends.postgresql.handlers.constraint_handler import (
            _normalize_check_expression,
        )

        assert _normalize_check_expression(stored) == _normalize_check_expression(declared)

    def test_normalization_keeps_different_expressions_apart(self):
        from dbwarden.engine.backends.postgresql.handlers.constraint_handler import (
            _normalize_check_expression,
        )

        assert _normalize_check_expression("age >= 0") != _normalize_check_expression("age >= 18")
