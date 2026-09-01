# ClickHouse Migration Locking

dbwarden provides production-grade migration locking for ClickHouse. This guide explains why ClickHouse needs custom locking, what the coordination profiles are, and how to set up your deployment.

See [Locking Architecture](../databases/locking.md) for the general locking model across all backends.

## Why ClickHouse needs custom locking

Every other database backend (PostgreSQL, MySQL, SQLite) provides a native lock primitive that dbwarden can use directly:

- PostgreSQL has `pg_advisory_lock()` (session-scoped, auto-releases on crash)
- MySQL has `GET_LOCK()` (session-scoped, auto-releases on crash)
- SQLite has `BEGIN IMMEDIATE` (write锁, released on crash via journal cleanup)

ClickHouse has none of these. Specifically, ClickHouse is missing:

1. **No session-scoped user locks.** There is no `LOCK TABLE` or advisory lock equivalent. You cannot hold a lock for the duration of a session.
2. **No synchronous compare-and-swap on table rows.** The `INSERT ... WHERE NOT EXISTS` pattern is not atomic in the way PostgreSQL's `INSERT ... ON CONFLICT` is.
3. **Non-transactional DDL.** DDL statements (CREATE, ALTER, DROP) cannot be rolled back. If a migration fails midway, there is no automatic undo.

Without native locks, two concurrent `dbwarden migrate` processes could both acquire the "lock" simultaneously, execute conflicting DDL, and corrupt the schema. dbwarden must therefore emulate locking using what ClickHouse does provide: table rows, ZooKeeper, and external infrastructure.

The coordination profiles (CH-0 through CH-4) represent progressively stronger guarantees, each addressing a specific weakness of the previous profile. They are not arbitrary levels; each one exists because the previous level has a concrete failure mode that cannot be solved within ClickHouse alone.

## Coordination profiles

| Profile | Description | Grade | Use case | What it fixes |
|---------|-------------|-------|----------|---------------|
| **CH-0** | Lease row with fencing token | C | Non-production, human-gated | Baseline: prevents concurrent migration via lease table |
| **CH-1** | CH-0 + strict idempotency | C | Production with idempotent migrations | Prevents data corruption from non-idempotent DDL |
| **CH-2** | CH-1 + Keeper-backed lock | C | Replicated with clickhouse-keeper | Better liveness: crash releases lock immediately via znode expiry |
| **CH-3** | Migration proxy choke point | A- | Production with migration proxy | Eliminates stale-lock window: single migration process |
| **CH-4** | Singleton executor | A* | Kubernetes with dedicated executor | Strongest guarantee: no DDL credentials on workers |

---

## CH-0: Lease with fencing token

The baseline profile. dbwarden writes a lease row to `dbwarden_lock` with a fencing token (monotonically increasing integer). A second process cannot acquire the lease while the first is active.

**Why it is grade C:** The fence check and DDL statement are not atomic. A paused worker (GC pause, SIGSTOP, CPU starvation) can lose its lease, another worker can acquire it, and the paused worker resumes and executes DDL against a database it no longer owns. The fencing token detects this, but only after the fact.

**Use when:** Development, staging, or any environment where a human is the only one triggering migrations and you can manually verify no concurrent runs are happening.

### CH-0 prerequisites

- ClickHouse server (any recent version)
- No additional infrastructure required

### CH-0 configuration

```python
from dbwarden import DbwardenDatabase

class Analytics(DbwardenDatabase):
    database_name = "analytics"
    default = True
    database_type = "clickhouse"
    database_url_sync = "clickhouse://default:@localhost:8123/analytics"
    model_paths = ["app/analytics_models"]
    # CH-0 is the default; no coordination profile setting needed
```

Or with `database_config()`:

```python
from dbwarden import database_config

database_config(
    database_name="analytics",
    database_type="clickhouse",
    database_url_sync="clickhouse://default:@localhost:8123/analytics",
    model_paths=["app/analytics_models"],
)
```

### How the lease works

When you run `dbwarden migrate`, dbwarden:

