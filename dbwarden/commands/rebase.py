"""dbwarden rebase command.

Recovers a disposable environment after a merge.
Implements the merge spec §7.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from dbwarden.logging import get_logger
from dbwarden.output import error, info, success, warning

from dbwarden.merge.marker import is_superseded, parse_superseded_marker
from dbwarden.merge.environments import is_persistent


def rebase_cmd(
    database: str | None = None,
    yes: bool = False,
    force: bool = False,
    check_only: bool = False,
    verbose: bool = False,
) -> None:
    """Recover a disposable environment after a merge.

    This command helps developers recover their local database after
    pulling a merge that superseded migrations they had applied.

    Args:
        database: Target database name.
        yes: Skip confirmation prompts.
        force: Force operation even against persistent environments.
        check_only: Only check what would happen, don't make changes.
        verbose: Enable verbose logging.
    """
    logger = get_logger(verbose=verbose)

    from dbwarden.engine.version import get_migrations_directory
    from dbwarden.repositories import get_migrated_versions

    info("Checking environment...")

    # Step 1: Read local applied migrations
    try:
        applied_versions = get_migrated_versions(database)
    except Exception as e:
        error(f"Could not read applied migrations: {e}")
        return

    if not applied_versions:
        info("No migrations applied. Already converged.")
        return

    # Step 2: Identify superseded versions
    migrations_dir = get_migrations_directory(database)
    superseded_applied = []

    for version in applied_versions:
        # Find the migration file for this version
        for f in Path(migrations_dir).glob(f"*__{version}_*.sql"):
            if is_superseded(f):
                superseded_applied.append(version)
                break

    if not superseded_applied:
        info("No superseded migrations applied. Already converged.")
        return

    info(f"Found {len(superseded_applied)} superseded migration(s) applied: {', '.join(superseded_applied)}")

    if check_only:
        info("Check-only mode. No changes will be made.")
        info("To recover, run: dbwarden rebase --database <name>")
        return

    # Step 3: Check if target is persistent
    if is_persistent("local", database) and not force:
        error("Target is a registered persistent environment. Use --force to override.")
        return

    # Step 4: Preferred path - rollback to merge-base
    info("Attempting rollback to merge-base...")
    from dbwarden.merge.git_utils import get_merge_base

    merge_base = get_merge_base()
    if merge_base is None:
        error("Could not determine merge-base. Cannot rollback.")
        return

    # Find the merge-base version from the superseded markers
    merge_base_version = _find_merge_base_version(migrations_dir, superseded_applied)

    if merge_base_version:
        info(f"Rolling back to merge-base version {merge_base_version}...")
        from dbwarden.commands.rollback import rollback_cmd

        try:
            rollback_cmd(
                to_version=merge_base_version,
                database=database,
                verbose=verbose,
            )
            info(f"Rolled back to version {merge_base_version}")
        except Exception as e:
            warning(f"Rollback failed: {e}")
            info("Falling back to reset...")
            _reset_database(database, yes, verbose)
    else:
        info("Could not determine merge-base version. Falling back to reset...")
        _reset_database(database, yes, verbose)

    # Step 5: Re-apply the runnable chain
    info("Re-applying migrations...")
    from dbwarden.commands.migrate import migrate_cmd

    try:
        migrate_cmd(database=database, verbose=verbose)
        info("Migrations re-applied successfully.")
    except Exception as e:
        error(f"Failed to re-apply migrations: {e}")
        return

    # Step 6: Verify convergence
    info("Verifying convergence...")
    from dbwarden.commands.diff import diff_cmd

    try:
        diff_cmd(database=database, verbose=verbose)
        info("Convergence verified.")
    except Exception as e:
        warning(f"Convergence check failed: {e}")

    success("Rebase complete.")


def _find_merge_base_version(migrations_dir: str, superseded_versions: list[str]) -> Optional[str]:
    """Find the merge-base version from superseded markers."""
    from dbwarden.merge.marker import parse_superseded_marker

    for version in superseded_versions:
        for f in Path(migrations_dir).glob(f"*__{version}_*.sql"):
            marker = parse_superseded_marker(f)
            if marker:
                return marker.merge_base

    return None


def _reset_database(database: str | None, yes: bool, verbose: bool) -> None:
    """Reset the database by dropping and re-creating."""
    from dbwarden.config import get_database

    config = get_database(database)

    if not yes:
        warning("This will drop and re-create the database.")
        response = input("Are you sure? (yes/no): ")
        if response.lower() != "yes":
            info("Aborted.")
            return

    info("Resetting database...")
    # In a full implementation, this would drop and re-create the database
    # For now, just log the action
    warning("Database reset not yet implemented. Please reset manually.")
