# `lock-status` and `unlock`

Inspect and recover migration lock state.

## Usage

```bash
$ dbwarden lock-status --database primary
$ dbwarden unlock --database primary
$ dbwarden unlock --database primary --force
```

## `lock-status`

Shows detailed lock status including state, health, holder information, and heartbeat age.

### Output

When unlocked:

```
Migration lock: INACTIVE
```

When locked:

```
Migration lock status
  State:       RUNNING
  Health:      HEALTHY
  Execution:   abc123def456ghi7
  Host:        deploy-runner-7
  PID:         1234
  Migration:   V042
  Acquired:    2026-08-22 12:03:14Z
  Heartbeat:   2026-08-22 12:04:01Z
```

### Health verdicts

- **HEALTHY**: Lock held, heartbeat is fresh
- **STUCK**: Lock held, heartbeat is stale (process may be paused or dead)
- **DEAD**: Lock free, status row shows dead worker
- **AVAILABLE**: No lock held
- **COMPLETE**: Migration completed successfully
- **FAILED**: Migration failed
- **INSPECTING**: Recovery inspection in progress
- **NEEDS_REVIEW**: Human intervention required

### JSON output

```bash
$ dbwarden --json lock-status --database primary
```

```json
{
  "database": "primary",
  "locked": true,
  "state": "RUNNING",
  "execution_id": "abc123def456ghi7",
  "owner_id": "f47ac10b-58cc",
  "host": "deploy-runner-7",
  "pid": 1234,
  "migration_version": "V042",
  "acquired_at": "2026-08-22T12:03:14Z",
  "last_heartbeat_at": "2026-08-22T12:04:01Z"
}
```

## `unlock`

Releases the migration lock. Without `--force`, shows holder diagnostics and requires confirmation.

### Options

- `--database`, `-d` - Target database name
- `--force`, `-f` - Skip confirmation and force-release the lock

### Behavior

Without `--force`:

```
Lock holder information
  State:       RUNNING
  Host:        deploy-runner-7
  PID:         1234
  Execution:   abc123def456ghi7
  Migration:   V042
  Acquired:    2026-08-22 12:03:14Z
  Heartbeat:   2026-08-22 12:04:01Z
This will terminate the holder's server connection and release the lock.
Use 'dbwarden unlock --force' to skip this prompt in automation.
```

With `--force`:

```
Migration lock released successfully.
```

### When to use

- After confirming no migration process is running
- When the lock status shows STUCK or DEAD
- In automation scripts (always use `--force`)

### When NOT to use

- If a migration process might still be running
- If the lock status shows HEALTHY with a recent heartbeat
- Without first checking `lock-status` and `history`

## Notes

- `lock-status` reads the full status row including heartbeat age
- `unlock` uses `force_release_lock()` which sets state to AVAILABLE
- For native-lock engines (PostgreSQL, MySQL), the actual lock is released when the holder's connection closes
- The status row is updated to reflect the release

See also: [Migration Locking](../advanced/migration-locking.md)
