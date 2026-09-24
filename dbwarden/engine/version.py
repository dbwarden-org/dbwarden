from dbwarden.constants import (
    RUNS_ALWAYS_FILE_PREFIX,
    RUNS_ON_CHANGE_FILE_PREFIX,
)
from dbwarden.exceptions import DirectoryNotFoundError
from pathlib import Path, PureWindowsPath
import re
import os
from dataclasses import dataclass, field
from typing import Optional


def _validate_path_within_project(path: Path, base_dir: Path, config_value: str) -> None:
    """Validate that resolved path stays within project."""
    resolved = path.resolve()
    base = base_dir.resolve()
    
    if not resolved.is_relative_to(base):
        raise DirectoryNotFoundError(
            f"Migration directory '{config_value}' resolves outside project. "
            f"Please use a path within the project."
        )


def get_migrations_directory(db_name: str | None = None) -> str:
    """
    Get the migrations directory path for a specific database.

    Args:
        db_name: Database name. If None, uses the database's configured migrations_dir.

    Returns:
        str: Path to migrations directory.

    Raises:
        DirectoryNotFoundError: If migrations directory is not found or is outside project.
    """
    from dbwarden.config import get_database

    config = get_database(db_name)
    current_dir = Path.cwd()
    migrations_dir = current_dir / config.migrations_dir

    # Validate path stays within project
    _validate_path_within_project(migrations_dir, current_dir, config.migrations_dir)

    if not migrations_dir.exists() or not migrations_dir.is_dir():
        raise DirectoryNotFoundError(
            f"Migrations directory '{config.migrations_dir}' not found. "
            f"Please run 'dbwarden init --database {db_name or config}' first."
        )
    return str(migrations_dir)


MIGRATION_PATTERN = re.compile(r"^[a-zA-Z0-9_]+__(\d{4})_(.+)\.sql$")
RUNS_ALWAYS_PATTERN = re.compile(
    rf"^[a-zA-Z0-9_]+__{re.escape(RUNS_ALWAYS_FILE_PREFIX)}(.+)\.sql$"
)
RUNS_ON_CHANGE_PATTERN = re.compile(
    rf"^[a-zA-Z0-9_]+__{re.escape(RUNS_ON_CHANGE_FILE_PREFIX)}(.+)\.sql$"
)


def get_migration_filepaths_by_version(
    directory: str,
    version_to_start_from: Optional[str] = None,
    end_version: Optional[str] = None,
) -> dict[str, str]:
    """
    Get migration file paths grouped by version.

    Args:
        directory: Path to migrations directory.
        version_to_start_from: Only get migrations after this version.
        end_version: Only get migrations up to this version.

    Returns:
        dict[str, str]: Mapping of version to file path.
    """
    migrations: dict[str, str] = {}

    if not os.path.exists(directory):
        return {}

    for filename in sorted(os.listdir(directory)):
        match = MIGRATION_PATTERN.match(filename)
        if match:
            version = match.group(1)
            filepath = os.path.join(directory, filename)
            _validate_migration_file(Path(filepath), Path(directory))
            if version in migrations:
                from dbwarden.merge.marker import is_superseded

                if not is_superseded(filepath) and not is_superseded(migrations[version]):
                    raise ValueError(f"Version collision {version}: {Path(migrations[version]).name}, {filename}. hint: run dbwarden merge")
                if is_superseded(filepath):
                    continue
            migrations[version] = filepath

    if version_to_start_from:
        versions = list(migrations.keys())
        start_idx = (
            versions.index(version_to_start_from) + 1
            if version_to_start_from in versions
            else 0
        )
        migrations = {k: v for k, v in list(migrations.items())[start_idx:]}

    if end_version:
        versions = list(migrations.keys())
        if end_version in versions:
            end_idx = versions.index(end_version)
            migrations = {k: v for k, v in list(migrations.items())[: end_idx + 1]}

    return migrations


