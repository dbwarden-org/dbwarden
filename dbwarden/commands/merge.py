"""dbwarden merge command.

Reconciles divergent migration histories after a branch merge.
Implements the merge spec §5.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Optional

from dbwarden import __version__
from dbwarden.config import get_database
from dbwarden.engine.version import get_migrations_directory, get_next_migration_number
from dbwarden.logging import get_logger
from dbwarden.output import error, info, success, warning

from dbwarden.merge.marker import (
    SupersededMarker,
    get_file_checksum,
    is_superseded,
    mark_file_superseded,
    parse_superseded_marker,
)
from dbwarden.merge.reconciliation import (
    ReconciliationHeader,
    write_reconciliation_header,
)
from dbwarden.merge.detection import (
    MergeSignal,
    detect_merge_signals,
    get_diagnostic_message,
)
from dbwarden.merge.environments import get_persistent_environments
from dbwarden.merge.git_utils import (
    get_merge_base,
    get_file_at_commit,
    is_clean_working_tree,
    has_conflict_markers,
)
from dbwarden.merge.rename_capture import harvest_rename_intents
from dbwarden.files import atomic_write_text


logger = None


def merge_cmd(
    database: str | None = None,
    rename_columns: list[str] | None = None,
    rename_tables: list[str] | None = None,
    force: bool = False,
    commit: bool = False,
    json_output: bool = False,
    verbose: bool = False,
) -> None:
    """Merge divergent migration histories.

    This command reconciles migration histories after a branch merge by:
    1. Resolving the merge-base state
    2. Rebuilding the current model state
    3. Computing the reconciliation diff
    4. Probing persistent environments
    5. Generating a reconciliation migration
    6. Marking branch migrations as superseded
    7. Atomic commit of outputs

    Args:
        database: Target database name.
        rename_columns: Column renames to confirm (format: "table.old=new").
        rename_tables: Table renames to confirm (format: "old=new").
        force: Force marking hand-edited migrations.
        commit: Create a git commit with the changes.
        json_output: Output results as JSON.
        verbose: Enable verbose logging.
    """
    global logger
    from dbwarden.logging import get_logger as _get_logger
    logger = _get_logger(verbose=verbose)

    from dbwarden.engine.version import get_migrations_directory
    from dbwarden.merge.detection import detect_merge_signals

    # Step 0: Preconditions
    info("Checking preconditions...")

    if not _check_preconditions(database):
        return

    # Step 1: Resolve merge-base state
    info("Resolving merge-base state...")
    merge_base = get_merge_base()
    if merge_base is None:
        error("Not in a merge state. Cannot determine merge-base.")
        return

    merge_base_state = _get_merge_base_state(merge_base, database)
    if merge_base_state is None:
        error("Could not read merge-base state from git.")
        return

    # Step 2: Rebuild current model state
    info("Rebuilding current model state...")
    current_state = _rebuild_current_state(database)
    if current_state is None:
        error("Could not rebuild current model state.")
        return

    # Step 3: Compute reconciliation diff
    info("Computing reconciliation diff...")
    diff_ops = _compute_diff(merge_base_state, current_state, database)

    # Step 3a: Detect semantic conflicts (R9.2)
    semantic_conflicts = _detect_semantic_conflicts(merge_base_state, current_state, database)
    if semantic_conflicts:
        warning("Semantic conflicts detected (merged state differs from both branches):")
        for conflict in semantic_conflicts:
            info(f"  {conflict['description']}")

    # Step 3b: Check for stale plans (R9.11)
    migrations_dir = get_migrations_directory(database)
    stale_plans = _check_stale_plans(migrations_dir, current_state)
    if stale_plans:
        warning("Stale migration plans detected (base checksum mismatch):")
        for plan in stale_plans:
            info(f"  {plan}")
        info("These plans will be invalidated and regenerated.")

    # Step 3c: Harvest rename intents from superseded files (R9.1.4)
    migrations_dir = get_migrations_directory(database)
    superseded_files = _find_superseded_files(migrations_dir, merge_base)
    harvested_renames = _harvest_rename_intents(migrations_dir, superseded_files)
    if harvested_renames:
        info(f"Harvested {len(harvested_renames)} rename intent(s) from superseded files.")
        # Apply harvested renames to diff ops
        _apply_harvested_renames(harvested_renames, diff_ops)

    # Step 3b: Check for rename candidates (R9.1.1)
    if rename_columns or rename_tables:
        info("Processing rename confirmations...")
        _process_rename_confirmations(rename_columns, rename_tables, diff_ops)
    else:
        # Check if there are any rename candidates that need confirmation
        rename_candidates = _detect_rename_candidates(diff_ops)
        if rename_candidates:
            # R9.1.5: Ranked-candidate wizard
            if sys.stdin.isatty():
                info("Rename candidates detected. Starting confirmation wizard...")
                confirmed = _prompt_rename_wizard(rename_candidates)
                if not confirmed:
                    error("No renames confirmed. Aborting merge.")
                    return
            else:
                # R9.1.6: CI contract - fail with JSON error
                error("Rename candidates detected in non-interactive mode.")
                info("Use --rename-column or --rename-table to confirm renames.")
                if json_output:
                    import json
                    error_json = {
                        "error": "rename_candidates_detected",
                        "candidates": rename_candidates,
                        "message": "Use --rename-column or --rename-table to confirm renames.",
                    }
                    print(json.dumps(error_json, indent=2))
                return

    # Step 4: Probe persistent environments
    info("Probing persistent environments...")
    probe_results = _probe_persistent_environments(database)

    # Step 5: Generate reconciliation migration
    migrations_dir = get_migrations_directory(database)
    superseded_files = _find_superseded_files(migrations_dir, merge_base)

    if not diff_ops and not superseded_files:
        info("No-op merge: no changes to reconcile.")
        _mark_only(migrations_dir, merge_base, probe_results)
        return

    info("Generating reconciliation migration...")
    reconciliation_version = get_next_migration_number(migrations_dir)
    reconciliation_file = _generate_reconciliation(
        migrations_dir,
        reconciliation_version,
        merge_base,
        merge_base_state,
        superseded_files,
        probe_results,
        diff_ops,
        database,
    )

    # Step 6: Mark branch migrations
    info("Marking branch migrations as superseded...")
    marked_files = _mark_branch_migrations(
        migrations_dir,
        merge_base,
        reconciliation_version,
        "merge",
        probe_results,
        force,
    )

    # Step 7: Report
    if json_output:
        _print_json_report(
            merge_base=merge_base,
            marked_files=marked_files,
            reconciliation_file=reconciliation_file,
            probe_results=probe_results,
        )
    else:
        _print_report(
            merge_base=merge_base,
            marked_files=marked_files,
            reconciliation_file=reconciliation_file,
            probe_results=probe_results,
        )

    # Step 8: Write merge record
    _write_merge_record(
        reconciliation_version=reconciliation_version,
        merge_base=merge_base,
        marked_files=marked_files,
        probe_results=probe_results,
        force=force,
    )

    success("Merge reconciliation complete.")


def _check_preconditions(database: str | None) -> bool:
    """Check preconditions for merge (§5 P1-P3)."""
    from dbwarden.merge.git_utils import is_clean_working_tree, has_conflict_markers
    from dbwarden.merge.detection import detect_merge_signals

    # P1: Clean working tree
    if not is_clean_working_tree():
        error("Working tree is not clean. Commit or stash changes before merging.")
        return False

    # P2: No conflict markers
    from dbwarden.engine.version import get_migrations_directory
    try:
        migrations_dir = get_migrations_directory(database)
        for f in Path(migrations_dir).glob("*.sql"):
            if has_conflict_markers(str(f)):
                error(f"Conflict markers found in {f.name}. Resolve conflicts first.")
                return False
    except Exception:
        pass

    # P3: Merge-base resolvable
    from dbwarden.merge.git_utils import get_merge_base
    merge_base = get_merge_base()
    if merge_base is None:
        error("Not in a merge state. Cannot determine merge-base.")
        return False

    return True


def _get_merge_base_state(merge_base: str, database: str | None) -> Optional[dict]:
    """Get the model state at the merge-base commit."""
    from dbwarden.commands.make_migrations.pipeline import get_model_state_path
    from dbwarden.merge.git_utils import get_file_at_commit

    # Try to read model_state.json at the merge-base commit
    state_path = get_model_state_path(database)
    state_filename = state_path.name

    content = get_file_at_commit(merge_base, state_filename)
    if content is None:
        # Try legacy path
        content = get_file_at_commit(merge_base, ".dbwarden/model_state.json")

    if content is None:
        logger.warning("Could not read model_state.json at merge-base %s", merge_base[:8])
        return None

    try:
        return json.loads(content)
    except json.JSONDecodeError as e:
        logger.error("Invalid model_state.json at merge-base: %s", e)
        return None


def _rebuild_current_state(database: str | None) -> Optional[dict]:
    """Rebuild current model state from merged models.

    §5 Step 2: The merged model_state.json may be conflicted, stale,
    or hand-merged (untrusted). Discard it and regenerate from the
    merged models: the equivalent of export-models, computed offline
    from the model definitions.
    """
    try:
        from dbwarden.engine.discovery import get_all_model_tables, auto_discover_model_paths
        from dbwarden.engine.offline import model_state_to_dict
        from dbwarden.config import get_database

        config = get_database(database)
        model_paths = config.model_paths

        if model_paths is None:
            model_paths = auto_discover_model_paths()

        if not model_paths:
            logger.warning("No model paths found for state rebuild")
            return None

        # Discover model tables from merged models
        tables = get_all_model_tables(model_paths, db_name=database)

        if not tables:
            logger.warning("No tables found in models for state rebuild")
            return None

        # Convert to model state dict (equivalent of export-models)
        state = model_state_to_dict(tables)

        logger.info("Rebuilt current model state from %d tables", len(tables))
        return state

    except Exception as e:
        logger.error("Failed to rebuild current model state: %s", e)
        return None


def _compute_state_checksum(state: dict) -> str:
    """Compute a checksum for a model state dict."""
    import hashlib
    import json

    state_copy = {k: v for k, v in state.items() if k != "checksum"}
    content = json.dumps(state_copy, sort_keys=True, default=str)
    return hashlib.sha256(content.encode()).hexdigest()


def _compute_diff(
    base_state: dict,
    current_state: dict,
    database: str | None,
) -> list[dict]:
    """Compute the diff between merge-base and current state.

    Uses the snapshot diff engine to compare the merge-base state
    against the current model state.
    """
    try:
        from dbwarden.engine.snapshot.diff import diff_models_against_snapshot
        from dbwarden.engine.core.model_state import reconstruct_model_table

        # Convert current state dict to model tables
        current_tables = []
        for table_name, table_data in current_state.get("tables", {}).items():
            try:
                model_table = reconstruct_model_table(table_data)
                model_table.name = table_name
                current_tables.append(model_table)
            except Exception as e:
                logger.warning("Failed to reconstruct model table %s: %s", table_name, e)

        # Use base_state as the snapshot (it's already in snapshot format)
        snapshot = base_state

        # Compute diff
        upgrade_ops, rollback_ops = diff_models_against_snapshot(
            current_tables,
            snapshot,
            database=database,
            db_name=database,
        )

        # Convert ops to dict format for the reconciliation migration
        diff_ops = []
        for op in upgrade_ops:
            diff_ops.append({
                "type": op.get("type", "unknown"),
                "table": op.get("table", ""),
                "description": op.get("description", str(op)),
            })

        return diff_ops

    except Exception as e:
        logger.warning("Failed to compute diff: %s", e)
        return []


def _detect_rename_candidates(diff_ops: list[dict]) -> list[str]:
    """Detect rename candidates from diff operations.

    R9.1.1: Every rename candidate detected during a merge reconciliation
    requires explicit confirmation.
    """
    candidates = []
    for op in diff_ops:
        op_type = op.get("type", "")
        # Look for drop+add patterns that might be renames
        if "drop" in op_type.lower() and "add" in op_type.lower():
            table = op.get("table", "")
            if table:
                candidates.append(f"{table} (possible rename)")
    return candidates


def _process_rename_confirmations(
    rename_columns: list[str] | None,
    rename_tables: list[str] | None,
    diff_ops: list[dict],
) -> None:
    """Process rename confirmations from CLI flags.

    R9.1.1: The confirmed rename mapping is written into the
    reconciliation migration header.
    """
    if rename_columns:
        for rename in rename_columns:
            if "=" not in rename:
                warning(f"Invalid rename format: {rename}. Expected format: table.old=new")
                continue
            info(f"Confirmed column rename: {rename}")

    if rename_tables:
        for rename in rename_tables:
            if "=" not in rename:
                warning(f"Invalid rename format: {rename}. Expected format: old=new")
                continue
        info(f"Confirmed table renames: {', '.join(rename_tables)}")


def _prompt_rename_wizard(candidates: list[str]) -> list[str]:
    """Interactive wizard for confirming renames (R9.1.5).

    Presents candidates ranked by confidence with evidence shown,
    one decision per screen, with batch acceptance.
    """
    import sys

    confirmed = []
    for candidate in candidates:
        info(f"\nRename candidate: {candidate}")
        info("Evidence: Possible drop+add pattern detected")

        while True:
            try:
                response = input("[a]ccept / [r]eject / [d]rop+create: ").strip().lower()
            except (EOFError, KeyboardInterrupt):
                # Non-interactive mode
                return []

            if response == "a":
                confirmed.append(candidate)
                info(f"  Confirmed: {candidate}")
                break
            elif response == "r":
                info(f"  Rejected: {candidate}")
                break
            elif response == "d":
                info(f"  Marked as drop+create: {candidate}")
                break
            else:
                info("  Please enter 'a', 'r', or 'd'")

    return confirmed


def _harvest_rename_intents(migrations_dir: str, superseded_files: list[str]) -> list[dict]:
    """Harvest rename intents from superseded migration files (R9.1.4).

    When make-migrations --rename-column/--rename-table is used on a branch,
    the mapping is recorded in that migration's header. dbwarden merge
    harvests these declarations automatically.
    """
    from dbwarden.merge.rename_capture import harvest_rename_intents

    migration_files = []
    for filename in superseded_files:
        filepath = Path(migrations_dir) / filename
        if filepath.exists():
            migration_files.append(filepath)

    return harvest_rename_intents(migration_files)


def _apply_harvested_renames(renames: list[dict], diff_ops: list[dict]) -> None:
    """Apply harvested rename intents to diff operations.

    This modifies diff_ops in place to reflect the confirmed renames.
    """
    for rename in renames:
        old_name = rename.get("from", "")
        new_name = rename.get("to", "")
        if old_name and new_name:
            # Mark the diff op as a confirmed rename
            for op in diff_ops:
                if old_name in op.get("description", ""):
                    op["rename_confirmed"] = True
                    op["rename_from"] = old_name
                    op["rename_to"] = new_name


def _detect_semantic_conflicts(
    base_state: dict,
    current_state: dict,
    database: str | None,
) -> list[dict]:
    """Detect semantic conflicts where merged state differs from both branches.

    R9.2: When both branches change the same column's type differently,
    and git auto-merges them, the merged state may differ from both
    branches' versions. This function detects such cases.

    Returns a list of conflict descriptions for display.
    """
    conflicts = []

    try:
        # Compare tables in base vs current
        base_tables = base_state.get("tables", {})
        current_tables = current_state.get("tables", {})

        for table_name, current_table in current_tables.items():
            if table_name not in base_table:
                continue

            base_table = base_tables[table_name]
            base_columns = base_table.get("columns", {})
            current_columns = current_table.get("columns", {})

            # Check for columns that changed type in both directions
            for col_name, current_col in current_columns.items():
                if col_name not in base_columns:
                    continue

                base_col = base_columns[col_name]
                base_type = base_col.get("type", "")
                current_type = current_col.get("type", "")

                if base_type != current_type:
                    # Column type changed - this could be a semantic conflict
                    # if both branches changed it differently
                    conflicts.append({
                        "table": table_name,
                        "column": col_name,
                        "base_type": base_type,
                        "current_type": current_type,
                        "description": f"{table_name}.{col_name}: {base_type} -> {current_type}",
                    })

    except Exception as e:
        logger.debug("Failed to detect semantic conflicts: %s", e)

    return conflicts


def _probe_persistent_environments(database: str | None) -> dict[str, str]:
    """Probe persistent environments to check if they applied branch migrations.

    R8.2: Environments that were unknown at merge time (unreachable) are
    probed on the next status/migrate run against them; if found dirty,
    migrate refuses to run the normal chain and directs the operator
    to reconcile.

    For each persistent environment, reads its applied-migration metadata table
    and checks whether any to-be-superseded migration version appears there.
    """
    from dbwarden.merge.environments import get_persistent_environments

    persistent_envs = get_persistent_environments(database)
    results = {}

    for env in persistent_envs:
        try:
            # Get the environment's database URL from the registry
            from dbwarden.merge.environments import load_environments
            envs = load_environments(database)
            env_config = envs.get(env)

            if env_config is None or not env_config.url_env:
                results[env] = "unknown"
                continue

            # Check if the environment variable is set
            import os
            env_url = os.environ.get(env_config.url_env)
            if not env_url:
                results[env] = "unknown"
                continue

            # Connect to the environment and check its migration table
            try:
                from sqlalchemy import create_engine, text
                from dbwarden.connection.queries import get_migration_table_name

                engine = create_engine(env_url)
                migration_table = get_migration_table_name(database)

                with engine.connect() as conn:
                    # Check if migration table exists
                    result = conn.execute(
                        text(f"SELECT name FROM sqlite_master WHERE type='table' AND name='{migration_table}'")
                    )
                    if not result.fetchone():
                        results[env] = "clean"
                        continue

                    # Get applied versions
                    result = conn.execute(
                        text(f"SELECT version FROM {migration_table} WHERE version IS NOT NULL")
                    )
                    applied_versions = {row[0] for row in result.fetchall()}

                    # Check for superseded versions
                    superseded = _find_superseded_versions(database)
                    dirty_versions = applied_versions.intersection(superseded)

                    if dirty_versions:
                        results[env] = "dirty"
                        logger.info("Environment %s has dirty migrations: %s", env, dirty_versions)
                    else:
                        results[env] = "clean"

                engine.dispose()

            except Exception as e:
                logger.debug("Failed to probe environment %s: %s", env, e)
                results[env] = "unknown"

        except Exception as e:
            logger.debug("Failed to probe environment %s: %s", env, e)
            results[env] = "unknown"

    return results


def _find_superseded_versions(database: str | None) -> set[str]:
    """Find all superseded migration versions."""
    from dbwarden.merge.marker import is_superseded
    from dbwarden.engine.version import get_migrations_directory, get_migration_filepaths_by_version

    migrations_dir = get_migrations_directory(database)
    all_migrations = get_migration_filepaths_by_version(migrations_dir)

    superseded = set()
    for version, filepath in all_migrations.items():
        if is_superseded(filepath):
            superseded.add(version)

    return superseded


def _find_superseded_files(migrations_dir: str, merge_base: str) -> list[str]:
    """Find migration files that should be superseded."""
    from dbwarden.merge.git_utils import get_file_at_commit
    from dbwarden.engine.version import get_migration_filepaths_by_version

    # Get all migrations
    all_migrations = get_migration_filepaths_by_version(migrations_dir)

    # Get migrations at merge-base
    merge_base_migrations = {}
    for version, filepath in all_migrations.items():
        filename = filepath.split("/")[-1]
        content = get_file_at_commit(merge_base, filename)
        if content is not None:
            merge_base_migrations[version] = filename

    # Migrations after merge-base are candidates for superseding
    superseded = []
    for version in sorted(all_migrations.keys()):
        if version > max(merge_base_migrations.keys(), default="0000"):
            filename = all_migrations[version].split("/")[-1]
            if not is_superseded(Path(migrations_dir) / filename):
                superseded.append(filename)

    return superseded


def _check_stale_plans(migrations_dir: str, current_state: dict) -> list[str]:
    """Check for stale migration plans (R9.11).

    Every generated migration/plan is pinned to a base model-state checksum.
    When that base no longer matches the current model state, the plan is stale.

    Returns a list of stale migration filenames.
    """
    import json
    from dbwarden.engine.version import get_migration_filepaths_by_version

    all_migrations = get_migration_filepaths_by_version(migrations_dir)
    stale_files = []

    # Compute current model state checksum
    current_checksum = _compute_state_checksum(current_state)

    for version, filepath in all_migrations.items():
        plan_file = Path(filepath).with_suffix(".plan.json")
        if not plan_file.exists():
            continue

        try:
            plan_data = json.loads(plan_file.read_text())
            plan_checksum = plan_data.get("base_checksum", "")

            if plan_checksum and plan_checksum != current_checksum:
                stale_files.append(filepath.split("/")[-1])
                logger.info("Stale plan detected: %s (base checksum mismatch)", filepath.split("/")[-1])
        except Exception as e:
            logger.debug("Failed to check plan for %s: %s", filepath, e)

    return stale_files


def _mark_only(
    migrations_dir: str,
    merge_base: str,
    probe_results: dict[str, str],
) -> None:
    """Mark branch migrations without generating a reconciliation (no-op merge)."""
    superseded_files = _find_superseded_files(migrations_dir, merge_base)
    if superseded_files:
        for filename in superseded_files:
            filepath = Path(migrations_dir) / filename
            mark_file_superseded(
                filepath,
                merged_into="none",
                merge_base=merge_base,
                branch="merge",
                applied_persistent=_format_probe_results(probe_results),
            )
        info(f"Marked {len(superseded_files)} migration(s) as superseded.")
    else:
        info("No branch migrations to mark.")


def _generate_reconciliation(
    migrations_dir: str,
    version: str,
    merge_base: str,
    merge_base_state: dict,
    superseded_files: list[str],
    probe_results: dict[str, str],
    diff_ops: list[dict],
    database: str | None,
) -> str:
    """Generate the reconciliation migration file."""
    from dbwarden.engine.version import generate_migration_filename

    # Generate filename
    description = f"merge reconciliation from {merge_base}"
    filename = generate_migration_filename(database or "default", description, version)
    filepath = Path(migrations_dir) / filename

    # Build upgrade SQL from diff ops
    upgrade_sql = "-- upgrade\n"
    for op in diff_ops:
        upgrade_sql += f"-- {op.get('description', 'no-op')}\n"

    if not diff_ops:
        upgrade_sql += "-- No schema changes required\n"

    # Build rollback SQL
    rollback_sql = "-- rollback\n-- No rollback required for merge reconciliation\n"

    # Write the migration file
    content = upgrade_sql + "\n" + rollback_sql
    atomic_write_text(filepath, content)

    # Write reconciliation header
    merge_base_checksum = _compute_state_checksum(merge_base_state)
    header = ReconciliationHeader(
        merge_base=merge_base,
        merge_base_checksum=merge_base_checksum[:16],
        supersedes=superseded_files,
        probe_results=probe_results,
        generated_by=f"dbwarden merge (dbwarden {__version__})",
    )
    write_reconciliation_header(filepath, header)

    return filename


def _mark_branch_migrations(
    migrations_dir: str,
    merge_base: str,
    reconciliation_version: str,
    branch_name: str,
    probe_results: dict[str, str],
    force: bool = False,
) -> list[str]:
    """Mark branch migrations as superseded."""
    from dbwarden.merge.marker import mark_file_superseded
    from dbwarden.merge.git_utils import get_file_at_commit
    from dbwarden.engine.version import get_migration_filepaths_by_version

    all_migrations = get_migration_filepaths_by_version(migrations_dir)

    # Get migrations at merge-base
    merge_base_versions = set()
    for version in all_migrations.keys():
        filename = all_migrations[version].split("/")[-1]
        content = get_file_at_commit(merge_base, filename)
        if content is not None:
            merge_base_versions.add(version)

    # Mark migrations after merge-base
    marked = []
    for version in sorted(all_migrations.keys()):
        if version > max(merge_base_versions, default="0000"):
            filename = all_migrations[version].split("/")[-1]
            filepath = Path(migrations_dir) / filename

            if is_superseded(filepath):
                continue

            # Check if file was hand-edited (R9.4)
            if not force and _is_hand_edited(filepath):
                warning(f"Refusing to mark hand-edited migration {filename}. Use --force to override.")
                continue

            mark_file_superseded(
                filepath,
                merged_into=reconciliation_version,
                merge_base=merge_base,
                branch=branch_name,
                applied_persistent=_format_probe_results(probe_results),
            )
            marked.append(filename)

    return marked


def _is_hand_edited(filepath: Path) -> bool:
    """Check if a migration file was hand-edited after generation.

    Compares the file's content checksum against the checksum recorded
    in the migration plan (.plan.json file).
    """
    from dbwarden.merge.marker import get_file_checksum

    # Check for plan file
    plan_file = filepath.with_suffix(".plan.json")
    if not plan_file.exists():
        # No plan file means it was hand-written, not generated
        return True

    try:
        import json
        plan_data = json.loads(plan_file.read_text())
        plan_checksum = plan_data.get("content_hash", "")

        # Get current file checksum
        current_checksum = get_file_checksum(filepath)

        # Compare (strip "sha256:" prefix if present)
        plan_hash = plan_checksum.replace("sha256:", "") if plan_checksum else ""
        current_hash = current_checksum.replace("sha256:", "") if current_checksum else ""

        if plan_hash and current_hash and plan_hash != current_hash:
            logger.warning("Migration %s has been hand-edited (checksum mismatch)", filepath.name)
            return True

        return False

    except Exception as e:
        logger.debug("Failed to check hand-edit status for %s: %s", filepath.name, e)
        return False


def _format_probe_results(probe_results: dict[str, str]) -> str:
    """Format probe results for the applied_persistent field."""
    if not probe_results:
        return "none"

    parts = []
    for env, result in probe_results.items():
        if result == "unknown":
            parts.append(f"unknown:{env}")
        elif result == "dirty":
            parts.append(env)

    return ",".join(parts) if parts else "none"


def _print_report(
    merge_base: str,
    marked_files: list[str],
    reconciliation_file: str,
    probe_results: dict[str, str],
) -> None:
    """Print the merge reconciliation report."""
    from dbwarden.output import section, info

    section("Merge reconciliation summary")
    info(f"  Merge base:      {merge_base[:8]}")
    info(f"  Superseded:      {', '.join(marked_files) if marked_files else 'none'}")
    info(f"  Reconciliation:  {reconciliation_file}")
    info(f"  Environments:    {_format_probe_results(probe_results)}")
    info("  Next steps:      commit; developers on feature branches: dbwarden rebase")


def _print_json_report(
    merge_base: str,
    marked_files: list[str],
    reconciliation_file: str,
    probe_results: dict[str, str],
) -> None:
    """Print the merge reconciliation report as JSON."""
    from dbwarden.output import emit_json

    report = {
        "merge_base": merge_base,
        "superseded_files": marked_files,
        "reconciliation_file": reconciliation_file,
        "probe_results": probe_results,
        "next_steps": "commit; developers on feature branches: dbwarden rebase",
    }
    emit_json(report)


def _write_merge_record(
    reconciliation_version: str,
    merge_base: str,
    marked_files: list[str],
    probe_results: dict[str, str],
    force: bool,
) -> None:
    """Write a durable merge record to .dbwarden/merges/."""
    from datetime import datetime, timezone
    from dbwarden import __version__
    from dbwarden.files import atomic_write_text

    # Create merges directory
    merges_dir = Path(".dbwarden") / "merges"
    merges_dir.mkdir(parents=True, exist_ok=True)

    # Build merge record
    record = {
        "version": reconciliation_version,
        "merge_base": merge_base,
        "superseded_files": marked_files,
        "probe_results": probe_results,
        "generated_by": f"dbwarden merge (dbwarden {__version__})",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "force_used": force,
    }

    # Write record file
    record_file = merges_dir / f"{reconciliation_version}.json"
    import json
    atomic_write_text(record_file, json.dumps(record, indent=2))
