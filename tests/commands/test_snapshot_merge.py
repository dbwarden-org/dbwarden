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
