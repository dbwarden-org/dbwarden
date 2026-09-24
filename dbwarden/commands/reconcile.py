"""dbwarden reconcile command.

Recovers a persistent environment after a dirty merge.
Implements the merge spec §8.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from dbwarden import __version__
from dbwarden.config import get_database, get_multi_db_config
from dbwarden.files import atomic_write_text
from dbwarden.logging import get_component_logger
from dbwarden.merge.environments import is_persistent, load_environments
from dbwarden.merge.reconciliation import load_merge_record
from dbwarden.output import info, success, warning

logger = get_component_logger("merge")


def reconcile_cmd(
    environment: str,
    database: str | None = None,
    rename_columns: list[str] | None = None,
    dry_run: bool = False,
    verbose: bool = False,
    force: bool = False,
    split_at_severity: str | None = None,
) -> None:
    """Recover a persistent environment after a dirty merge.

    This command reconciles a persistent environment that applied
    superseded migrations by generating an environment-specific
    reconciliation migration.

    Args:
        environment: Environment name to reconcile.
        database: Target database name.
        rename_columns: Column renames to confirm.
        dry_run: Only show what would happen, don't apply.
        verbose: Enable verbose logging.
        force: Acknowledge operation risks in reconciliation.
        split_at_severity: Split reconciliation by severity.
    """
    from sqlalchemy.engine import make_url

    from dbwarden.commands.make_migrations.generation import (
        append_data_contract,
        data_expansion_state,
        diff_states,
        generate_files,
        include_data_operations,
    )
    from dbwarden.commands.make_migrations.pipeline import get_current_model_state_path
    from dbwarden.connection.connection import sandbox_override
    from dbwarden.engine.offline import diff_model_states

    if not is_persistent(environment, database):
        warning(
            f"Environment '{environment}' is not registered as persistent. hint: use rebase for disposable environments"
        )
        return
    env = next(
        (item for item in load_environments(database) if item.name == environment), None
    )
    url = os.environ.get(env.url_env) if env else None
    if not url:
        raise ValueError(
            f"No URL configured for environment {environment}. hint: set its url_env variable"
        )
    config = get_database(database)
    backend = make_url(url).get_backend_name()
    if backend not in (
        config.database_type,
        "clickhousedb"
        if config.database_type == "clickhouse"
        else config.database_type,
    ):
        raise ValueError("Environment backend differs from database configuration")
    db_name = database or get_multi_db_config().default
    state_path = get_current_model_state_path(db_name)
    if not state_path.exists():
        raise ValueError(
            "No merged model state. hint: run merge or export-models first"
        )
    target = json.loads(state_path.read_text(encoding="utf-8"))
    with sandbox_override(url, config.database_type):
        snapshot = _snapshot_environment(environment, database)
        if snapshot is None:
            raise ValueError(
                "Could not snapshot environment. hint: verify its connection settings"
            )
        from dbwarden.engine.generation_state import configuration_state

        snapshot = configuration_state(snapshot, config)
        target = configuration_state(target, config, desired=True)
        upgrade, rollback = diff_states(
            snapshot,
            target,
            db_name=db_name,
            rename_columns=[
                value.replace("=", ":", 1) for value in rename_columns or []
            ],
        )
        directory = _get_reconciliation_dir(environment)
        from dbwarden.connection import get_db_connection
        from dbwarden.data.integration import applied_data_spec, load_data_plan
        from dbwarden.data.planning import plan_data, previous_data_spec
        from dbwarden.engine.version import get_migrations_directory

        normal_paths = sorted(Path(get_migrations_directory(db_name)).glob("*.sql"))
        history_paths = [*normal_paths, *sorted(directory.glob("*.sql"))]
        data_spec = previous_data_spec(
            {path.name: path for path in normal_paths},
            database=db_name,
            backend=config.database_type,
        )
        with get_db_connection(db_name) as connection:
            previous_data = applied_data_spec(
                connection,
                history_paths,
                database=db_name,
                backend=config.database_type,
            )
        data_ops = plan_data(data_spec, previous_data)
        expanded = data_expansion_state(snapshot, target, data_ops)
        if expanded != target:
            upgrade, rollback = diff_states(
                snapshot,
                expanded,
                db_name=db_name,
                rename_columns=[
                    value.replace("=", ":", 1) for value in rename_columns or []
                ],
            )
        upgrade = include_data_operations(upgrade, data_ops)
        append_data_contract(upgrade, snapshot, expanded, target, db_name=db_name)
        artifacts = generate_files(
            upgrade,
            rollback,
            migrations_dir=str(directory),
            database=database,
            db_name=db_name,
            description=f"reconcile {environment}",
            baseline=snapshot,
            target=target,
            threshold=split_at_severity
            if split_at_severity is not None
            else config.split_at_severity,
            header=f"-- Generated by: dbwarden reconcile (dbwarden {__version__})\n",
            write=not dry_run,
            data_spec=data_spec,
        )
        for artifact in artifacts:
            info(
                f"{'Would generate' if dry_run else 'Generated'} {artifact['filename']} [{artifact['plan']['severity']['file']}]"
            )
        if dry_run:
            return
        from dbwarden.commands.migrate import migrate_single

        def complete(connection):
            from dbwarden.engine.snapshot import extract_full_schema_snapshot

            actual_snapshot = extract_full_schema_snapshot(database=db_name)
            actual = configuration_state(actual_snapshot, config)
            remaining, _ = diff_model_states(actual, target, db_name=db_name)
            if remaining:
                raise RuntimeError(
                    f"Reconciliation did not converge: {len(remaining)} operations remain. hint: inspect dbwarden diff"
                )
            from dbwarden.data.convergence import check_convergence

            data_plans = [
                plan
                for path in [*normal_paths, *sorted(directory.glob("*.sql"))]
                if (plan := load_data_plan(path, db_name)) is not None
            ]
            findings = check_convergence(connection, data_spec, plans=data_plans)
            if findings:
                raise RuntimeError(
                    f"Data reconciliation did not converge: {len(findings)} findings. hint: inspect dbwarden check --data"
                )
            _update_merge_record(
                environment, str(directory), database=db_name, connection=connection
            )
            from dbwarden.engine.core.snapshot_io import write_snapshot
            from dbwarden.repositories import get_migrated_versions

            applied = get_migrated_versions(db_name)
            actual_snapshot.update(
                source="applied", model_state=target, applied_versions=applied
            )
            write_snapshot(
                actual_snapshot,
                database=db_name,
                migration_id=max(applied, default="0000"),
            )

        migrate_single(
            db_name=db_name,
            reconciliation_dir=str(directory),
            reconciliation_complete=complete,
            force=force,
            max_severity="CRITICAL",
        )
    success(f"Reconciliation for environment '{environment}' complete.")


def _snapshot_environment(environment: str, database: str | None) -> dict | None:
    from dbwarden.engine.snapshot import extract_full_schema_snapshot

    env = next(
        (item for item in load_environments(database) if item.name == environment), None
    )
    url = os.environ.get(env.url_env) if env else None
    if not url:
        return None
    return extract_full_schema_snapshot(
        sqlalchemy_url=url, database_type=get_database(database).database_type
    )


def _get_reconciliation_dir(environment: str) -> Path:
    base = Path(".dbwarden") / "reconciliations"
    path = base / environment
    if (
        not environment
        or not path.resolve().is_relative_to(base.resolve())
        or path.resolve() == base.resolve()
    ):
        raise ValueError("Invalid environment path")
    return path


def _update_merge_record(
    environment: str, reconciliation_file: str, *, database=None, connection=None
) -> None:
    from dbwarden.engine.file_parser import parse_upgrade_statements
    from dbwarden.engine.version import get_migrations_directory
    from dbwarden.repositories import get_migrated_versions
    from dbwarden.repositories.migrations_repo import _record_upgrade

    applied = set(get_migrated_versions(database))
    directory = Path(get_migrations_directory(database))
    for path in sorted(Path(".dbwarden/merges").glob("*.json"), reverse=True):
        record = load_merge_record(path)
        if record.get("database", database) == database and environment in record.get(
            "probe_results", {}
        ):
            for version in record.get("reconciliation_versions", []):
                if version not in applied:
                    candidates = list(directory.glob(f"*__{version}_*.sql"))
                    if len(candidates) != 1:
                        raise ValueError(
                            f"Cannot record reconciled version {version}: expected one migration"
                        )
                    _record_upgrade(
                        version=version,
                        filename=candidates[0].name,
                        migration_type="reconciliation",
                        sql_statements=parse_upgrade_statements(str(candidates[0])),
                        db_name=database,
                        connection=connection,
                    )
                    applied.add(version)
            if connection is not None:
                connection.commit()
            record["probe_results"][environment] = "reconciled"
            record.setdefault("environment_reconciliations", {})[environment] = (
                reconciliation_file
            )
            atomic_write_text(path, json.dumps(record, sort_keys=True, indent=2) + "\n")
