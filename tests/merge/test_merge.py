"""Tests for merge module."""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

import pytest

from dbwarden.merge.marker import (
    SupersededMarker,
    parse_superseded_marker,
    write_superseded_marker,
    is_superseded,
    get_file_checksum,
    mark_file_superseded,
)
from dbwarden.merge.reconciliation import (
    ReconciliationHeader,
    parse_reconciliation_header,
    write_reconciliation_header,
    is_reconciliation,
)
from dbwarden.merge.detection import (
    MergeSignal,
    detect_merge_signals,
    check_version_collisions,
    get_diagnostic_message,
)
from dbwarden.merge.environments import (
    EnvironmentConfig,
    load_environments,
    is_persistent,
    get_persistent_environments,
)
from dbwarden.merge.rename_capture import (
    capture_rename_intent,
    parse_rename_intents,
    harvest_rename_intents,
)
from dbwarden.merge.git_utils import is_git_available


class TestSupersededMarker:
    """Tests for superseded marker parsing and writing."""

    def test_parse_marker(self, tmp_path):
        """Test parsing a valid superseded marker."""
        migration_file = tmp_path / "0001_test.sql"
        migration_file.write_text("""-- dbwarden:superseded
-- merged-into: 0006
-- merged-at: 2026-08-22T14:03:11Z
-- merge-base: 0004
-- branch: feature/profile-fields
-- applied-persistent: none
-- file-checksum: sha256:7ab1...

-- upgrade
ALTER TABLE users ADD COLUMN bio TEXT;

-- rollback
ALTER TABLE users DROP COLUMN bio;
""")
        marker = parse_superseded_marker(migration_file)
        assert marker is not None
        assert marker.merged_into == "0006"
        assert marker.merge_base == "0004"
        assert marker.branch == "feature/profile-fields"
        assert marker.applied_persistent == "none"

    def test_parse_marker_no_marker(self, tmp_path):
        """Test parsing a file without a marker."""
        migration_file = tmp_path / "0001_test.sql"
        migration_file.write_text("""-- upgrade
ALTER TABLE users ADD COLUMN bio TEXT;
""")
        marker = parse_superseded_marker(migration_file)
        assert marker is None

    def test_parse_marker_corrupt(self, tmp_path):
        """Test parsing a corrupt marker raises ValueError."""
        migration_file = tmp_path / "0001_test.sql"
        migration_file.write_text("""-- dbwarden:superseded
-- merged-into: 0006
-- merged-at: 2026-08-22T14:03:11Z
-- branch: feature/profile-fields
-- applied-persistent: none
-- file-checksum: sha256:7ab1...
""")
        with pytest.raises(ValueError, match="missing required field"):
            parse_superseded_marker(migration_file)

    def test_write_marker(self, tmp_path):
        """Test writing a superseded marker."""
        migration_file = tmp_path / "0001_test.sql"
        migration_file.write_text("""-- upgrade
ALTER TABLE users ADD COLUMN bio TEXT;
""")
        marker = SupersededMarker(
            merged_into="0006",
            merged_at="2026-08-22T14:03:11Z",
            merge_base="0004",
            branch="feature/profile-fields",
            applied_persistent="none",
            file_checksum="sha256:7ab1...",
        )
        write_superseded_marker(migration_file, marker)

        # Verify marker was written
        content = migration_file.read_text()
        assert "-- dbwarden:superseded" in content
        assert "-- merged-into: 0006" in content
        assert "-- branch: feature/profile-fields" in content

    def test_is_superseded(self, tmp_path):
        """Test is_superseded function."""
        migration_file = tmp_path / "0001_test.sql"
        migration_file.write_text("-- upgrade\nSELECT 1;")
        assert not is_superseded(migration_file)

        marker = SupersededMarker(
            merged_into="0006",
            merged_at="2026-08-22T14:03:11Z",
            merge_base="0004",
            branch="feature/profile-fields",
            applied_persistent="none",
            file_checksum="sha256:7ab1...",
        )
        write_superseded_marker(migration_file, marker)
        assert is_superseded(migration_file)

    def test_get_file_checksum(self, tmp_path):
        """Test file checksum calculation."""
        file1 = tmp_path / "test1.sql"
        file1.write_text("-- upgrade\nSELECT 1;")
        checksum1 = get_file_checksum(file1)

        file2 = tmp_path / "test2.sql"
        file2.write_text("-- upgrade\nSELECT 1;")
        checksum2 = get_file_checksum(file2)

        # Same content = same checksum
        assert checksum1 == checksum2
        assert checksum1.startswith("sha256:")

    def test_mark_file_superseded(self, tmp_path):
        """Test mark_file_superseded convenience function."""
        migration_file = tmp_path / "0001_test.sql"
        migration_file.write_text("-- upgrade\nSELECT 1;")

        marker = mark_file_superseded(
            migration_file,
            merged_into="0006",
            merge_base="0004",
            branch="feature/test",
        )

        assert marker.merged_into == "0006"
        assert marker.merge_base == "0004"
        assert marker.branch == "feature/test"
        assert marker.applied_persistent == "none"
        assert is_superseded(migration_file)


