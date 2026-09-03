from __future__ import annotations

import os
import re
from typing import Any

_VERSION_RE = re.compile(r"(\d+)")


def _parse_version(version: str) -> float:
    match = _VERSION_RE.search(version)
    if match:
        return float(match.group(1))
    return 0.0


def _noop(*args: Any, **kwargs: Any) -> None:
    pass


try:
    import prometheus_client

    _HAS_PROMETHEUS = True
except ImportError:
    _HAS_PROMETHEUS = False


if _HAS_PROMETHEUS:
    _REGISTRY = prometheus_client.CollectorRegistry()

    _migrations_total = prometheus_client.Counter(
        "dbwarden_migrations_total",
        "Total number of migrations applied",
        labelnames=["database", "version", "success"],
    )

    _migration_duration = prometheus_client.Histogram(
        "dbwarden_migration_duration_seconds",
        "Duration of individual migration steps in seconds",
        labelnames=["database", "version"],
        buckets=(
            0.01, 0.05, 0.1, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0, 60.0, float("inf")
        ),
    )

    _schema_version = prometheus_client.Gauge(
        "dbwarden_schema_version",
        "Applied schema version per database",
        labelnames=["database"],
    )

    _seed_version = prometheus_client.Gauge(
        "dbwarden_seed_version",
        "Applied seed version per database",
        labelnames=["database"],
    )

    _pending_migrations = prometheus_client.Gauge(
        "dbwarden_migrations_pending",
        "Number of pending migrations per database",
        labelnames=["database"],
    )

    _migration_errors = prometheus_client.Counter(
        "dbwarden_migration_errors_total",
        "Total number of migration failures",
        labelnames=["database"],
    )

    # Lock-specific metrics (Sec 12.4)
    _lock_acquisitions = prometheus_client.Counter(
        "dbwarden_lock_acquisitions_total",
        "Total number of lock acquisitions",
        labelnames=["database", "engine"],
    )

    _lock_acquire_wait = prometheus_client.Histogram(
        "dbwarden_lock_acquire_wait_seconds",
        "Time spent waiting to acquire lock",
        labelnames=["database", "engine"],
        buckets=(0.01, 0.05, 0.1, 0.5, 1.0, 2.5, 5.0, 10.0, float("inf")),
    )

    _lock_acquire_timeouts = prometheus_client.Counter(
        "dbwarden_lock_acquire_timeouts_total",
        "Total number of lock acquisition timeouts",
        labelnames=["database", "engine"],
    )

    _heartbeat_failures = prometheus_client.Counter(
        "dbwarden_heartbeat_failures_total",
        "Total number of heartbeat failures",
        labelnames=["database", "engine"],
    )

    _fence_aborts = prometheus_client.Counter(
        "dbwarden_fence_aborts_total",
        "Total number of fence aborts (ClickHouse)",
        labelnames=["database"],
    )

    _unlocks_total = prometheus_client.Counter(
        "dbwarden_unlocks_total",
        "Total number of unlock operations",
        labelnames=["database", "engine"],
    )

    def increment_migrations_total(
        database: str, version: str, success: bool = True
    ) -> None:
        _migrations_total.labels(
            database=database, version=version, success=str(success)
        ).inc()

    def observe_migration_duration(
        database: str, version: str, duration: float
    ) -> None:
        _migration_duration.labels(database=database, version=version).observe(duration)

    def set_schema_version(database: str, version: str) -> None:
        _schema_version.labels(database=database).set(_parse_version(version))

    def set_seed_version(database: str, version: str) -> None:
        _seed_version.labels(database=database).set(_parse_version(version))

    def set_pending_migrations(database: str, count: int) -> None:
        _pending_migrations.labels(database=database).set(count)

    def increment_migration_errors(database: str) -> None:
        _migration_errors.labels(database=database).inc()

    def increment_lock_acquisitions(database: str, engine: str) -> None:
        _lock_acquisitions.labels(database=database, engine=engine).inc()

    def observe_lock_acquire_wait(database: str, engine: str, duration: float) -> None:
        _lock_acquire_wait.labels(database=database, engine=engine).observe(duration)

    def increment_lock_acquire_timeouts(database: str, engine: str) -> None:
        _lock_acquire_timeouts.labels(database=database, engine=engine).inc()

    def increment_heartbeat_failures(database: str, engine: str) -> None:
        _heartbeat_failures.labels(database=database, engine=engine).inc()

    def increment_fence_aborts(database: str) -> None:
        _fence_aborts.labels(database=database).inc()

    def increment_unlocks(database: str, engine: str) -> None:
        _unlocks_total.labels(database=database, engine=engine).inc()

    def generate_metrics() -> str:
        return prometheus_client.generate_latest().decode("utf-8")

    def metrics_enabled() -> bool:
        return os.environ.get("DBWARDEN_METRICS", "false").lower() in (
            "true",
            "1",
            "yes",
        )

else:
    increment_migrations_total = _noop
    observe_migration_duration = _noop
    set_schema_version = _noop
    set_seed_version = _noop
    set_pending_migrations = _noop
    increment_migration_errors = _noop
    increment_lock_acquisitions = _noop
    observe_lock_acquire_wait = _noop
    increment_lock_acquire_timeouts = _noop
    increment_heartbeat_failures = _noop
    increment_fence_aborts = _noop
    increment_unlocks = _noop

    def generate_metrics() -> str:
        return ""

    def metrics_enabled() -> bool:
        return os.environ.get("DBWARDEN_METRICS", "false").lower() in (
            "true",
            "1",
            "yes",
        )
