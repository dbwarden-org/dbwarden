"""Table constraints must survive every migration-generation path.

From a bug report: a generated initial migration contained no `uq_` at all —
every `UNIQUE` constraint declared in a model's `class Meta` was missing, so
`INSERT ... ON CONFLICT (branch_id)` failed at runtime with "there is no unique
or exclusion constraint matching the ON CONFLICT specification".

`make-migrations` has two generation paths. The snapshot path renders
constraints through ConstraintHandler; the fallback path — taken when there is
no schema snapshot, the database is unreachable, or the snapshot diff raises —
rendered only columns, indexes and foreign keys, dropping UNIQUE and CHECK
constraints silently.
"""

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from dbwarden.commands.make_migrations.pipeline import generate_migration_sql
from dbwarden.engine.core.models import (
    ModelColumn,
    ModelTable,
    column_unique_is_table_constraint,
)
from dbwarden.engine.snapshot import diff_models_against_snapshot, snapshot_diff_to_sql

EMPTY_SNAPSHOT = {"tables": {}, "indexes": {}, "constraints": {}, "enums": {}}


@pytest.fixture
def pg_backend(monkeypatch):
    config = SimpleNamespace(
        database_type="postgresql", model_paths=None, model_tables=None,
        sqlalchemy_url="postgresql://ignored/db",
    )
    monkeypatch.setattr(
        "dbwarden.engine.model_discovery.type_mapping.get_database",
        lambda db_name=None: config,
    )
    monkeypatch.setattr("dbwarden.config.get_database", lambda db_name=None: config)
    # pipeline.py imports these at module load, so patching the source module
    # alone would not rebind them.
    monkeypatch.setattr(
        "dbwarden.commands.make_migrations.pipeline.get_database",
        lambda db_name=None: config,
    )
    monkeypatch.setattr(
        "dbwarden.commands.make_migrations.pipeline.get_multi_db_config",
        lambda: SimpleNamespace(default="primary"),
    )
    return config


def _col(name, type_="varchar", *, nullable=True, pk=False, unique=False):
    return ModelColumn(
        name=name, type=type_, nullable=nullable, primary_key=pk, unique=unique,
        default=None, foreign_key=None,
    )


def _heartbeat_table(**kwargs):
    return ModelTable(
        name="sync_heartbeat",
        columns=[
            _col("id", "integer", nullable=False, pk=True),
            _col("branch_id", "varchar", nullable=False),
        ],
        **kwargs,
    )


def _fallback_sql(tables):
    """Generate through the path taken when no snapshot is available."""
    with patch("dbwarden.engine.snapshot.find_latest_snapshot", return_value=None), \
         patch(
             "dbwarden.engine.snapshot.extract_full_schema_snapshot",
             side_effect=OSError("database unreachable"),
         ), \
         patch("dbwarden.engine.discovery.extract_tables_from_database", return_value={}):
        upgrade, rollback, changes = generate_migration_sql(tables, None, None, None)
    return upgrade, rollback, changes


def _snapshot_sql(tables):
    """Generate through the normal snapshot-diff path."""
    upgrade_ops, rollback_ops = diff_models_against_snapshot(
        tables, dict(EMPTY_SNAPSHOT), db_name=None,
    )
    by_name = {t.name: t for t in tables}
    with patch(
        "dbwarden.engine.snapshot._find_model_table",
        lambda name, db_name=None: by_name.get(name),
    ):
        return snapshot_diff_to_sql(upgrade_ops, rollback_ops, db_name=None)


