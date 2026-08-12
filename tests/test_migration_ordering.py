from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


# =============================================================================
# resolve_migration_order tests
# =============================================================================

def _make_migration_file(directory: str, db_name: str, version: str, desc: str, depends_on: list[str] | None = None, is_seed: bool = False) -> str:
    filename = f"{db_name}__{version}_{desc}.sql"
    filepath = os.path.join(directory, filename)
    lines = []
    if depends_on:
        lines.append(f"-- depends_on: {json.dumps(depends_on)}")
    if is_seed:
        lines.append("-- seed")
    lines.append("")
    lines.append("-- upgrade")
    lines.append("")
    lines.append(f"SELECT 1;")
    lines.append("")
    lines.append("-- rollback")
    lines.append("")
    lines.append("SELECT 1;")
    with open(filepath, "w") as f:
        f.write("\n".join(lines))
    return filepath


class TestGetAllMigrationsWithMetadata:
    def test_empty_directory(self):
        from dbwarden.engine.version import get_all_migrations_with_metadata

        with tempfile.TemporaryDirectory() as tmpdir:
            result = get_all_migrations_with_metadata(tmpdir)
            assert result == []

    def test_nonexistent_directory(self):
        from dbwarden.engine.version import get_all_migrations_with_metadata

        result = get_all_migrations_with_metadata("/nonexistent/path")
        assert result == []

    def test_returns_metadata(self):
        from dbwarden.engine.version import get_all_migrations_with_metadata

        with tempfile.TemporaryDirectory() as tmpdir:
            _make_migration_file(tmpdir, "db", "0001", "init", depends_on=[], is_seed=False)
            _make_migration_file(tmpdir, "db", "0002", "add_users", depends_on=["0001"], is_seed=True)
            result = get_all_migrations_with_metadata(tmpdir)
            assert len(result) == 2
            versions = {r[0] for r in result}
            assert versions == {"0001", "0002"}
            for ver, fp, deps, seed in result:
                if ver == "0001":
                    assert deps == []
                    assert seed is False
                elif ver == "0002":
                    assert deps == ["0001"]
                    assert seed is True

    def test_ignores_non_migration_files(self):
        from dbwarden.engine.version import get_all_migrations_with_metadata

        with tempfile.TemporaryDirectory() as tmpdir:
            Path(tmpdir, "README.md").touch()
            Path(tmpdir, "random.sql").touch()
            _make_migration_file(tmpdir, "db", "0001", "init")
            result = get_all_migrations_with_metadata(tmpdir)
            assert len(result) == 1


