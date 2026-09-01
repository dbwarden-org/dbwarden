from dbwarden.engine.version import get_migrations_directory
from dbwarden.exceptions import DBDisconnectedError
from dbwarden.logging import get_logger
from dbwarden.output import (
    data_table,
    emit_json,
    error,
    json_mode,
    kv_table,
    render,
    section,
    warning,
)
from dbwarden.repositories import (
    get_migrated_versions,
    migrations_table_exists,
)


def _status_payload(database: str | None = None) -> dict:
    """Build the structured status payload for a single database."""
    db_name = database or "default"
    payload: dict = {"database": db_name, "migrations": [], "summary": {}}

    try:
        migrations_dir = get_migrations_directory(database)
    except Exception:
        payload["error"] = (
            f"Migrations directory not found for database '{db_name}'. Run 'dbwarden init' first."
        )
        return payload

    applied_versions: list[str] = []
    try:
        if migrations_table_exists(database):
            applied_versions = get_migrated_versions(database)
    except DBDisconnectedError:
        raise

    from dbwarden.engine.version import get_migration_filepaths_by_version

    all_migrations = get_migration_filepaths_by_version(directory=migrations_dir)
    payload["migrations"] = [
        {
            "version": version,
            "filename": filepath.split("/")[-1],
            "status": "applied" if version in applied_versions else "pending",
        }
        for version, filepath in all_migrations.items()
    ]
    payload["summary"] = {
        "applied": len(applied_versions),
        "pending": len([v for v in all_migrations if v not in applied_versions]),
        "total": len(all_migrations),
    }

    # Phase 2: Add merge signal detection
    from dbwarden.merge.detection import detect_merge_signals
    signals = detect_merge_signals(database)
    if signals:
        payload["merge_signals"] = [s.value for s in signals]
        payload["merge_status"] = "MERGE_PENDING"
    else:
        payload["merge_status"] = "CLEAN"

    return payload


def status_single(database: str | None = None) -> None:
    """Display migration status for a single database."""
    logger = get_logger()

    db_name = database or "default"
    payload = _status_payload(database)

    if json_mode():
        emit_json(payload)
        return

    if "error" in payload:
        warning(payload["error"])
        return

    applied_versions = [
        entry["version"] for entry in payload["migrations"] if entry["status"] == "applied"
    ]
    pending_versions = [
        entry["version"] for entry in payload["migrations"] if entry["status"] == "pending"
    ]

    # Phase 2: Show merge status
    merge_status = payload.get("merge_status", "CLEAN")
    merge_signals = payload.get("merge_signals", [])

    render(
        data_table(
            f"Migration Status - {db_name}",
            ("Status", "Version", "Filename"),
            (
                (
                    entry["status"].capitalize(),
                    entry["version"],
                    entry["filename"],
                )
                for entry in payload["migrations"]
            ),
        )
    )

    summary_data = {
        "Applied": payload["summary"]["applied"],
        "Pending": payload["summary"]["pending"],
        "Total": payload["summary"]["total"],
    }

    # Add merge status to summary
    if merge_status == "MERGE_PENDING":
        summary_data["Merge"] = "PENDING"
    else:
        summary_data["Merge"] = "CLEAN"

    render(kv_table("Summary", summary_data))

    if merge_status == "MERGE_PENDING":
        warning(f"Merge detected: {', '.join(merge_signals)}")
        info("Run 'dbwarden merge' to reconcile before generating migrations.")

    if pending_versions:
        logger.info(f"Pending migrations: {', '.join(pending_versions)}")


def status_cmd(
    database: str | None = None,
    all_databases: bool = False,
    all_environments: bool = False,
) -> None:
    """Display migration status: applied and pending migrations.

    Args:
        database: Target database name.
        all_databases: Show status for all configured databases.
        all_environments: Show status for all registered environments.
    """
    # R4.4: --all-environments shows per-environment status
    if all_environments:
        _show_all_environments_status(database)
        return

    if all_databases:
        from dbwarden.connection.availability import (
            DatabaseAvailability,
            MultiDatabaseResult,
            probe_database,
        )
        from dbwarden.config import get_multi_db_config

        config = get_multi_db_config()
        result = MultiDatabaseResult()
        payloads: list[dict] = []
        for db_name, db_config in config.databases.items():
            availability = probe_database(db_name, optional=True, config=db_config)
            if availability.skipped:
                result.skipped.append(availability)
                continue
            if not availability.available:
                result.failed.append(availability)
                continue
            try:
                payloads.append(_status_payload(db_name))
                result.succeeded.append(db_name)
            except Exception as exc:
                result.failed.append(
                    DatabaseAvailability(
                        database=db_name,
                        available=False,
                        error_code="status_failed",
                        message=str(exc),
                    )
                )

        if json_mode():
            if result.skipped or result.failed:
                emit_json({**result.as_dict(), "databases": payloads})
            else:
                for payload in payloads:
                    emit_json(payload)
        else:
            for payload in payloads:
                db_name = payload["database"]
                section(db_name)
                _render_status_payload(payload)
            for item in result.skipped:
                warning(f"{item.database}: skipped, connection failed after retries")
            for item in result.failed:
                error(f"Error getting status for database '{item.database}': {item.message}")

        if result.failed:
            raise RuntimeError(
                "Status failed for "
                f"{len(result.failed)} database(s)"
            )
        if result.skipped:
            import typer
            raise typer.Exit(code=result.exit_code)
    else:
        status_single(database)


def _render_status_payload(payload: dict) -> None:
    if "error" in payload:
        warning(payload["error"])
        return
    applied_versions = [
        entry["version"] for entry in payload["migrations"] if entry["status"] == "applied"
    ]
    pending_versions = [
        entry["version"] for entry in payload["migrations"] if entry["status"] == "pending"
    ]

    # Phase 2: Show merge status
    merge_status = payload.get("merge_status", "CLEAN")
    merge_signals = payload.get("merge_signals", [])

    render(
        data_table(
            f"Migration Status - {payload['database']}",
            ("Status", "Version", "Filename"),
            ((entry["status"].capitalize(), entry["version"], entry["filename"])
             for entry in payload["migrations"]),
        )
    )

    summary_data = {
        "Applied": payload["summary"]["applied"],
        "Pending": payload["summary"]["pending"],
        "Total": payload["summary"]["total"],
    }

    # Add merge status to summary
    if merge_status == "MERGE_PENDING":
        summary_data["Merge"] = "PENDING"
    else:
        summary_data["Merge"] = "CLEAN"

    render(kv_table("Summary", summary_data))

    if merge_status == "MERGE_PENDING":
        warning(f"Merge detected: {', '.join(merge_signals)}")
        info("Run 'dbwarden merge' to reconcile before generating migrations.")

    if pending_versions:
        get_logger().info(f"Pending migrations: {', '.join(pending_versions)}")


def _show_all_environments_status(database: str | None = None) -> None:
    """Show status for all registered environments (R4.4)."""
    from dbwarden.merge.environments import load_environments

    envs = load_environments(database)

    if not envs:
        info("No environments registered.")
        return

    if json_mode():
        payloads = []
        for env_name, env_config in envs.items():
            payload = {
                "environment": env_name,
                "persistent": env_config.persistent,
                "status": "registered",
            }
            payloads.append(payload)
        emit_json({"environments": payloads})
        return

    section("Environment Status")
    for env_name, env_config in envs.items():
        persistent_str = "persistent" if env_config.persistent else "disposable"
        info(f"  {env_name}: {persistent_str}")

    info("")
    info("To check database status for an environment, set the URL environment variable")
    info("and run: dbwarden status --database <name>")