class TestFallbackPathEmitsConstraints:
    def test_named_unique_constraint_is_emitted(self, pg_backend):
        table = _heartbeat_table(
            uniques=[{"columns": ["branch_id"], "name": "uq_sync_heartbeat_branch_id"}],
        )
        upgrade, _, _ = _fallback_sql([table])
        assert (
            "ALTER TABLE sync_heartbeat ADD CONSTRAINT uq_sync_heartbeat_branch_id "
            "UNIQUE (branch_id);" in upgrade
        )

    def test_unique_constraint_is_dropped_on_rollback(self, pg_backend):
        table = _heartbeat_table(
            uniques=[{"columns": ["branch_id"], "name": "uq_sync_heartbeat_branch_id"}],
        )
        _, rollback, _ = _fallback_sql([table])
        assert "DROP CONSTRAINT uq_sync_heartbeat_branch_id" in rollback

    def test_unnamed_unique_gets_the_generated_name(self, pg_backend):
        table = _heartbeat_table(uniques=[{"columns": ["branch_id"]}])
        upgrade, _, _ = _fallback_sql([table])
        assert "ADD CONSTRAINT uq_sync_heartbeat_branch_id UNIQUE (branch_id);" in upgrade

    def test_multi_column_unique(self, pg_backend):
        table = ModelTable(
            name="delivery_zones",
            columns=[
                _col("id", "integer", nullable=False, pk=True),
                _col("branch_id", "varchar"),
                _col("location_id", "varchar"),
            ],
            uniques=[{
                "columns": ["branch_id", "location_id"],
                "name": "uq_delivery_zones_branch_location",
            }],
        )
        upgrade, _, _ = _fallback_sql([table])
        assert (
            "ADD CONSTRAINT uq_delivery_zones_branch_location "
            "UNIQUE (branch_id, location_id);" in upgrade
        )

    def test_deferrable_unique(self, pg_backend):
        table = _heartbeat_table(uniques=[{
            "columns": ["branch_id"], "name": "uq_h", "deferrable": True,
            "initially_deferred": True,
        }])
        upgrade, _, _ = _fallback_sql([table])
        assert "UNIQUE (branch_id) DEFERRABLE INITIALLY DEFERRED;" in upgrade

    def test_check_constraint_is_emitted(self, pg_backend):
        table = _heartbeat_table(
            checks=[{"name": "ck_sync_heartbeat_branch", "expression": "branch_id <> ''"}],
        )
        upgrade, rollback, _ = _fallback_sql([table])
        assert (
            "ALTER TABLE sync_heartbeat ADD CONSTRAINT ck_sync_heartbeat_branch "
            "CHECK (branch_id <> '');" in upgrade
        )
        assert "DROP CONSTRAINT IF EXISTS ck_sync_heartbeat_branch" in rollback

    def test_unnamed_check_gets_a_generated_name(self, pg_backend):
        table = _heartbeat_table(checks=[{"expression": "branch_id <> ''"}])
        upgrade, _, _ = _fallback_sql([table])
        assert "ADD CONSTRAINT ck_sync_heartbeat_0 CHECK (branch_id <> '');" in upgrade

    def test_constraints_are_reported_as_changes(self, pg_backend):
        table = _heartbeat_table(
            uniques=[{"columns": ["branch_id"], "name": "uq_h"}],
            checks=[{"name": "ck_h", "expression": "branch_id <> ''"}],
        )
        _, _, changes = _fallback_sql([table])
        operations = {c.operation for c in changes}
        assert "add_unique_constraint" in operations
        assert "add_check_constraint" in operations

    def test_constraints_only_for_newly_created_tables(self, pg_backend):
        """An existing table's constraints are the snapshot path's business."""
        table = _heartbeat_table(
            uniques=[{"columns": ["branch_id"], "name": "uq_sync_heartbeat_branch_id"}],
        )
        with patch("dbwarden.engine.snapshot.find_latest_snapshot", return_value=None), \
             patch(
                 "dbwarden.engine.snapshot.extract_full_schema_snapshot",
                 side_effect=OSError("database unreachable"),
             ), \
             patch(
                 "dbwarden.commands.make_migrations.pipeline.extract_tables_from_database",
                 return_value={"sync_heartbeat": {"id", "branch_id"}},
             ):
            upgrade, _, _ = generate_migration_sql([table], None, None, None)
        assert "ADD CONSTRAINT" not in upgrade


class TestSnapshotPathStillEmitsConstraints:
    def test_named_unique_constraint_is_emitted(self, pg_backend):
        table = _heartbeat_table(
            uniques=[{"columns": ["branch_id"], "name": "uq_sync_heartbeat_branch_id"}],
        )
        upgrade, _, _ = _snapshot_sql([table])
        assert (
            "ADD CONSTRAINT uq_sync_heartbeat_branch_id UNIQUE (branch_id);" in upgrade
        )

    def test_check_constraint_is_emitted(self, pg_backend):
        table = _heartbeat_table(
            checks=[{"name": "ck_sync_heartbeat_branch", "expression": "branch_id <> ''"}],
        )
        upgrade, _, _ = _snapshot_sql([table])
        assert "ADD CONSTRAINT ck_sync_heartbeat_branch CHECK" in upgrade


