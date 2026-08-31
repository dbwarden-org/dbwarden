from __future__ import annotations

import pytest

from dbwarden.commands.make_migrations.snapshot_merge import _merge_pending_migrations_into_snapshot


class TestSnapshotMergeAlterAddColumn:
    def test_add_constraint_is_not_treated_as_add_column(self):
        """Regression: ALTER TABLE ... ADD CONSTRAINT must not inject a fake 'constraint' column."""
        snapshot = {
            "tables": {
                "orders": {
                    "columns": {
                        "id": {"type": "integer"},
                        "event_id": {"type": "integer"},
                    },
                    "primary_key": ["id"],
                },
            },
        }
        import tempfile, os
        with tempfile.TemporaryDirectory() as tmpdir:
            with open(os.path.join(tmpdir, "0001_add_fk.sql"), "w") as f:
                f.write("-- upgrade\n\nALTER TABLE orders ADD CONSTRAINT fk_orders_event_id FOREIGN KEY (event_id) REFERENCES events(id);\n")
            _merge_pending_migrations_into_snapshot(snapshot, tmpdir)

        assert "constraint" not in snapshot["tables"]["orders"]["columns"]
        assert set(snapshot["tables"]["orders"]["columns"].keys()) == {"id", "event_id"}

    def test_add_column_still_merged(self):
        snapshot = {
            "tables": {
                "orders": {
                    "columns": {"id": {"type": "integer"}},
                    "primary_key": ["id"],
                },
            },
        }
        import tempfile, os
        with tempfile.TemporaryDirectory() as tmpdir:
            with open(os.path.join(tmpdir, "0001_add_col.sql"), "w") as f:
                f.write("-- upgrade\n\nALTER TABLE orders ADD COLUMN quantity integer NOT NULL;\n")
            _merge_pending_migrations_into_snapshot(snapshot, tmpdir)

        assert "quantity" in snapshot["tables"]["orders"]["columns"]


class TestSnapshotMergeFollowsDropsAndRenames:
    """A migration's staging tables must not survive the merge.

    SQLite cannot drop a constraint in place, so dbwarden rebuilds the table:
    create ``t__dbw_new``, copy, drop ``t``, rename the staging table over it.
    The merge used to record only the CREATE, leaving a table nothing declares.
    The next diff then proposed to drop it, in SQL built from columns typed
    "unknown" - a migration nobody asked for, generated forever.
    """

    REBUILD_SQL = (
        "-- upgrade\n\n"
        "CREATE TABLE heartbeats__dbw_new (\n"
        "    id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,\n"
        "    branch_id INTEGER NOT NULL\n"
        ");\n\n"
        "INSERT INTO heartbeats__dbw_new (id, branch_id) SELECT id, branch_id FROM heartbeats;\n\n"
        "DROP TABLE heartbeats;\n\n"
        "ALTER TABLE heartbeats__dbw_new RENAME TO heartbeats;\n"
    )

    @staticmethod
    def _merge(snapshot, sql):
        import os
        import tempfile

        with tempfile.TemporaryDirectory() as tmpdir:
            with open(os.path.join(tmpdir, "0002_rebuild.sql"), "w") as handle:
                handle.write(sql)
            _merge_pending_migrations_into_snapshot(snapshot, tmpdir)
        return snapshot

    def test_staging_table_does_not_survive_the_rebuild(self):
        snapshot = {
            "tables": {
                "heartbeats": {
                    "columns": {"id": {"type": "integer"}, "branch_id": {"type": "integer"}},
                    "primary_key": ["id"],
                },
            },
        }
        self._merge(snapshot, self.REBUILD_SQL)

        assert "heartbeats__dbw_new" not in snapshot["tables"]

    def test_the_real_table_keeps_its_own_definition(self):
        snapshot = {
            "tables": {
                "heartbeats": {
                    "columns": {"id": {"type": "integer"}, "branch_id": {"type": "integer"}},
                    "primary_key": ["id"],
                },
            },
        }
        self._merge(snapshot, self.REBUILD_SQL)

        columns = snapshot["tables"]["heartbeats"]["columns"]
        assert columns["id"]["type"] == "integer", "the staging entry overwrote the live one"
        assert snapshot["tables"]["heartbeats"]["primary_key"] == ["id"]

    def test_a_renamed_new_table_is_still_merged_under_its_new_name(self):
        snapshot: dict = {"tables": {}}
        self._merge(
            snapshot,
            "-- upgrade\n\nCREATE TABLE staging (id INTEGER);\n\n"
            "ALTER TABLE staging RENAME TO arrivals;\n",
        )

        assert "staging" not in snapshot["tables"]
        assert "id" in snapshot["tables"]["arrivals"]["columns"]

    def test_a_table_the_database_reported_is_never_dropped_by_the_merge(self):
        """The merge adds what migrations create; it does not delete live state.

        A migration file may still be pending, but the snapshot is what the
        server answered, and a drop recorded there would hide a real table.
        """
        snapshot = {"tables": {"orders": {"columns": {"id": {"type": "integer"}}}}}
        self._merge(snapshot, "-- upgrade\n\nDROP TABLE orders;\n")

        assert "orders" in snapshot["tables"]
