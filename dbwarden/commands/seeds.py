from __future__ import annotations


def seed_create_cmd(
    description: str,
    seed_type: str = "sql",
    database: str | None = None,
    verbose: bool = False,
) -> None:
    from dbwarden.plugin import HookRegistry

    if HookRegistry.is_registered("seed_create"):
        HookRegistry.execute_single(
            "seed_create",
            description,
            seed_type=seed_type,
            database=database,
            verbose=verbose,
        )
        return

    raise RuntimeError(
        "Seed creation requires dbwarden-seeds plugin. "
        "Install it: `dbwarden plugin add dbwarden-seeds`"
    )


def seed_apply_cmd(
    version: str | None = None,
    dry_run: bool = False,
    database: str | None = None,
    all_databases: bool = False,
    verbose: bool = False,
) -> None:
    from dbwarden.plugin import HookRegistry

    if HookRegistry.is_registered("seed_apply"):
        if all_databases:
            from dbwarden.connection.availability import run_all_databases

            result = run_all_databases(
                lambda db_name: HookRegistry.execute_single(
                    "seed_apply",
                    version=version,
                    dry_run=dry_run,
                    database=db_name,
                    all_databases=False,
                    verbose=verbose,
                )
            )
            _finish_multi_database_result(result, "Seed application")
            return
        HookRegistry.execute_single(
            "seed_apply",
            version=version,
            dry_run=dry_run,
            database=database,
            all_databases=all_databases,
            verbose=verbose,
        )
        return

    raise RuntimeError(
        "Seed application requires dbwarden-seeds plugin. "
        "Install it: `dbwarden plugin add dbwarden-seeds`"
    )


def seed_list_cmd(
    database: str | None = None,
    all_databases: bool = False,
    verbose: bool = False,
    prune: bool = False,
) -> None:
    from dbwarden.plugin import HookRegistry

    if HookRegistry.is_registered("seed_list"):
        if all_databases:
            from dbwarden.connection.availability import run_all_databases

            result = run_all_databases(
                lambda db_name: HookRegistry.execute_single(
                    "seed_list",
                    database=db_name,
                    all_databases=False,
                    verbose=verbose,
                    prune=prune,
                )
            )
            _finish_multi_database_result(result, "Seed listing")
            return
        HookRegistry.execute_single(
            "seed_list",
            database=database,
            all_databases=all_databases,
            verbose=verbose,
            prune=prune,
        )
        return

    raise RuntimeError(
        "Seed listing requires dbwarden-seeds plugin. "
        "Install it: `dbwarden plugin add dbwarden-seeds`"
    )


def seed_rollback_cmd(
    count: int | None = None,
    to_version: str | None = None,
    database: str | None = None,
    all_databases: bool = False,
    verbose: bool = False,
) -> None:
    from dbwarden.plugin import HookRegistry

    if HookRegistry.is_registered("seed_rollback"):
        if all_databases:
            from dbwarden.connection.availability import run_all_databases

            result = run_all_databases(
                lambda db_name: HookRegistry.execute_single(
                    "seed_rollback",
                    count=count,
                    to_version=to_version,
                    database=db_name,
                    all_databases=False,
                    verbose=verbose,
                )
            )
            _finish_multi_database_result(result, "Seed rollback")
            return
        HookRegistry.execute_single(
            "seed_rollback",
            count=count,
            to_version=to_version,
            database=database,
            all_databases=all_databases,
            verbose=verbose,
        )
        return

    raise RuntimeError(
        "Seed rollback requires dbwarden-seeds plugin. "
        "Install it: `dbwarden plugin add dbwarden-seeds`"
    )


def _finish_multi_database_result(result, operation: str) -> None:
    from dbwarden.output import emit_json, error, json_mode, success, warning

    if json_mode():
        emit_json(result.as_dict())
    else:
        for item in result.skipped:
            warning(f"{item.database}: skipped, connection failed after retries")
        for item in result.failed:
            error(f"{operation} failed for '{item.database}': {item.message}")
        if result.skipped:
            success(f"{operation} completed with partial success.")
    if result.failed:
        raise RuntimeError(f"{operation} failed for {len(result.failed)} database(s)")
    if result.skipped:
        import typer
        raise typer.Exit(code=result.exit_code)
