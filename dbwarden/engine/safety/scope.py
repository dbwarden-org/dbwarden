from __future__ import annotations

from dataclasses import dataclass

from dbwarden.engine.safety.classifiers import exceeds
from dbwarden.engine.safety.plans import file_severity
from dbwarden.logging import get_component_logger
from dbwarden.output import info, warning


@dataclass(frozen=True)
class DeferredStop:
    version: str
    filepath: str
    severity: str
    ceiling: str
    remaining: int
    reason: str = ""


def severity_prefix(
    files: dict[str, str], ceiling: str
) -> tuple[dict[str, str], DeferredStop | None]:
    allowed: dict[str, str] = {}
    for version, filepath in sorted(files.items()):
        level, reason = file_severity(filepath)
        if exceeds(level, ceiling):
            return allowed, DeferredStop(
                version,
                filepath,
                level.value,
                ceiling,
                len(files) - len(allowed),
                reason,
            )
        allowed[version] = filepath
    return allowed, None


def report_stop(stop: DeferredStop, database: str, *, dry_run: bool = False) -> None:
    info(
        f"{'Would stop' if dry_run else 'Stopped'}: {stop.filepath} has file severity {stop.severity}, above ceiling {stop.ceiling}."
    )
    if stop.reason:
        info(
            f"Reason: {stop.reason}. hint: run dbwarden check --write-plan or raise the ceiling."
        )
    info(
        f"{stop.remaining} migration(s) deferred. hint: run with a higher --max-severity to apply."
    )
    if not dry_run:
        get_component_logger("safety").info(
            "migration_deferred",
            extra={
                "event": "migration_deferred",
                "database": database,
                "ceiling": stop.ceiling,
                "stopping_file": stop.filepath,
                "file_severity": stop.severity,
                "remaining_pending": stop.remaining,
            },
        )
        import time
        from pathlib import Path

        from dbwarden.metrics import increment_migrations_deferred, set_deferred_age

        increment_migrations_deferred(database, stop.severity)
        set_deferred_age(
            database,
            stop.version,
            max(0, time.time() - Path(stop.filepath).stat().st_mtime),
        )


def filter_repeatables(
    files: list[str], ceiling: str, database: str, *, dry_run: bool = False
) -> list[str]:
    result = []
    for filepath in files:
        level, reason = file_severity(filepath)
        if exceeds(level, ceiling):
            warning(
                f"Skipping repeatable {filepath}: severity {level.value} exceeds {ceiling}. {reason}"
            )
            if not dry_run:
                get_component_logger("safety").warning(
                    "repeatable_skipped_severity",
                    extra={
                        "event": "repeatable_skipped_severity",
                        "database": database,
                        "ceiling": ceiling,
                        "stopping_file": filepath,
                        "file_severity": level.value,
                    },
                )
        else:
            result.append(filepath)
    return result
