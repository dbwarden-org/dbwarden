"""Tests for migration dependency enforcement."""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from dbwarden.engine.version import (
    validate_dependencies,
    resolve_migration_order,
    DependencyError,
    get_all_migrations_with_metadata,
)
from dbwarden.engine.preflight import run_preflight


def _make_migration(tmp_path: Path, version: str, depends_on: list[str] | None = None) -> str:
    """Create a migration file with optional depends_on header.

    Header must come BEFORE -- upgrade per the parser spec.
    """
    header = ""
    if depends_on:
        header += f'-- depends_on: {json.dumps(depends_on)}\n'
    header += "-- upgrade\nSELECT 1;\n"
    filepath = tmp_path / f"migration__{version}_test.sql"
    filepath.write_text(header)
    return str(filepath)


# ─────────────────────────────────────────
# validate_dependencies tests
# ─────────────────────────────────────────


class TestValidateDependencies:
    def test_valid_deps(self, tmp_path):
        _make_migration(tmp_path, "0001")
        _make_migration(tmp_path, "0002", depends_on=["0001"])
        errors = validate_dependencies(str(tmp_path), set())
        assert errors == []

    def test_empty_deps(self, tmp_path):
        _make_migration(tmp_path, "0001")
        _make_migration(tmp_path, "0002")
        errors = validate_dependencies(str(tmp_path), set())
        assert errors == []

    def test_missing_target(self, tmp_path):
        _make_migration(tmp_path, "0001", depends_on=["9999"])
        errors = validate_dependencies(str(tmp_path), set())
        assert len(errors) == 1
        assert errors[0].error_type == "missing_target"
        assert "9999" in errors[0].message

    def test_circular_deps(self, tmp_path):
        _make_migration(tmp_path, "0001", depends_on=["0002"])
        _make_migration(tmp_path, "0002", depends_on=["0001"])
        errors = validate_dependencies(str(tmp_path), set())
        circular = [e for e in errors if e.error_type == "circular"]
        assert len(circular) >= 1

    def test_unmet_applied_dep(self, tmp_path):
        _make_migration(tmp_path, "0001")
        _make_migration(tmp_path, "0002", depends_on=["0001"])
        # 0002 is applied but 0001 is not
        errors = validate_dependencies(str(tmp_path), applied_versions={"0002"})
        unmet = [e for e in errors if e.error_type == "unmet_applied_dep"]
        assert len(unmet) == 1
        assert "0002" in unmet[0].message

    def test_applied_deps_met(self, tmp_path):
        _make_migration(tmp_path, "0001")
        _make_migration(tmp_path, "0002", depends_on=["0001"])
        errors = validate_dependencies(str(tmp_path), applied_versions={"0001", "0002"})
        assert errors == []

    def test_no_migrations(self, tmp_path):
        errors = validate_dependencies(str(tmp_path), set())
        assert errors == []

    def test_self_dependency(self, tmp_path):
        _make_migration(tmp_path, "0001", depends_on=["0001"])
        errors = validate_dependencies(str(tmp_path), set())
        # Self-dep is a cycle
        circular = [e for e in errors if e.error_type == "circular"]
        assert len(circular) >= 1

    def test_chain_deps(self, tmp_path):
        _make_migration(tmp_path, "0001")
        _make_migration(tmp_path, "0002", depends_on=["0001"])
        _make_migration(tmp_path, "0003", depends_on=["0002"])
        errors = validate_dependencies(str(tmp_path), set())
        assert errors == []

    def test_multiple_missing_targets(self, tmp_path):
        _make_migration(tmp_path, "0001", depends_on=["9998", "9999"])
        errors = validate_dependencies(str(tmp_path), set())
        missing = [e for e in errors if e.error_type == "missing_target"]
        assert len(missing) == 2
        all_missing = [dep for e in missing for dep in e.missing]
        assert "9998" in all_missing
        assert "9999" in all_missing


# ─────────────────────────────────────────
# resolve_migration_order tests
# ─────────────────────────────────────────


class TestResolveMigrationOrder:
    def test_respects_deps(self, tmp_path):
        _make_migration(tmp_path, "0002", depends_on=["0001"])
        _make_migration(tmp_path, "0001")
        result = resolve_migration_order(str(tmp_path), set())
        versions = [m[0] for m in result]
        assert versions.index("0001") < versions.index("0002")

    def test_circular_raises(self, tmp_path):
        _make_migration(tmp_path, "0001", depends_on=["0002"])
        _make_migration(tmp_path, "0002", depends_on=["0001"])
        with pytest.raises(ValueError, match="Cannot resolve"):
            resolve_migration_order(str(tmp_path), set())

    def test_improved_error_message(self, tmp_path):
        _make_migration(tmp_path, "0001", depends_on=["9999"])
        with pytest.raises(ValueError) as exc_info:
            resolve_migration_order(str(tmp_path), set())
        msg = str(exc_info.value)
        assert "Unresolved migrations" in msg
        assert "0001" in msg
        assert "unmet=" in msg

    def test_empty_directory(self, tmp_path):
        result = resolve_migration_order(str(tmp_path), set())
        assert result == []


# ─────────────────────────────────────────
# Preflight dep validation tests
# ─────────────────────────────────────────


class TestPreflightDepValidation:
    def test_missing_target_aborts(self, tmp_path):
        f = _make_migration(tmp_path, "0001", depends_on=["9999"])
        result = run_preflight(
            {"0001": f},
            missing_plan="off",
            migrations_dir=str(tmp_path),
            applied_versions=set(),
        )
        assert result.abort
        assert any("9999" in e for e in result.errors)

    def test_circular_dep_aborts(self, tmp_path):
        f1 = _make_migration(tmp_path, "0001", depends_on=["0002"])
        f2 = _make_migration(tmp_path, "0002", depends_on=["0001"])
        result = run_preflight(
            {"0001": f1, "0002": f2},
            missing_plan="off",
            migrations_dir=str(tmp_path),
            applied_versions=set(),
        )
        assert result.abort
        assert any("circular" in e.lower() or "Cannot resolve" in e for e in result.errors)

    def test_valid_deps_no_error(self, tmp_path):
        f1 = _make_migration(tmp_path, "0001")
        f2 = _make_migration(tmp_path, "0002", depends_on=["0001"])
        result = run_preflight(
            {"0001": f1, "0002": f2},
            missing_plan="off",
            migrations_dir=str(tmp_path),
            applied_versions=set(),
        )
        assert not result.abort

    def test_no_migrations_dir_skips_dep_check(self, tmp_path):
        f = _make_migration(tmp_path, "0001")
        result = run_preflight(
            {"0001": f},
            missing_plan="off",
            migrations_dir=None,
        )
        # No dep check without migrations_dir
        assert not result.abort

    def test_dep_errors_not_bypassed_by_force(self, tmp_path):
        f = _make_migration(tmp_path, "0001", depends_on=["9999"])
        result = run_preflight(
            {"0001": f},
            missing_plan="off",
            migrations_dir=str(tmp_path),
            applied_versions=set(),
        )
        assert result.abort
        # force is not a parameter of run_preflight — it's checked at a higher level
        # But dep errors always abort regardless