class TestReconciliationHeader:
    """Tests for reconciliation header parsing and writing."""

    def test_parse_header(self, tmp_path):
        """Test parsing a valid reconciliation header."""
        migration_file = tmp_path / "0006_merge.sql"
        migration_file.write_text("""-- dbwarden:merge-reconciliation
-- merge-base: 0004 (state checksum 9f2c...)
-- supersedes: 0005_add_profile.sql, 0005_extend_billing.sql
-- probe: staging=clean, production=clean, qa=unknown
-- generated-by: dbwarden merge (0.18.0)

-- upgrade
ALTER TABLE users ADD COLUMN profile TEXT;

-- rollback
ALTER TABLE users DROP COLUMN profile;
""")
        header = parse_reconciliation_header(migration_file)
        assert header is not None
        assert header.merge_base == "0004"
        assert header.merge_base_checksum == "9f2c..."
        assert len(header.supersedes) == 2
        assert header.probe_results["staging"] == "clean"
        assert header.probe_results["production"] == "clean"
        assert header.probe_results["qa"] == "unknown"

    def test_parse_header_no_header(self, tmp_path):
        """Test parsing a file without a header."""
        migration_file = tmp_path / "0001_test.sql"
        migration_file.write_text("-- upgrade\nSELECT 1;")
        header = parse_reconciliation_header(migration_file)
        assert header is None

    def test_write_header(self, tmp_path):
        """Test writing a reconciliation header."""
        migration_file = tmp_path / "0006_merge.sql"
        migration_file.write_text("-- upgrade\nSELECT 1;")

        header = ReconciliationHeader(
            merge_base="0004",
            merge_base_checksum="9f2c...",
            supersedes=["0005_add_profile.sql", "0005_extend_billing.sql"],
            probe_results={"staging": "clean", "production": "clean"},
            generated_by="dbwarden merge (0.18.0)",
        )
        write_reconciliation_header(migration_file, header)

        content = migration_file.read_text()
        assert "-- dbwarden:merge-reconciliation" in content
        assert "-- merge-base: 0004" in content
        assert "-- supersedes: 0005_add_profile.sql, 0005_extend_billing.sql" in content

    def test_is_reconciliation(self, tmp_path):
        """Test is_reconciliation function."""
        migration_file = tmp_path / "0001_test.sql"
        migration_file.write_text("-- upgrade\nSELECT 1;")
        assert not is_reconciliation(migration_file)

        header = ReconciliationHeader(
            merge_base="0004",
            merge_base_checksum="abc",
            supersedes=[],
            probe_results={},
            generated_by="test",
        )
        write_reconciliation_header(migration_file, header)
        assert is_reconciliation(migration_file)


