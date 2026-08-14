from __future__ import annotations

import json

from dbwarden import __version__
from dbwarden.logging import get_logger
from dbwarden.files import atomic_write_text


def _write_migration_snapshot(
    db_name: str | None = None,
    migration_id: str = "",
) -> None:
    from dbwarden.engine.core.snapshot_io import write_snapshot
    from dbwarden.engine.snapshot.extract import extract_full_schema_snapshot

    try:
        snapshot = extract_full_schema_snapshot(database=db_name)
        filepath = write_snapshot(
            snapshot,
            database=db_name,
            migration_id=migration_id,
        )
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
    if config is None:
        from dbwarden.config import get_database
        config = get_database(db_name)

    model_paths = config.model_paths
    if not model_paths:
        return

    try:
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
        from dbwarden.commands.make_migrations import get_model_state_path

        legacy_path = get_model_state_path(db_name, legacy=True)
        state_path = get_model_state_path(db_name)
        state_path.parent.mkdir(parents=True, exist_ok=True)
        file_state = dict(state)
        file_state["database"] = db_name or "default"
        from dbwarden.engine.core.model_state import model_state_json_dumps
        payload = model_state_json_dumps(file_state)
        if legacy_path != state_path:
            legacy_path.parent.mkdir(parents=True, exist_ok=True)
            atomic_write_text(legacy_path, payload)
        atomic_write_text(state_path, payload)
        logger = get_logger(db_name=db_name)
        logger.info(f"Model state written: {state_path}")
    except Exception:
        logger = get_logger(db_name=db_name)
        logger.warning("Failed to write model state", exc_info=True)