1. Attempts to insert a lease row into `dbwarden_lock` with a unique `execution_id`, a monotonically increasing `fencing_token`, and metadata
2. The insert uses a simple INSERT followed by verification (ClickHouse MergeTree does not support atomic conditional inserts)
3. A background heartbeat updates `last_heartbeat_at` every 15 seconds
4. On completion, the lease row is marked as COMPLETE

```sql
INSERT INTO dbwarden_lock
    (namespace, execution_id, owner_id, migration_version, migration_checksum,
     fencing_token, host, pid, state, acquired_at, last_heartbeat_at)
VALUES
    (:namespace, :execution_id, :owner_id, :migration_version, :migration_checksum,
     :fencing_token, :host, :pid, :state, :acquired_at, :last_heartbeat_at)
```

After insertion, the system verifies ownership by reading back the row and confirming the `execution_id` matches.

**Note:** The `expires_at` column exists in the schema for lease TTL tracking but is not currently populated by the INSERT statement. Lease expiry is determined by comparing `last_heartbeat_at` against the current time.

### CH-0 verification

```bash
# Run a migration
dbwarden migrate -d analytics

# Check lock status (should show INACTIVE after migration completes)
dbwarden lock-status -d analytics

# Inspect the lease table directly
clickhouse-client --query "SELECT * FROM analytics.dbwarden_lock"
```

### CH-0 limitations

- If the process crashes after the lease expires but before cleanup, another process must wait for the TTL (default 120 seconds) before acquiring the lock
- A paused worker can lose its lease and resume DDL execution against a database it no longer owns (the fencing token detects this after the fact)
- No automatic recovery from partial migration failure

---

## CH-1: Idempotency enforcement

CH-1 adds strict idempotency checking on top of CH-0. Non-idempotent statements (`RENAME`, `MODIFY COLUMN`, `UPDATE`, `DELETE`) are refused by default.

**Why it exists:** Even with CH-0's lease, a stale worker can execute non-idempotent DDL after losing its lease. Idempotent DDL (`CREATE TABLE IF NOT EXISTS`, `DROP TABLE IF EXISTS`) is safe to re-execute; non-idempotent DDL is not. By refusing non-idempotent statements, CH-1 limits the blast radius of the stale-worker window to idempotent operations only.

**Use when:** Production environments where all migrations are idempotent (dbwarden generates idempotent DDL by default).

### CH-1 prerequisites

- Everything in CH-0
- All migrations must use idempotent forms (`IF NOT EXISTS`, `IF EXISTS`)

### CH-1 configuration

```python
from dbwarden import DbwardenDatabase

class Analytics(DbwardenDatabase):
    database_name = "analytics"
    default = True
    database_type = "clickhouse"
    database_url_sync = "clickhouse://default:@localhost:8123/analytics"
    model_paths = ["app/analytics_models"]
```

Or with `database_config()`:

```python
from dbwarden import database_config

database_config(
    database_name="analytics",
    database_type="clickhouse",
    database_url_sync="clickhouse://default:@localhost:8123/analytics",
    model_paths=["app/analytics_models"],
)
```

### Idempotent vs non-idempotent DDL

**Idempotent** (allowed under CH-1):

- `CREATE TABLE/VIEW/DICTIONARY IF NOT EXISTS`
- `DROP TABLE/VIEW/DICTIONARY IF EXISTS`
- `ALTER TABLE ADD COLUMN IF NOT EXISTS`
- `ALTER TABLE DROP COLUMN IF EXISTS`
- Metadata-only alters (COMMENT, TTL)

**Non-idempotent** (refused under CH-1):

- `RENAME` (no idempotent form)
- `MODIFY COLUMN` (rewrites data)
- `UPDATE` / `DELETE` (data mutations)

### CH-1 idempotency checklist

Before enabling CH-1 in production:

1. **Audit existing migrations**:

   ```bash
   grep -E "RENAME|MODIFY COLUMN|UPDATE|DELETE" migrations/analytics/*.sql
   ```

2. **Test idempotency**:

   ```bash
   dbwarden migrate -d analytics
   dbwarden migrate -d analytics  # Should be no-op
   ```

