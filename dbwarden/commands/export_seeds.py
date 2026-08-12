from __future__ import annotations


def export_seeds_cmd(
    database: str | None = None,
    all_databases: bool = False,
    output_dir: str = "seeds",
) -> None:
    from dbwarden.plugin import HookRegistry

    if HookRegistry.is_registered("seed_export"):
        if all_databases:
            from dbwarden.database.availability import run_all_databases

            result = run_all_databases(
                lambda db_name: HookRegistry.execute_single(
                    "seed_export",
                    database=db_name,
                    all_databases=False,
                    output_dir=output_dir,
                )
            )
            from dbwarden.commands.seeds import _finish_multi_database_result
            _finish_multi_database_result(result, "Seed export")
            return
        HookRegistry.execute_single(
            "seed_export",
            database=database,
            all_databases=all_databases,
            output_dir=output_dir,
        )
        return

    raise RuntimeError(
        "Seed export requires dbwarden-seeds plugin. "
        "Install it: `dbwarden plugin add dbwarden-seeds`"
    )