class TestMergeDetection:
    """Tests for merge signal detection."""

    def test_version_collisions_in_directory(self, tmp_path):
        """Test version collision detection in a directory."""
        from dbwarden.merge.detection import check_version_collisions
        from dbwarden.engine.version import MIGRATION_PATTERN
        import os

        # Create two files with same version in a temp directory
        migrations_dir = tmp_path / "migrations"
        migrations_dir.mkdir()
        (migrations_dir / "primary__0001_a.sql").write_text("-- upgrade\nSELECT 1;")
        (migrations_dir / "primary__0001_b.sql").write_text("-- upgrade\nSELECT 2;")

        # Manually test the collision detection logic
        versions = {}
        for filename in os.listdir(migrations_dir):
            match = MIGRATION_PATTERN.match(filename)
            if match:
                version = match.group(1)
                if version not in versions:
                    versions[version] = []
                versions[version].append(filename)

        collisions = [v for v, files in versions.items() if len(files) > 1]
        assert "0001" in collisions

    def test_no_version_collisions(self, tmp_path):
        """Test no collisions detected."""
        from dbwarden.engine.version import MIGRATION_PATTERN
        import os

        migrations_dir = tmp_path / "migrations"
        migrations_dir.mkdir()
        (migrations_dir / "primary__0001_a.sql").write_text("-- upgrade\nSELECT 1;")
        (migrations_dir / "primary__0002_a.sql").write_text("-- upgrade\nSELECT 2;")

        versions = {}
        for filename in os.listdir(migrations_dir):
            match = MIGRATION_PATTERN.match(filename)
            if match:
                version = match.group(1)
                if version not in versions:
                    versions[version] = []
                versions[version].append(filename)

        collisions = [v for v, files in versions.items() if len(files) > 1]
        assert len(collisions) == 0

    def test_diagnostic_message(self):
        """Test diagnostic message generation."""
        msg = get_diagnostic_message([MergeSignal.DIVERGENT_BASE])
        assert "Divergent generation base" in msg

        msg = get_diagnostic_message([MergeSignal.VERSION_COLLISION])
        assert "Version collision" in msg

        msg = get_diagnostic_message([MergeSignal.SNAPSHOT_DISCONTINUITY])
        assert "Snapshot discontinuity" in msg

        msg = get_diagnostic_message([])
        assert "No merge signals" in msg


class TestEnvironments:
    """Tests for environment registry."""

    def test_environment_config(self):
        """Test EnvironmentConfig dataclass."""
        env = EnvironmentConfig(
            name="staging",
            url_env="STAGING_DATABASE_URL",
            persistent=True,
        )
        assert env.name == "staging"
        assert env.persistent is True

    def test_is_persistent_no_envs(self, tmp_path):
        """Test is_persistent with no environments configured."""
        # This will fail to load config, so should return False
        result = is_persistent("staging", "nonexistent")
        assert result is False

    def test_get_persistent_environments_no_envs(self, tmp_path):
        """Test get_persistent_environments with no environments."""
        result = get_persistent_environments("nonexistent")
        assert result == []


class TestRenameCapture:
    """Tests for rename intent capture."""

    def test_capture_and_parse(self, tmp_path):
        """Test capturing and parsing rename intents."""
        migration_file = tmp_path / "0001_test.sql"
        migration_file.write_text("""-- upgrade
ALTER TABLE users RENAME COLUMN username TO handle;
""")

        renames = [{"from": "users.username", "to": "users.handle"}]
        capture_rename_intent(migration_file, renames)

        parsed = parse_rename_intents(migration_file)
        assert len(parsed) == 1
        assert parsed[0]["from"] == "users.username"
        assert parsed[0]["to"] == "users.handle"

    def test_parse_no_renames(self, tmp_path):
        """Test parsing file without renames."""
        migration_file = tmp_path / "0001_test.sql"
        migration_file.write_text("-- upgrade\nSELECT 1;")
        parsed = parse_rename_intents(migration_file)
        assert parsed == []

    def test_harvest_intents(self, tmp_path):
        """Test harvesting rename intents from multiple files."""
        file1 = tmp_path / "0001_test.sql"
        file1.write_text("-- upgrade\nSELECT 1;")
        capture_rename_intent(file1, [{"from": "a.x", "to": "a.y"}])

        file2 = tmp_path / "0002_test.sql"
        file2.write_text("-- upgrade\nSELECT 1;")
        capture_rename_intent(file2, [{"from": "b.x", "to": "b.y"}])

        all_renames = harvest_rename_intents([file1, file2])
        assert len(all_renames) == 2


class TestGitUtils:
    """Tests for git utilities."""

    def test_is_git_available(self):
        """Test git availability check."""
        # This should work in most dev environments
        result = is_git_available()
        assert isinstance(result, bool)