3. **Review generated migrations**:

   - All CREATE statements should use `IF NOT EXISTS`
   - All DROP statements should use `IF EXISTS`
   - All ALTER ADD/DROP COLUMN should use `IF NOT EXISTS`/`IF EXISTS`

### CH-1 verification

```bash
# Run a migration
dbwarden migrate -d analytics

# Run again (should be a no-op with CH-1 enforcement)
dbwarden migrate -d analytics

# Check lock status
dbwarden lock-status -d analytics
```

### Overriding CH-1 for non-idempotent migrations

If you must run a non-idempotent migration (e.g., a one-time RENAME), restructure it to use an idempotent form:

```sql
-- Instead of:
ALTER TABLE events RENAME TO events_v2;

-- Use:
ALTER TABLE IF EXISTS events RENAME TO events_v2;
```

Or drop to CH-0 temporarily by removing the idempotency check in your deployment.

---

## CH-2: Keeper-backed lock

For replicated deployments with clickhouse-keeper (ZooKeeper-compatible), CH-2 replaces the lease table with ephemeral-sequential znodes.

**Why it exists:** CH-0/CH-1 lease expiry depends on TTL, which means there is always a window between "worker died" and "lease expired" where the database is locked but no work is happening. With ZooKeeper ephemeral znodes, the lock is released the instant the ZooKeeper session ends (typically within seconds of a crash), eliminating the stale-lock window for liveness purposes.

**Use when:** Replicated ClickHouse deployments with clickhouse-keeper or ZooKeeper available.

### CH-2 prerequisites

- ClickHouse cluster with clickhouse-keeper or ZooKeeper
- clickhouse-keeper must be reachable from all cluster nodes
- Default keeper path: `/dbwarden/locks`

### CH-2 infrastructure setup

**1. Deploy clickhouse-keeper (if not already running):**

clickhouse-keeper ships with ClickHouse. A minimal 3-node keeper cluster for quorum:

```xml
<!-- /etc/clickhouse-server/config.d/keeper.xml -->
<clickhouse>
    <keeper_server>
        <tcp_port>9181</tcp_port>
        <server_id>1</server_id>
        <raft_configuration>
            <server>
                <id>1</id>
                <hostname>keeper1</hostname>
                <port>9234</port>
            </server>
            <server>
                <id>2</id>
                <hostname>keeper2</hostname>
                <port>9234</port>
            </server>
            <server>
                <id>3</id>
                <hostname>keeper3</hostname>
                <port>9234</port>
            </server>
        </raft_configuration>
    </keeper_server>
</clickhouse>
```

**2. Verify keeper is running:**

```bash
clickhouse-client --host keeper1 --port 9181 --query "SELECT * FROM system.zookeeper WHERE path = '/'"
```

### CH-2 configuration

```python
from dbwarden import DbwardenDatabase

class Analytics(DbwardenDatabase):
    database_name = "analytics"
    default = True
    database_type = "clickhouse"
    database_url_sync = "clickhouse://default:@clickhouse1:8123/analytics"
    model_paths = ["app/analytics_models"]
    ch_cluster = "production_cluster"
```

Or with `database_config()`:

```python
from dbwarden import database_config

database_config(
    database_name="analytics",
    database_type="clickhouse",
    database_url_sync="clickhouse://default:@clickhouse1:8123/analytics",
    model_paths=["app/analytics_models"],
    ch_cluster="production_cluster",
)
```

### How CH-2 locking works

1. dbwarden creates an ephemeral-sequential znode under the keeper path
2. The znode sequence number serves as the fencing token (monotonic by construction)
3. If the process crashes, the ZooKeeper session ends, and the ephemeral znode is removed immediately
4. The next worker sees no active lock and acquires one instantly

### CH-2 verification

```bash
# Run a migration on a replicated cluster
dbwarden migrate -d analytics

# Check lock status
dbwarden lock-status -d analytics

# Inspect keeper znodes directly
clickhouse-client --host keeper1 --port 9181 --query \
  "SELECT name, value, ctime FROM system.zookeeper WHERE path = '/dbwarden/locks'"
```

### CH-2 limitations

- Requires clickhouse-keeper or ZooKeeper infrastructure
- Keeper availability is a single point of failure for migration locking (but not for data serving)
- Does not prevent two workers from acquiring the lock during a network partition (addressed by CH-3)

