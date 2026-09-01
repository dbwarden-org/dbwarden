"""Recovery state machine for migration locking.

The state machine governs the *run*, recorded in the status row and history table.

    AVAILABLE -> RUNNING -> COMPLETE
                         -> FAILED
                         -> DEAD (detected by new acquirer)
                              -> INSPECTING
                                   -> COMPLETE (all steps applied)
                                   -> resume (safely resumable)
                                   -> NEEDS_REVIEW (human decision)
"""
from __future__ import annotations

from enum import Enum


class LockState(str, Enum):
    AVAILABLE = "AVAILABLE"
    RUNNING = "RUNNING"
    COMPLETE = "COMPLETE"
    FAILED = "FAILED"
    DEAD = "DEAD"
    INSPECTING = "INSPECTING"
    NEEDS_REVIEW = "NEEDS_REVIEW"


# Valid transitions: from_state -> set of allowed to_states
_TRANSITIONS: dict[LockState, set[LockState]] = {
    LockState.AVAILABLE: {LockState.RUNNING},
    LockState.RUNNING: {LockState.COMPLETE, LockState.FAILED, LockState.DEAD},
    LockState.DEAD: {LockState.INSPECTING},
    LockState.INSPECTING: {LockState.COMPLETE, LockState.NEEDS_REVIEW, LockState.RUNNING},
    LockState.COMPLETE: {LockState.AVAILABLE, LockState.RUNNING},
    LockState.FAILED: {LockState.AVAILABLE, LockState.RUNNING},
    LockState.NEEDS_REVIEW: {LockState.AVAILABLE, LockState.RUNNING},
}


def validate_transition(from_state: LockState, to_state: LockState) -> bool:
    """Check if a state transition is valid.

    Returns True if the transition is allowed, False otherwise.
    Does not raise; callers decide how to handle invalid transitions.
    """
    allowed = _TRANSITIONS.get(from_state, set())
    return to_state in allowed


def describe_holder(
    state: LockState,
    execution_id: str | None,
    owner_id: str | None,
    migration_version: str | None,
    host: str | None,
    pid: int | None,
    acquired_at: str | None,
    last_heartbeat_at: str | None,
    heartbeat_ttl_seconds: int = 45,
) -> str:
    """Build a human-readable description of the current lock holder.

    Used by status, unlock, and diagnostic output.
    """
    if state == LockState.AVAILABLE:
        return "No lock held."

    parts = [f"State: {state.value}"]
    if execution_id:
        parts.append(f"Execution: {execution_id[:12]}")
    if owner_id:
        parts.append(f"Owner: {owner_id[:12]}")
    if migration_version:
        parts.append(f"Migration: {migration_version}")
    if host:
        parts.append(f"Host: {host}")
    if pid:
        parts.append(f"PID: {pid}")
    if acquired_at:
        parts.append(f"Acquired: {acquired_at}")
    if last_heartbeat_at:
        parts.append(f"Last heartbeat: {last_heartbeat_at}")

    return " | ".join(parts)


def compute_health(
    state: LockState,
    last_heartbeat_at: str | None,
    heartbeat_ttl_seconds: int = 45,
    now_seconds: float | None = None,
) -> str:
    """Compute the health verdict from status row data.

    Returns one of: HEALTHY, STUCK, DEAD, AVAILABLE, NEEDS_REVIEW, INSPECTING.
    """
    if state in (LockState.COMPLETE, LockState.FAILED, LockState.AVAILABLE):
        return state.value
    if state == LockState.NEEDS_REVIEW:
        return "NEEDS_REVIEW"
    if state == LockState.INSPECTING:
        return "INSPECTING"

    if state == LockState.DEAD:
        return "DEAD"

    # RUNNING state - check heartbeat staleness
    if last_heartbeat_at is None:
        return "HEALTHY"

    # Parse the heartbeat timestamp and compare with current time
    if now_seconds is None:
        import time
        now_seconds = time.time()

    try:
        from datetime import datetime
        # Parse ISO format or common timestamp formats
        heartbeat_time = datetime.fromisoformat(last_heartbeat_at.replace("Z", "+00:00"))
        heartbeat_epoch = heartbeat_time.timestamp()
        elapsed = now_seconds - heartbeat_epoch

        if elapsed > heartbeat_ttl_seconds:
            return "STUCK"
    except (ValueError, TypeError):
        # If we can't parse the timestamp, assume healthy
        pass

    return "HEALTHY"