class TestResolveMigrationOrder:
    def test_empty_directory(self):
        from dbwarden.engine.version import resolve_migration_order

        with patch("dbwarden.engine.version.get_all_migrations_with_metadata", return_value=[]):
            result = resolve_migration_order("/tmp/x", set())
            assert result == []

    def test_all_already_applied(self):
        from dbwarden.engine.version import resolve_migration_order

        def fake_meta(_dir):
            return [("0001", "/tmp/1.sql", [], False)]

        with patch("dbwarden.engine.version.get_all_migrations_with_metadata", side_effect=fake_meta):
            result = resolve_migration_order("/tmp/x", {"0001"})
            assert result == []

    def test_linear_chain(self):
        from dbwarden.engine.version import resolve_migration_order

        def fake_meta(_dir):
            return [
                ("0001", "/tmp/1.sql", [], False),
                ("0002", "/tmp/2.sql", ["0001"], False),
                ("0003", "/tmp/3.sql", ["0002"], False),
            ]

        with patch("dbwarden.engine.version.get_all_migrations_with_metadata", side_effect=fake_meta):
            result = resolve_migration_order("/tmp/x", set())
            assert len(result) == 3
            versions = [r[0] for r in result]
            assert versions == ["0001", "0002", "0003"]

    def test_diamond_dependency(self):
        from dbwarden.engine.version import resolve_migration_order

        def fake_meta(_dir):
            return [
                ("0001", "/tmp/1.sql", [], False),
                ("0002", "/tmp/2.sql", ["0001"], False),
                ("0003", "/tmp/3.sql", ["0001"], False),
                ("0004", "/tmp/4.sql", ["0002", "0003"], False),
            ]

        with patch("dbwarden.engine.version.get_all_migrations_with_metadata", side_effect=fake_meta):
            result = resolve_migration_order("/tmp/x", set())
            assert len(result) == 4
            # 0001 must be first, 0004 must be last
            assert result[0][0] == "0001"
            assert result[-1][0] == "0004"

    def test_dependency_on_applied_version(self):
        from dbwarden.engine.version import resolve_migration_order

        def fake_meta(_dir):
            return [
                ("0001", "/tmp/1.sql", [], False),
                ("0002", "/tmp/2.sql", ["0001"], False),
                ("0003", "/tmp/3.sql", ["0002"], False),
            ]

        with patch("dbwarden.engine.version.get_all_migrations_with_metadata", side_effect=fake_meta):
            # 0001 is already applied
            result = resolve_migration_order("/tmp/x", {"0001"})
            assert len(result) == 2
            assert result[0][0] == "0002"
            assert result[1][0] == "0003"

    def test_partial_apply(self):
        from dbwarden.engine.version import resolve_migration_order

        def fake_meta(_dir):
            return [
                ("0001", "/tmp/1.sql", [], False),
                ("0002", "/tmp/2.sql", ["0001"], False),
                ("0003", "/tmp/3.sql", ["0002"], False),
            ]

        with patch("dbwarden.engine.version.get_all_migrations_with_metadata", side_effect=fake_meta):
            result = resolve_migration_order("/tmp/x", {"0001", "0002"})
            assert len(result) == 1
            assert result[0][0] == "0003"

    def test_no_dependencies(self):
        from dbwarden.engine.version import resolve_migration_order

        def fake_meta(_dir):
            return [
                ("0001", "/tmp/1.sql", [], False),
                ("0002", "/tmp/2.sql", [], False),
                ("0003", "/tmp/3.sql", [], False),
            ]

        with patch("dbwarden.engine.version.get_all_migrations_with_metadata", side_effect=fake_meta):
            result = resolve_migration_order("/tmp/x", set())
            assert len(result) == 3

    def test_circular_dependency_raises(self):
        from dbwarden.engine.version import resolve_migration_order

        def fake_meta(_dir):
            return [
                ("0001", "/tmp/1.sql", ["0002"], False),
                ("0002", "/tmp/2.sql", ["0001"], False),
            ]

        with patch("dbwarden.engine.version.get_all_migrations_with_metadata", side_effect=fake_meta):
            with pytest.raises(ValueError, match="Cannot resolve migration dependencies"):
                resolve_migration_order("/tmp/x", set())

    def test_missing_dependency_raises(self):
        from dbwarden.engine.version import resolve_migration_order

        def fake_meta(_dir):
            return [
                ("0002", "/tmp/2.sql", ["0001"], False),
            ]

        with patch("dbwarden.engine.version.get_all_migrations_with_metadata", side_effect=fake_meta):
            with pytest.raises(ValueError, match="Cannot resolve migration dependencies"):
                resolve_migration_order("/tmp/x", set())

    def test_disconnected_subgraphs(self):
        from dbwarden.engine.version import resolve_migration_order

        def fake_meta(_dir):
            return [
                ("0001", "/tmp/1.sql", [], False),
                ("0002", "/tmp/2.sql", [], False),
            ]

        with patch("dbwarden.engine.version.get_all_migrations_with_metadata", side_effect=fake_meta):
            result = resolve_migration_order("/tmp/x", set())
            assert len(result) == 2

    def test_mixed_seed_and_normal(self):
        from dbwarden.engine.version import resolve_migration_order

        def fake_meta(_dir):
            return [
                ("0001", "/tmp/1.sql", [], False),
                ("0002", "/tmp/2.sql", ["0001"], True),
                ("0003", "/tmp/3.sql", [], False),
            ]

        with patch("dbwarden.engine.version.get_all_migrations_with_metadata", side_effect=fake_meta):
            result = resolve_migration_order("/tmp/x", set())
            assert len(result) == 3