class TestNoDuplicateUniqueConstraint:
    """`unique=True` is collected as a table constraint; it must not also be inline."""

    def test_helper_matches_single_column_constraint(self):
        table = _heartbeat_table(uniques=[{"columns": ["branch_id"], "name": "uq_h"}])
        assert column_unique_is_table_constraint(table, "branch_id") is True
        assert column_unique_is_table_constraint(table, "id") is False

    def test_helper_ignores_multi_column_constraint(self):
        table = _heartbeat_table(uniques=[{"columns": ["branch_id", "id"]}])
        assert column_unique_is_table_constraint(table, "branch_id") is False

    def test_helper_handles_a_table_without_constraints(self):
        assert column_unique_is_table_constraint(_heartbeat_table(), "branch_id") is False

    def test_column_unique_is_not_emitted_twice(self, pg_backend):
        table = ModelTable(
            name="users",
            columns=[
                _col("id", "integer", nullable=False, pk=True),
                _col("email", "varchar", nullable=False, unique=True),
            ],
            uniques=[{"columns": ["email"]}],
        )
        upgrade, _, _ = _snapshot_sql([table])
        assert upgrade.count("UNIQUE") == 1
        assert "ADD CONSTRAINT uq_users_email UNIQUE (email);" in upgrade

    def test_column_unique_without_a_table_constraint_stays_inline(self, pg_backend):
        table = ModelTable(
            name="users",
            columns=[
                _col("id", "integer", nullable=False, pk=True),
                _col("email", "varchar", nullable=False, unique=True),
            ],
        )
        upgrade, _, _ = _snapshot_sql([table])
        assert "email varchar NOT NULL UNIQUE" in upgrade

    def test_sqlite_does_not_emit_it_twice(self, monkeypatch):
        config = SimpleNamespace(
            database_type="sqlite", model_paths=None, model_tables=None,
        )
        monkeypatch.setattr(
            "dbwarden.engine.model_discovery.type_mapping.get_database",
            lambda db_name=None: config,
        )
        monkeypatch.setattr("dbwarden.config.get_database", lambda db_name=None: config)

        from dbwarden.engine.backends.sqlite.sql_build import build_sqlite_create_table_sql

        table = ModelTable(
            name="users",
            columns=[
                _col("id", "integer", nullable=False, pk=True),
                _col("email", "TEXT", nullable=False, unique=True),
            ],
            uniques=[{"columns": ["email"], "name": "uq_users_email"}],
        )
        sql = build_sqlite_create_table_sql(table)
        assert sql.count("UNIQUE") == 1
        assert "CONSTRAINT uq_users_email UNIQUE (email)" in sql


class TestFallbackIsAnnounced:
    """Degrading to model-only generation must not be silent."""

    def test_a_failed_snapshot_diff_warns(self, pg_backend, monkeypatch):
        messages = []
        monkeypatch.setattr(
            "dbwarden.commands.make_migrations.pipeline.warning",
            lambda message: messages.append(message),
        )

        table = _heartbeat_table(
            uniques=[{"columns": ["branch_id"], "name": "uq_sync_heartbeat_branch_id"}],
        )
        with patch("dbwarden.engine.snapshot.find_latest_snapshot", return_value=None), \
             patch("dbwarden.engine.snapshot.extract_full_schema_snapshot", return_value=dict(EMPTY_SNAPSHOT)), \
             patch(
                 "dbwarden.engine.snapshot.diff_models_against_snapshot",
                 side_effect=RuntimeError("boom"),
             ), \
             patch("dbwarden.engine.discovery.extract_tables_from_database", return_value={}):
            upgrade, _, _ = generate_migration_sql([table], None, None, None)

        assert any("generating from models" in m for m in messages)
        # And the degraded output still carries the constraint.
        assert "ADD CONSTRAINT uq_sync_heartbeat_branch_id" in upgrade