---

## CH-3: Migration proxy

All migration DDL flows through a single migration proxy process. Workers connect only to the proxy, not directly to ClickHouse.

**Why it exists:** Even with CH-2's fast lock release, two workers could theoretically acquire the lock in rapid succession during a network partition. CH-3 eliminates this by design: only one process ever has DDL credentials, so concurrent migration is structurally impossible.

**Use when:** Production environments where zero-downtime deploys run from multiple replicas and you need the strongest DDL safety guarantee without Kubernetes.

### CH-3 prerequisites

- Everything in CH-2
- A dedicated proxy host or container
- Network configuration to restrict DDL access to the proxy only

### CH-3 infrastructure setup

**1. Create a migration-only ClickHouse user with DDL privileges:**

```sql
CREATE USER dbwarden_migration IDENTIFIED BY 'secure_password';
GRANT CREATE, ALTER, DROP, RENAME ON *.* TO dbwarden_migration;
```

**2. Create a restricted user for application connections (no DDL):**

```sql
CREATE USER dbwarden_app IDENTIFIED BY 'app_password';
GRANT SELECT, INSERT, UPDATE, DELETE ON analytics.* TO dbwarden_app;
```

**3. Configure ClickHouse to only allow DDL from the proxy IP:**

```xml
<!-- /etc/clickhouse-server/users.d/migration_restriction.xml -->
<clickhouse>
    <users>
        <dbwarden_migration>
            <networks>
                <ip>proxy-host-ip</ip>
            </networks>
        </dbwarden_migration>
    </users>
</clickhouse>
```

The permission model is the core of CH-3:

| User | Privileges | Used by | Purpose |
|------|-----------|---------|---------|
| `dbwarden_app` | `SELECT, INSERT, UPDATE, DELETE` on `analytics.*` | Worker nodes | Data read/write only. No DDL. |
| `dbwarden_migration` | `CREATE, ALTER, DROP, RENAME` on `*.*` | Proxy node only | Schema changes only. No DML. |

Workers can read and write data but cannot create, alter, or drop tables. The proxy can change schema but does not serve application traffic. Since only the proxy has DDL credentials, concurrent migration is structurally impossible.

**4. Run the migration proxy:**

The proxy is a thin process that holds the CH-2 lock and accepts DDL commands:

```python
# migration_proxy.py
import asyncio
from dbwarden import database_config
from dbwarden_redis import migration_lock
from redis.asyncio import Redis

database_config(
    database_name="analytics",
    database_type="clickhouse",
    database_url_sync="clickhouse://dbwarden_migration:secure_password@proxy-host:8123/analytics",
    model_paths=["app/analytics_models"],
    ch_cluster="production_cluster",
)

async def run_migration():
    redis = Redis.from_url("redis://localhost:6379")
    async with migration_lock(redis, key="analytics_migration"):
        # Run migration via CLI
        import subprocess
        subprocess.run(["dbwarden", "migrate", "-d", "analytics"])

if __name__ == "__main__":
    asyncio.run(run_migration())
```

### CH-3 configuration

**Worker nodes (no DDL access):**

```python
from dbwarden import DbwardenDatabase

class AnalyticsWorker(DbwardenDatabase):
    database_name = "analytics"
    default = True
    database_type = "clickhouse"
    # Workers use the restricted user (no DDL privileges)
    database_url_sync = "clickhouse://dbwarden_app:app_password@clickhouse1:8123/analytics"
    model_paths = ["app/analytics_models"]
    ch_cluster = "production_cluster"
    # Workers do NOT run migrations; they only read schema
```

**Proxy node (DDL access):**

```python
from dbwarden import DbwardenDatabase

class AnalyticsProxy(DbwardenDatabase):
    database_name = "analytics"
    default = True
    database_type = "clickhouse"
    # Proxy uses the migration user (has DDL privileges)
    database_url_sync = "clickhouse://dbwarden_migration:secure_password@proxy-host:8123/analytics"
    model_paths = ["app/analytics_models"]
    ch_cluster = "production_cluster"
```

### CH-3 verification