# =============================================================================
# Repeatable migration filepath tests
# =============================================================================

class TestGetRunsAlwaysFilepaths:
    def test_empty_directory(self):
        from dbwarden.engine.version import get_runs_always_filepaths

        with tempfile.TemporaryDirectory() as tmpdir:
            result = get_runs_always_filepaths(tmpdir)
            assert result == []

    def test_nonexistent_directory(self):
        from dbwarden.engine.version import get_runs_always_filepaths

        result = get_runs_always_filepaths("/nonexistent")
        assert result == []

    def test_returns_ra_files_only(self):
        from dbwarden.engine.version import get_runs_always_filepaths

        with tempfile.TemporaryDirectory() as tmpdir:
            ra_path = Path(tmpdir, "db__RA__seed_data.sql")
            ra_path.touch()
            Path(tmpdir, "db__0001_normal.sql").touch()
            Path(tmpdir, "db__ROC__on_change.sql").touch()
            result = get_runs_always_filepaths(tmpdir)
            assert len(result) == 1
            assert result[0] == str(ra_path)

    def test_sorts_alphabetically(self):
        from dbwarden.engine.version import get_runs_always_filepaths

        with tempfile.TemporaryDirectory() as tmpdir:
            Path(tmpdir, "db__RA__z_last.sql").touch()
            Path(tmpdir, "db__RA__a_first.sql").touch()
            result = get_runs_always_filepaths(tmpdir)
            assert "a_first" in result[0]
            assert "z_last" in result[1]


class TestGetRunsOnChangeFilepaths:
    def test_empty_directory(self):
        from dbwarden.engine.version import get_runs_on_change_filepaths

        with tempfile.TemporaryDirectory() as tmpdir:
            result = get_runs_on_change_filepaths(tmpdir)
            assert result == []

    def test_nonexistent_directory(self):
        from dbwarden.engine.version import get_runs_on_change_filepaths

        result = get_runs_on_change_filepaths("/nonexistent")
        assert result == []

    def test_returns_roc_files(self):
        from dbwarden.engine.version import get_runs_on_change_filepaths

        with tempfile.TemporaryDirectory() as tmpdir:
            roc_path = Path(tmpdir, "db__ROC__refresh.sql")
            roc_path.touch()
            Path(tmpdir, "db__0001_normal.sql").touch()
            Path(tmpdir, "db__RA__always.sql").touch()
            result = get_runs_on_change_filepaths(tmpdir)
            assert len(result) == 1
            assert result[0] == str(roc_path)

    def test_changed_only_no_prior_checksums(self):
        from dbwarden.engine.version import get_runs_on_change_filepaths

        with tempfile.TemporaryDirectory() as tmpdir:
            roc_path = Path(tmpdir, "db__ROC__refresh.sql")
            roc_path.write_text("-- upgrade\nSELECT 1;\n-- rollback\nSELECT 1;")

            with patch(
                "dbwarden.repositories.get_existing_runs_on_change_filenames_to_checksums",
                return_value={},
            ):
                result = get_runs_on_change_filepaths(tmpdir, changed_only=True)
                assert len(result) == 1  # not in existing checksums -> included

    def test_changed_only_with_matching_checksum(self):
        from dbwarden.engine.version import get_runs_on_change_filepaths

        with tempfile.TemporaryDirectory() as tmpdir:
            roc_path = Path(tmpdir, "db__ROC__refresh.sql")
            roc_path.write_text("-- upgrade\nSELECT 1;\n-- rollback\nSELECT 1;")

            with (
                patch(
                    "dbwarden.repositories.get_existing_runs_on_change_filenames_to_checksums",
                    return_value={"db__ROC__refresh.sql": "abc"},
                ),
                patch("dbwarden.engine.checksum.calculate_checksum", return_value="abc"),
            ):
                result = get_runs_on_change_filepaths(tmpdir, changed_only=True)
                assert len(result) == 0  # checksum matches -> excluded

    def test_changed_only_with_differing_checksum(self):
        from dbwarden.engine.version import get_runs_on_change_filepaths

        with tempfile.TemporaryDirectory() as tmpdir:
            roc_path = Path(tmpdir, "db__ROC__refresh.sql")
            roc_path.write_text("-- upgrade\nSELECT 1;\n-- rollback\nSELECT 1;")

            with (
                patch(
                    "dbwarden.repositories.get_existing_runs_on_change_filenames_to_checksums",
                    return_value={"db__ROC__refresh.sql": "old_checksum"},
                ),
                patch("dbwarden.engine.checksum.calculate_checksum", return_value="new_checksum"),
            ):
                result = get_runs_on_change_filepaths(tmpdir, changed_only=True)
                assert len(result) == 1  # checksum differs -> included

    def test_changed_only_false_returns_all(self):
        from dbwarden.engine.version import get_runs_on_change_filepaths

        with tempfile.TemporaryDirectory() as tmpdir:
            Path(tmpdir, "db__ROC__a.sql").touch()
            Path(tmpdir, "db__ROC__b.sql").touch()
            result = get_runs_on_change_filepaths(tmpdir, changed_only=False)
            assert len(result) == 2