class TestForeignKeyToANewTable:
    """A foreign key must survive even when the referenced table is plain.

    The add-foreign-key guard checks that the referenced table exists. It
    consulted the constraint handler's own model spec, which only listed tables
    that declared constraints — so a foreign key pointing at a table with
    nothing but columns and a primary key looked like a foreign key to a
    missing table, and was dropped without a word.
    """

    @staticmethod
    def _owners(with_constraint):
        return ModelTable(
            name="owners",
            columns=[
                _col("id", "integer", nullable=False, pk=True),
                _col("email", "varchar"),
            ],
            uniques=(
                [{"columns": ["email"], "name": "uq_owners_email"}]
                if with_constraint else []
            ),
        )

    @staticmethod
    def _pets(referred_table="owners", referred_columns=("id",)):
        return ModelTable(
            name="pets",
            columns=[
                _col("id", "integer", nullable=False, pk=True),
                _col("owner_id", "integer"),
            ],
            foreign_keys=[{
                "columns": ["owner_id"],
                "referred_table": referred_table,
                "referred_columns": list(referred_columns),
                "on_delete": "CASCADE",
            }],
        )

    def test_referenced_table_without_constraints(self, pg_backend):
        upgrade, _, _ = _snapshot_sql([self._owners(False), self._pets()])
        assert "FOREIGN KEY (owner_id) REFERENCES owners(id)" in upgrade
        assert "ON DELETE CASCADE" in upgrade

    def test_referenced_table_with_constraints(self, pg_backend):
        upgrade, _, _ = _snapshot_sql([self._owners(True), self._pets()])
        assert "FOREIGN KEY (owner_id) REFERENCES owners(id)" in upgrade

    def test_foreign_key_to_a_missing_table_is_still_skipped(self, pg_backend):
        upgrade, _, _ = _snapshot_sql([self._pets(referred_table="nonexistent")])
        assert "FOREIGN KEY" not in upgrade

    def test_foreign_key_to_a_missing_column_is_still_skipped(self, pg_backend):
        upgrade, _, _ = _snapshot_sql([
            self._owners(False), self._pets(referred_columns=("no_such_column",)),
        ])
        assert "FOREIGN KEY" not in upgrade


class TestFallbackPathEmitsExcludes:
    """EXCLUDE constraints are PgTableHandler's job, which the fallback lacks."""

    @staticmethod
    def _table():
        return ModelTable(
            name="bookings",
            columns=[
                _col("id", "integer", nullable=False, pk=True),
                _col("room", "varchar"),
            ],
            pg_table={"pg_excludes": [{
                "name": "ex_bookings_room", "expression": "room WITH =",
            }]},
        )

    def test_exclude_constraint_is_emitted(self, pg_backend):
        upgrade, _, _ = _fallback_sql([self._table()])
        assert "ADD CONSTRAINT ex_bookings_room EXCLUDE room WITH =;" in upgrade

    def test_exclude_constraint_is_dropped_on_rollback(self, pg_backend):
        _, rollback, _ = _fallback_sql([self._table()])
        assert "DROP CONSTRAINT ex_bookings_room" in rollback

    def test_an_expression_that_already_says_exclude_is_not_doubled(self, pg_backend):
        table = ModelTable(
            name="bookings",
            columns=[_col("id", "integer", nullable=False, pk=True), _col("room", "varchar")],
            pg_table={"pg_excludes": [{
                "name": "ex_bookings_room", "expression": "EXCLUDE USING gist (room WITH =)",
            }]},
        )
        upgrade, _, _ = _fallback_sql([table])
        assert upgrade.count("EXCLUDE") == 1


class TestNoDuplicateForeignKey:
    """A single-column FK reaches the model twice; it must be emitted once."""

    @staticmethod
    def _tables():
        owners = ModelTable(
            name="owners",
            columns=[_col("id", "integer", nullable=False, pk=True)],
        )
        pets = ModelTable(
            name="pets",
            columns=[
                _col("id", "integer", nullable=False, pk=True),
                ModelColumn(
                    name="owner_id", type="integer", nullable=True, primary_key=False,
                    unique=False, default=None, foreign_key="owners(id)",
                ),
            ],
            foreign_keys=[{
                "columns": ["owner_id"], "referred_table": "owners",
                "referred_columns": ["id"],
            }],
        )
        return [owners, pets]

    @pytest.mark.parametrize("backend", ["postgresql", "mysql", "mariadb"])
    def test_foreign_key_is_emitted_once(self, monkeypatch, backend):
        config = SimpleNamespace(
            database_type=backend, model_paths=None, model_tables=None,
            sqlalchemy_url="x://y",
        )
        monkeypatch.setattr(
            "dbwarden.engine.model_discovery.type_mapping.get_database",
            lambda db_name=None: config,
        )
        monkeypatch.setattr("dbwarden.config.get_database", lambda db_name=None: config)

        upgrade, _, _ = _snapshot_sql(self._tables())
        create_table_part = upgrade.split("ALTER TABLE")[0]
        assert "REFERENCES" not in create_table_part, (
            "the column definition repeats a foreign key that is also a table constraint"
        )
        assert upgrade.count("REFERENCES owners") == 1