def get_runs_always_filepaths(directory: str) -> list[str]:
    """
    Get all runs-always (RA__) migration file paths.

    Args:
        directory: Path to migrations directory.

    Returns:
        list[str]: List of file paths for runs-always migrations.
    """
    filepaths = []

    if not os.path.exists(directory):
        return []

    for filename in sorted(os.listdir(directory)):
        match = RUNS_ALWAYS_PATTERN.match(filename)
        if match:
            filepath = os.path.join(directory, filename)
            _validate_migration_file(Path(filepath), Path(directory))
            filepaths.append(filepath)

    return filepaths


def get_runs_on_change_filepaths(
    directory: str, changed_only: bool = False, db_name: str | None = None
) -> list[str]:
    """
    Get all runs-on-change (ROC__) migration file paths.

    Args:
        directory: Path to migrations directory.
        changed_only: Only return files that have changed since last run.
        db_name: Database name for getting existing checksums.

    Returns:
        list[str]: List of file paths for runs-on-change migrations.
    """
    from dbwarden.engine.checksum import calculate_checksum
    from dbwarden.repositories import (
        get_existing_runs_on_change_filenames_to_checksums,
    )

    filepaths = []

    if not os.path.exists(directory):
        return []

    existing_checksums = (
        get_existing_runs_on_change_filenames_to_checksums(db_name)
        if changed_only
        else {}
    )
    existing_checksums = {PureWindowsPath(name).name: checksum for name, checksum in existing_checksums.items()}
    for filename in sorted(os.listdir(directory)):
        match = RUNS_ON_CHANGE_PATTERN.match(filename)
        if match:
            filepath = os.path.join(directory, filename)
            _validate_migration_file(Path(filepath), Path(directory))
            if changed_only:
                if filename in existing_checksums:
                    from dbwarden.engine.file_parser import parse_upgrade_statements

                    statements = parse_upgrade_statements(filepath)
                    current_checksum = calculate_checksum(statements)
                    existing_checksum = existing_checksums[filename]
                    if current_checksum != existing_checksum:
                        filepaths.append(filepath)
                else:
                    filepaths.append(filepath)
            else:
                filepaths.append(filepath)

    return filepaths


def _validate_migration_file(path: Path, directory: Path) -> None:
    """Reject migration files that escape or symlink outside the directory."""
    resolved_directory = directory.resolve()
    resolved_path = path.resolve()
    if not resolved_path.is_relative_to(resolved_directory):
        raise DirectoryNotFoundError(
            f"Migration file '{path}' resolves outside the migrations directory."
        )
    if path.is_symlink():
        raise DirectoryNotFoundError(
            f"Migration file '{path}' must not be a symbolic link."
        )
    if not path.is_file():
        raise DirectoryNotFoundError(f"Migration file '{path}' is not a regular file.")


def get_all_repeatable_filepaths(
    directory: str, db_name: str | None = None
) -> dict[str, list[str]]:
    """
    Get all repeatable migration file paths (both RA__ and ROC__).

    Args:
        directory: Path to migrations directory.
        db_name: Database name for getting existing checksums.

    Returns:
        dict[str, list[str]]: Dictionary with 'runs_always' and 'runs_on_change' keys.
    """
    return {
        "runs_always": get_runs_always_filepaths(directory),
        "runs_on_change": get_runs_on_change_filepaths(directory, db_name=db_name),
    }


def get_next_migration_number(directory: str) -> str:
    """
    Get the next migration number for a new migration.

    Args:
        directory: Path to migrations directory.

    Returns:
        str: Next migration number as 4-digit string.
    """
    existing_migrations = {
        match.group(1): path for path in Path(directory).glob("*.sql")
        if (match := MIGRATION_PATTERN.match(path.name))
    }
    if not existing_migrations:
        return "0001"

    existing_numbers = []
    for version in existing_migrations.keys():
        if version.isdigit():
            existing_numbers.append(int(version))

    if existing_numbers:
        next_num = max(existing_numbers) + 1
    else:
        next_num = 1

    return f"{next_num:04d}"