class TestGetAllRepeatableFilepaths:
    def test_returns_both_types(self):
        from dbwarden.engine.version import get_all_repeatable_filepaths

        with tempfile.TemporaryDirectory() as tmpdir:
            Path(tmpdir, "db__RA__always.sql").touch()
            Path(tmpdir, "db__ROC__change.sql").touch()
            result = get_all_repeatable_filepaths(tmpdir)
            assert "runs_always" in result
            assert "runs_on_change" in result
            assert len(result["runs_always"]) == 1
            assert len(result["runs_on_change"]) == 1

    def test_only_runs_always(self):
        from dbwarden.engine.version import get_all_repeatable_filepaths

        with tempfile.TemporaryDirectory() as tmpdir:
            Path(tmpdir, "db__RA__always.sql").touch()
            result = get_all_repeatable_filepaths(tmpdir)
            assert len(result["runs_always"]) == 1
            assert len(result["runs_on_change"]) == 0

    def test_only_runs_on_change(self):
        from dbwarden.engine.version import get_all_repeatable_filepaths

        with tempfile.TemporaryDirectory() as tmpdir:
            Path(tmpdir, "db__ROC__change.sql").touch()
            result = get_all_repeatable_filepaths(tmpdir)
            assert len(result["runs_always"]) == 0
            assert len(result["runs_on_change"]) == 1

    def test_empty_dir(self):
        from dbwarden.engine.version import get_all_repeatable_filepaths

        with tempfile.TemporaryDirectory() as tmpdir:
            result = get_all_repeatable_filepaths(tmpdir)
            assert len(result["runs_always"]) == 0
            assert len(result["runs_on_change"]) == 0


class TestGenerateRepeatableFilename:
    def test_generates_ra_filename(self):
        from dbwarden.engine.version import generate_repeatable_filename

        result = generate_repeatable_filename("primary", "seed data", "RA__")
        assert result == "primary__RA__seed_data.sql"

    def test_generates_roc_filename(self):
        from dbwarden.engine.version import generate_repeatable_filename

        result = generate_repeatable_filename("primary", "refresh materialized", "ROC__")
        assert result == "primary__ROC__refresh_materialized.sql"

    def test_sanitizes_description(self):
        from dbwarden.engine.version import generate_repeatable_filename

        result = generate_repeatable_filename("db", "Add  USER! data?", "RA__")
        assert result == "db__RA__add_user_data.sql"
def test_migration_file_symlink_is_rejected(tmp_path):
    from dbwarden.engine.version import get_migration_filepaths_by_version
    from dbwarden.exceptions import DirectoryNotFoundError

    migrations = tmp_path / "migrations"
    migrations.mkdir()
    outside = tmp_path / "outside.sql"
    outside.write_text("-- upgrade\nSELECT 1;\n-- rollback\nSELECT 1;\n", encoding="utf-8")
    (migrations / "primary__0001_outside.sql").symlink_to(outside)

    with pytest.raises(DirectoryNotFoundError, match="outside the migrations directory"):
        get_migration_filepaths_by_version(str(migrations))