```bash
# On the proxy node: run migration
python migration_proxy.py

# On any node: check lock status
dbwarden lock-status -d analytics

# On any node: verify schema
dbwarden diff -d analytics
```

### CH-3 takeover procedure

If the proxy process dies and you need to run migrations from a different host:

```bash
# 1. Kill any lingering proxy processes
kill $(pgrep -f migration_proxy)

# 2. Wait for the keeper lock to expire (or force release)
clickhouse-client --host keeper1 --port 9181 --query \
  "SELECT * FROM system.zookeeper WHERE path = '/dbwarden/locks'"

# 3. Run migration from the new proxy host
python migration_proxy.py
```

---

## CH-4: Singleton executor

Workers submit migration jobs; a single executor process executes them. The executor is the only process with DDL credentials, enforced by the deployment platform.

**Why it exists:** CH-3 requires running a proxy process, which is additional infrastructure. CH-4 achieves the same guarantee using the deployment platform's native primitives (Kubernetes Job with `parallelism: 1`). Workers submit jobs; the platform serializes them; the executor runs them one at a time.

**Use when:** Kubernetes deployments where you want the platform to enforce migration serialization rather than running a dedicated proxy process.

### CH-4 prerequisites

- Kubernetes cluster
- ClickHouse cluster with clickhouse-keeper or ZooKeeper
- A container image with dbwarden and your models installed

### CH-4 infrastructure setup

**1. Create a Kubernetes Secret for the migration credentials:**

```yaml
# migration-secret.yaml
apiVersion: v1
kind: Secret
metadata:
  name: clickhouse-migration
  namespace: analytics
type: Opaque
stringData:
  url: "clickhouse://dbwarden_migration:secure_password@clickhouse1:8123/analytics"
```

**2. Create a ConfigMap with your dbwarden config:**

```yaml
# dbwarden-config.yaml
apiVersion: v1
kind: ConfigMap
metadata:
  name: dbwarden-config
  namespace: analytics
data:
  dbwarden.py: |
    from dbwarden import DbwardenDatabase

    class Analytics(DbwardenDatabase):
        database_name = "analytics"
        default = True
        database_type = "clickhouse"
        database_url_sync = "clickhouse://dbwarden_migration:secure_password@clickhouse1:8123/analytics"
        model_paths = ["app/analytics_models"]
        ch_cluster = "production_cluster"
```

**3. Create the executor Job (runs once, serially):**

```yaml
# migration-executor.yaml
apiVersion: batch/v1
kind: Job
metadata:
  name: dbwarden-migrate
  namespace: analytics
spec:
  # Key setting: only one job runs at a time
  parallelism: 1
  completions: 1
  backoffLimit: 1
  template:
    spec:
      restartPolicy: Never
      containers:
        - name: migrate
          image: your-registry/dbwarden:latest
          command: ["dbwarden", "migrate", "-d", "analytics"]
          env:
            - name: DATABASE_URL
              valueFrom:
                secretKeyRef:
                  name: clickhouse-migration
                  key: url
          volumeMounts:
            - name: config
              mountPath: /app/dbwarden.py
              subPath: dbwarden.py
            - name: models
              mountPath: /app/app/analytics_models
          resources:
            limits:
              memory: "256Mi"
              cpu: "500m"
      volumes:
        - name: config
          configMap:
            name: dbwarden-config
        - name: models
          configMap:
            name: analytics-models
```

**4. Trigger migrations from workers:**

Workers submit Jobs instead of running migrations directly:

```python
# In your deploy script or CI pipeline
from kubernetes import client, config

config.load_incluster_config()
batch_api = client.BatchV1Api()

job = client.V1Job(
    metadata=client.V1ObjectMeta(name=f"dbwarden-migrate-{timestamp}"),
    spec=client.V1JobSpec(
        parallelism=1,
        completions=1,
        backoff_limit=1,
        template=client.V1PodTemplateSpec(
            spec=client.V1PodSpec(
                restart_policy="Never",
                containers=[client.V1Container(
                    name="migrate",
                    image="your-registry/dbwarden:latest",
                    command=["dbwarden", "migrate", "-d", "analytics"],
                )],
            ),
        ),
    ),
)

batch_api.create_namespaced_job(namespace="analytics", body=job)
```