def get_all_migrations_with_metadata(
    directory: str,
) -> list[tuple[str, str, list[str], bool]]:
    """
    Get all migration files with their metadata.

    Returns:
        list[tuple]: [(version, filepath, depends_on, is_seed), ...]
    """
    from dbwarden.engine.file_parser import parse_migration_header

    migrations: list[tuple[str, str, list[str], bool]] = []

    if not os.path.exists(directory):
        return []

    for filename in sorted(os.listdir(directory)):
        match = MIGRATION_PATTERN.match(filename)
        if match:
            version = match.group(1)
            filepath = os.path.join(directory, filename)
            metadata = parse_migration_header(filepath)
            migrations.append(
                (version, filepath, metadata.depends_on, metadata.is_seed)
            )

    return migrations


@dataclass
class DependencyError:
    """A dependency validation error."""

    version: str
    error_type: str  # "missing_target", "circular", "unmet_applied_dep", "superseded_dep"
    message: str
    missing: list[str] = field(default_factory=list)


def validate_dependencies(
    directory: str,
    applied_versions: set[str],
) -> list[DependencyError]:
    """Validate migration dependency integrity.

    Checks that:
    1. Every depends_on target exists as a migration file
    2. No circular dependencies exist
    3. Applied migrations have their deps also applied
    4. Dependencies on superseded migrations are flagged

    Args:
        directory: Path to migrations directory.
        applied_versions: Set of already applied migration versions.

    Returns:
        List of DependencyError if any issues found, empty list if valid.
    """
    from dbwarden.engine.file_parser import parse_migration_header
    from dbwarden.merge.marker import is_superseded

    errors: list[DependencyError] = []
    all_migrations = get_all_migrations_with_metadata(directory)
    all_versions = {m[0] for m in all_migrations}
    filepath_by_version = {m[0]: m[1] for m in all_migrations}

    # Check 1: Every depends_on target exists as a migration file
    for version, filepath, deps, seed in all_migrations:
        for dep in deps:
            if dep not in all_versions:
                errors.append(
                    DependencyError(
                        version=version,
                        error_type="missing_target",
                        message=f"Migration {version} depends on {dep}, but no migration file exists for version {dep}",
                        missing=[dep],
                    )
                )

    # Check 2: Dependencies on superseded migrations
    for version, filepath, deps, seed in all_migrations:
        for dep in deps:
            if dep in all_versions:
                dep_filepath = filepath_by_version.get(dep)
                if dep_filepath and is_superseded(Path(dep_filepath)):
                    errors.append(
                        DependencyError(
                            version=version,
                            error_type="superseded_dep",
                            message=f"Migration {version} depends on {dep}, which is superseded",
                            missing=[dep],
                        )
                    )

    # Check 3: Applied migrations have their deps also applied
    for version, filepath, deps, seed in all_migrations:
        if version in applied_versions:
            for dep in deps:
                if dep not in applied_versions:
                    errors.append(
                        DependencyError(
                            version=version,
                            error_type="unmet_applied_dep",
                            message=(
                                f"Migration {version} is applied but its dependency {dep} "
                                f"is not applied. This indicates an inconsistent migration history."
                            ),
                            missing=[dep],
                        )
                    )

    # Check 4: Circular dependencies (quick cycle detection)
    # Build adjacency list for pending migrations only
    pending = {m[0]: m[2] for m in all_migrations if m[0] not in applied_versions}

    def _detect_cycle(start: str, visited: set[str], stack: set[str]) -> list[str] | None:
        """DFS cycle detection. Returns cycle path if found."""
        visited.add(start)
        stack.add(start)
        for dep in pending.get(start, []):
            if dep not in pending:
                continue  # dep is applied or doesn't exist (already caught above)
            if dep not in visited:
                cycle = _detect_cycle(dep, visited, stack)
                if cycle:
                    return cycle
            elif dep in stack:
                return [dep, start]
        stack.discard(start)
        return None

    visited: set[str] = set()
    for version in pending:
        if version not in visited:
            cycle = _detect_cycle(version, visited, set())
            if cycle:
                errors.append(
                    DependencyError(
                        version=cycle[-1],
                        error_type="circular",
                        message=f"Circular dependency detected: {' -> '.join(cycle)}",
                        missing=cycle,
                    )
                )

    return errors


