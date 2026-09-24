from __future__ import annotations

import json

from dbwarden import __version__
from dbwarden.files import atomic_write_text, preserve_files_on_error
from dbwarden.logging import get_logger


def _write_migration_snapshot(
    db_name: str | None = None,
    migration_id: str = "",
) -> None:
    from dbwarden.connection.connection import _sandbox_url_var

    if _sandbox_url_var.get() is not None:
        return
    from dbwarden.engine.core.snapshot_io import write_snapshot
    from dbwarden.engine.snapshot.extract import extract_full_schema_snapshot

    try:
        from dbwarden.config import get_multi_db_config
        db_name = db_name or get_multi_db_config().default
        snapshot = extract_full_schema_snapshot(database=db_name)
        from dbwarden.commands.make_migrations.pipeline import (
            get_current_model_state_path,
        )
        from dbwarden.engine.generation_state import effective_state
        from dbwarden.engine.version import (
            get_migration_filepaths_by_version,
            get_migrations_directory,
        )
        from dbwarden.repositories import get_migrated_versions

        snapshot["source"] = "applied"
        snapshot["applied_versions"] = get_migrated_versions(db_name)
        state_path = get_current_model_state_path(db_name)
        if state_path.exists():
            recorded = json.loads(state_path.read_text(encoding="utf-8"))
            if "generation_base" in recorded:
                files = get_migration_filepaths_by_version(get_migrations_directory(db_name))
                applied_files = {v: p for v, p in files.items() if v in snapshot["applied_versions"] and v not in recorded.get("generation_applied", [])}
                try:
                    snapshot["model_state"], _ = effective_state(recorded, applied_files, strict=True)
                except (ValueError, KeyError, TypeError, OSError):
                    get_logger(db_name=db_name).warning("Typed checkpoint unavailable; preserving the live schema snapshot", exc_info=True)
        filepath = write_snapshot(
            snapshot,
            database=db_name,
            migration_id=migration_id,
        )
        from dbwarden.data.snapshots import register_snapshot
        from dbwarden.config import get_database
        from pathlib import Path
        stored = json.loads(Path(filepath).read_text(encoding="utf-8"))
        register_snapshot(stored, registry_path=get_database(db_name).snapshot_registry)
        logger = get_logger(db_name=db_name)
        logger.info(f"Schema snapshot written: {filepath}")
    except Exception:
        logger = get_logger(db_name=db_name)
        logger.warning("Failed to write schema snapshot", exc_info=True)


def _write_model_state(
    config=None,
    db_name: str | None = None,
) -> None:
    """Export current model definitions to a database-specific model state file."""
    from dbwarden.connection.connection import _sandbox_url_var

    if _sandbox_url_var.get() is not None:
        return
    if config is None:
        from dbwarden.config import get_database
        config = get_database(db_name)

    model_paths = config.model_paths
    if not model_paths:
        return

    try:
        from dbwarden.config import get_multi_db_config
        db_name = db_name or get_multi_db_config().default
        from dbwarden.engine.discovery import (
            filter_model_tables_by_name,
            get_all_model_tables,
            validate_model_tables_exist,
        )
        from dbwarden.engine.offline import model_state_to_dict

        tables = get_all_model_tables(model_paths, db_name=db_name)
        validate_model_tables_exist(tables, config.model_tables, db_name or "default")
        tables = filter_model_tables_by_name(tables, config.model_tables)
        state = model_state_to_dict(tables, dbwarden_version=__version__)
        from dbwarden.engine.generation_state import configuration_state
        state = configuration_state(state, config, desired=True)
        from dbwarden.commands.make_migrations import get_model_state_path

        legacy_path = get_model_state_path(db_name, legacy=True)
        state_path = get_model_state_path(db_name)
        state_path.parent.mkdir(parents=True, exist_ok=True)
        file_state = dict(state)
        file_state["database"] = db_name or "default"
        from dbwarden.engine.core.model_state import model_state_json_dumps
        payload = model_state_json_dumps(file_state)
        with preserve_files_on_error({legacy_path, state_path}):
            if legacy_path != state_path:
                legacy_path.parent.mkdir(parents=True, exist_ok=True)
                atomic_write_text(legacy_path, payload)
            atomic_write_text(state_path, payload)
        logger = get_logger(db_name=db_name)
        logger.info(f"Model state written: {state_path}")
    except Exception:
        logger = get_logger(db_name=db_name)
        logger.warning("Failed to write model state", exc_info=True)
