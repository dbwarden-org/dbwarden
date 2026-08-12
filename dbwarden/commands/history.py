from dbwarden.exceptions import DBDisconnectedError
from dbwarden.logging import get_logger
from dbwarden.output import data_table, emit_json, json_mode, render, warning
from dbwarden.repositories import get_migration_records, migrations_table_exists


def history_cmd(database: str | None = None) -> None:
    """Display the migration history in a formatted table."""
    logger = get_logger()

    db_name = database or "default"
    payload: dict = {"database": db_name, "migrations": []}

    try:
        table_exists = migrations_table_exists(database)
    except DBDisconnectedError:
        raise

    if not table_exists:
        if json_mode():
            emit_json(payload)
            return
        warning(f"No migrations have been applied to '{db_name}' yet.")
        return

    migration_records = get_migration_records(database)
    if not migration_records:
        if json_mode():
            emit_json(payload)
            return
        warning(f"No migrations have been applied to '{db_name}' yet.")
        return

    payload["migrations"] = [
        {
            "version": record.version or "N/A",
            "order_executed": record.order_executed,
            "description": record.description,
            "applied_at": record.applied_at,
            "migration_type": record.migration_type,
        }
        for record in migration_records
    ]

    if json_mode():
        emit_json(payload)
        return

    render(
        data_table(
            f"Migration History - {db_name}",
            ("Version", "Order Executed", "Description", "Applied At", "Type"),
            (
                (
                    entry["version"],
                    entry["order_executed"],
                    entry["description"],
                    entry["applied_at"],
                    entry["migration_type"],
                )
                for entry in payload["migrations"]
            ),
        )
    )
    logger.info(f"Total migrations applied: {len(migration_records)}")