### CH-4 configuration

The executor container uses the same dbwarden config as CH-3 (the migration user with DDL privileges):

```python
from dbwarden import DbwardenDatabase

class Analytics(DbwardenDatabase):
    database_name = "analytics"
    default = True
    database_type = "clickhouse"
    database_url_sync = "clickhouse://dbwarden_migration:secure_password@clickhouse1:8123/analytics"
    model_paths = ["app/analytics_models"]
    ch_cluster = "production_cluster"
```

### CH-4 verification

```bash
# Check that the executor Job ran successfully
kubectl get jobs -n analytics

# Check logs
kubectl logs job/dbwarden-migrate -n analytics

# Check lock status from any pod
kubectl exec -it clickhouse-client-pod -n analytics -- \
  dbwarden lock-status -d analytics
```

### CH-4 cleanup

After a successful migration, the Job remains until manually cleaned up. Use a TTL policy:

```yaml
# Add to the Job spec
spec:
  ttlSecondsAfterFinished: 3600  # Clean up after 1 hour
```

---

## Choosing a profile

| Your situation | Recommended profile |
|----------------|---------------------|
| Local development, single developer | CH-0 |
| Staging, single deploy process | CH-0 |
| Production, all migrations are idempotent | CH-1 |
| Production, replicated ClickHouse with keeper | CH-2 |
| Production, multiple deploy replicas, zero-downtime | CH-3 |
| Kubernetes, want platform-enforced serialization | CH-4 |

## Configuration reference

| Key | Default | Description |
|-----|---------|-------------|
| `ch_cluster` | `None` | ClickHouse cluster name. Appends `ON CLUSTER '<name>'` to every DDL statement. |
| `ch_replicated_database` | `False` | Use ClickHouse `Replicated` database engine. Mutually exclusive with `ch_cluster`. |
| `clickhouse_lock_ttl` | `120` | Lease TTL in seconds for CH-0/CH-1. How long a migration lock is held before auto-expiring. |
| `lock_namespace` | `"default"` | Lock scope. Allows independent lock streams (e.g., separate schema migrations from data operations). |

## Troubleshooting

### "Non-idempotent statement" error

```
Non-idempotent: ALTER TABLE events RENAME TO events_v2
```

**Solution**: Make the statement idempotent:

```sql
-- Instead of:
ALTER TABLE events RENAME TO events_v2;

-- Use:
ALTER TABLE IF EXISTS events RENAME TO events_v2;
-- Or restructure to avoid RENAME
```

Or restructure the migration to use idempotent forms:

### "Lease expired" error

```
ClickHouse lease held by: host=clickhouse1, pid=1234, expires=2026-09-01 12:00:00
```

**Solution**: Wait for lease expiry or force release:

```bash
dbwarden unlock -d analytics --force
```

### Replicated database conflicts

```
ConfigurationError: ch_cluster and ch_replicated_database are mutually exclusive
```

**Solution**: Use one or the other, not both:

```python
# Correct: ON CLUSTER
ch_cluster = "production_cluster"
ch_replicated_database = False

# Correct: Replicated database
ch_cluster = None
ch_replicated_database = True
```

### Keeper connection refused

```
Connection refused: keeper1:9181
```

**Solution**: Verify keeper is running and reachable:

```bash
# Check keeper status
clickhouse-client --host keeper1 --port 9181 --query "SELECT * FROM system.build_options"

# Check network connectivity
nc -zv keeper1 9181
```

### Migration stuck in RUNNING state

Check if the migration process is still alive:

```bash
dbwarden lock-status -d analytics
```

If the process is dead, force release the lock:

```bash
dbwarden unlock -d analytics --force
```

## See Also

- [Locking Architecture](../databases/locking.md): General locking model across all backends
- [ON Cluster](../databases/clickhouse/on-cluster.md): Cluster modes and DDL propagation
- [Immutability](../databases/clickhouse/immutability.md): What can never change in ClickHouse
- [Safety Classification](../databases/clickhouse/safety.md): Change classification levels