def resolve_migration_order(
    directory: str, applied_versions: set[str]
) -> list[tuple[str, str, list[str], bool]]:
    """
    Resolve migration order based on dependencies.

    Args:
        directory: Path to migrations directory.
        applied_versions: Set of already applied migration versions.

    Returns:
        list[tuple]: [(version, filepath, depends_on, is_seed), ...] in execution order.
    """
    all_migrations = get_all_migrations_with_metadata(directory)

    pending = [
        (v, fp, deps, seed)
        for v, fp, deps, seed in all_migrations
        if v not in applied_versions
    ]

    resolved: list[tuple[str, str, list[str], bool]] = []
    remaining = pending.copy()
    iterations = 0
    max_iterations = len(pending) * 2

    while remaining and iterations < max_iterations:
        iterations += 1
        for migration in remaining[:]:
            version, filepath, deps, seed = migration
            deps_met = all(
                d in applied_versions or d in [m[0] for m in resolved] for d in deps
            )
            if deps_met:
                resolved.append(migration)
                remaining.remove(migration)

    if remaining:
        # Build detailed error info
        all_versions = {m[0] for m in all_migrations}
        unresolved_versions = [m[0] for m in remaining]
        details = []
        for m in remaining:
            version, filepath, deps, seed = m
            unmet = [
                d
                for d in deps
                if d not in applied_versions
                and d not in [mm[0] for mm in resolved]
            ]
            missing_targets = [d for d in unmet if d not in all_versions]
            applied_deps = [d for d in deps if d in applied_versions and d not in unmet]
            details.append(
                f"  {version}: unmet={unmet}"
                + (f", missing_files={missing_targets}" if missing_targets else "")
                + (f", already_applied={applied_deps}" if applied_deps else "")
            )

        raise ValueError(
            f"Cannot resolve migration dependencies.\n"
            f"Unresolved migrations: {unresolved_versions}\n"
            + "\n".join(details)
        )

    return resolved


def parse_version_string(version: str) -> tuple[int, ...]:
    """Parse a version string into a tuple of integers."""
    return tuple(int(x) for x in version.split("."))


def compare_versions(v1: str, v2: str) -> int:
    """
    Compare two version strings.

    Returns:
        -1 if v1 < v2, 0 if v1 == v2, 1 if v1 > v2
    """
    p1 = parse_version_string(v1)
    p2 = parse_version_string(v2)

    if p1 < p2:
        return -1
    elif p1 > p2:
        return 1
    return 0


def generate_migration_filename(db_name: str, description: str, version: str) -> str:
    """
    Generate a migration filename with database name prefix.

    Args:
        db_name: Database name.
        description: Migration description.
        version: Migration version number.

    Returns:
        str: Migration filename (e.g., "primary__0001_create_users.sql")
    """
    safe_description = re.sub(r"[^a-zA-Z0-9_]", "_", description.lower())
    safe_description = re.sub(r"_+", "_", safe_description)
    safe_description = safe_description.strip("_")
    if not safe_description:
        safe_description = "untitled"
    return f"{db_name}__{version}_{safe_description}.sql"


def generate_repeatable_filename(db_name: str, description: str, prefix: str) -> str:
    """
    Generate a repeatable migration filename with database name prefix.

    Args:
        db_name: Database name.
        description: Migration description.
        prefix: RA__ or ROC__ prefix.

    Returns:
        str: Filename (e.g., "primary__RA__seed_data.sql")
    """
    safe_description = re.sub(r"[^a-zA-Z0-9_]", "_", description.lower())
    safe_description = re.sub(r"_+", "_", safe_description)
    safe_description = safe_description.strip("_")
    if not safe_description:
        safe_description = "untitled"
    return f"{db_name}__{prefix}{safe_description}.sql"
