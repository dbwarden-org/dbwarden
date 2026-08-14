from __future__ import annotations

import os
from pathlib import Path

from dbwarden import __version__
from dbwarden.config import ConfigurationError, get_database, get_multi_db_config
from dbwarden.engine.model_discovery import (
    get_all_model_tables,
    filter_model_tables_by_name,
    validate_model_tables_exist,
)
from dbwarden.engine.core.model_state import model_state_json_dumps
from dbwarden.engine.offline import model_state_to_dict
from dbwarden.output import success
from dbwarden.files import atomic_write_text


def export_models_cmd(
    output: str | None = None,
    database: str | None = None,
) -> None:
    """Export current model definitions to a JSON state file for offline diffs.

    Args:
        output: Path to write the model state JSON file.
        database: Target database name.
    """
    config = get_database(database)
    try:
        db_name = database or get_multi_db_config().default
    except ConfigurationError:
        db_name = database or "default"
    model_paths = config.model_paths

    if not model_paths:
        from dbwarden.commands.make_migrations import auto_discover_model_paths
        model_paths = auto_discover_model_paths()

    if not model_paths:
        msg = "No model paths found. Set model_paths in database_config(...) or ensure models/ directory exists."
        raise ValueError(msg)

    tables = get_all_model_tables(model_paths, db_name=database)
    validate_model_tables_exist(tables, config.model_tables, db_name)
    tables = filter_model_tables_by_name(tables, config.model_tables)
    state = model_state_to_dict(tables, dbwarden_version=__version__)

    if output is None:
        from dbwarden.commands.make_migrations import get_model_state_path

        out_path = get_model_state_path(db_name)
    else:
        out_path = Path(output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    file_state = dict(state)
    if output is None:
        file_state["database"] = db_name
    payload = model_state_json_dumps(file_state)

    from dbwarden.commands.make_migrations import get_model_state_path

    legacy_path = get_model_state_path(db_name, legacy=True)
    if legacy_path != out_path:
        legacy_path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(legacy_path, payload)
    atomic_write_text(out_path, payload)

    success(f"Exported {len(tables)} model(s) to {out_path}")
