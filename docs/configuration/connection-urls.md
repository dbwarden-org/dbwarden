# Connection URLs

Complete reference for database connection URL formats.

## URL Format

All database URLs follow this general structure:

```
[dialect[+driver]]://[username[:password]@][host][:port][/database][?option=value&...]
```

## PostgreSQL

### Basic Format

```
postgresql://[user[:password]@][host][:port]/database[?options]
```

### Examples

**Local default:**
```python
database_url_sync="postgresql://localhost/myapp"
```

**With credentials:**
```python
database_url_sync="postgresql://user:password@localhost:5432/myapp"
```

**Remote host:**
```python
database_url_sync="postgresql://user:password@db.example.com:5432/myapp"
```

**With SSL:**
```python
database_url_sync="postgresql://user:password@localhost/myapp?sslmode=require"
```

**With connection pool:**
```python
database_url_sync="postgresql://user:password@localhost/myapp?pool_size=20&max_overflow=10"
```

### SSL Modes

| Mode | Description |
|------|-------------|
| `disable` | No SSL |
| `allow` | Try SSL, fall back to non-SSL |
| `prefer` | Try SSL first (default) |
| `require` | Require SSL, fail if unavailable |
| `verify-ca` | Require SSL + verify CA |
| `verify-full` | Require SSL + verify CA + hostname |

**Example:**
```python
database_url_sync="postgresql://user:pass@host/db?sslmode=verify-full&sslrootcert=/path/to/ca.pem"
```

### Common Options

| Option | Description | Example |
|--------|-------------|---------|
| `sslmode` | SSL connection mode | `sslmode=require` |
| `sslcert` | Client certificate | `sslcert=/path/to/cert.pem` |
| `sslkey` | Client key | `sslkey=/path/to/key.pem` |
| `sslrootcert` | CA certificate | `sslrootcert=/path/to/ca.pem` |
| `connect_timeout` | Connection timeout (seconds) | `connect_timeout=10` |
| `application_name` | App name in pg_stat_activity | `application_name=myapp` |

### Cloud Providers

**AWS RDS:**
```python
database_url_sync="postgresql://user:pass@mydb.abc123.us-east-1.rds.amazonaws.com:5432/myapp?sslmode=require"
```

**Google Cloud SQL:**
```python
database_url_sync="postgresql://user:pass@/myapp?host=/cloudsql/project:region:instance"
```

**Azure Database:**
```python
database_url_sync="postgresql://user@server:pass@server.postgres.database.azure.com:5432/myapp?sslmode=require"
```

**Heroku:**
```python
import os
database_url_sync=os.getenv("DATABASE_URL")  # Provided by Heroku
```

## SQLite

### Basic Format

```
sqlite:///[path]
```

### Examples

**Relative path:**
```python
database_url_sync="sqlite:///./app.db"
database_url_sync="sqlite:///./data/app.db"
```

**Absolute path:**
```python
database_url_sync="sqlite:////absolute/path/to/app.db"
```

**In-memory (testing only):**
```python
database_url_sync="sqlite:///:memory:"
```

In-memory databases are lost when the connection closes. Only use for testing.

### Common Options

| Option | Description | Example |
|--------|-------------|---------|
| `timeout` | Lock timeout (seconds) | `?timeout=20` |
| `check_same_thread` | Thread safety check | `?check_same_thread=false` |

**Example:**
```python
database_url_sync="sqlite:///./app.db?timeout=20"
```

## MySQL / MariaDB

### Basic Format

```
mysql://[user[:password]@][host][:port]/database[?options]
```

### Examples

**Local:**
```python
database_url_sync="mysql://root:password@localhost:3306/myapp"
```

**With charset:**
```python
database_url_sync="mysql://user:pass@localhost/myapp?charset=utf8mb4"
```

**With SSL:**
```python
database_url_sync="mysql://user:pass@localhost/myapp?ssl_ca=/path/to/ca.pem"
```

### Common Options

| Option | Description | Example |
|--------|-------------|---------|
| `charset` | Character set | `charset=utf8mb4` |
| `ssl_ca` | CA certificate | `ssl_ca=/path/to/ca.pem` |
| `ssl_cert` | Client certificate | `ssl_cert=/path/to/cert.pem` |
| `ssl_key` | Client key | `ssl_key=/path/to/key.pem` |

### MariaDB

MariaDB uses the same URL format as MySQL:

```python
database_url_sync="mysql://user:pass@localhost:3306/myapp"
```

Configure with `database_type="mariadb"`:

```python
primary = database_config(
    database_name="primary",
    database_type="mariadb",
    database_url_sync="mysql://localhost/myapp",
)
```

## ClickHouse

### Basic Format

```
http://[user[:password]@]host[:port]/database[?options]
```

### Examples

**Local:**
```python
database_url_sync="http://default:@localhost:8123/myapp"
```

**With authentication:**
```python
database_url_sync="http://user:password@localhost:8123/myapp"
```

**With HTTPS:**
```python
database_url_sync="https://user:password@clickhouse.example.com:8443/myapp"
```

### Common Options

| Option | Description | Example |
|--------|-------------|---------|
| `compression` | Enable compression | `compression=1` |
| `connect_timeout` | Connection timeout | `connect_timeout=10` |
| `send_timeout` | Send timeout | `send_timeout=300` |
| `receive_timeout` | Receive timeout | `receive_timeout=300` |

**Example:**
```python
database_url_sync="http://user:pass@localhost:8123/myapp?compression=1&connect_timeout=10"
```

## Environment Variables

### Basic Pattern

```python
import os

primary = database_config(
    database_name="primary",
    default=True,
    database_type="postgresql",
    database_url_sync=os.getenv("DATABASE_URL"),
)
```

### With Fallback

```python
import os

database_url = os.getenv("DATABASE_URL", "sqlite:///./dev.db")

primary = database_config(
    database_name="primary",
    default=True,
    database_type="postgresql" if "postgresql" in database_url else "sqlite",
    database_url_sync=database_url,
)
```

### Required Environment Variables

```python
import os

DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    raise ValueError("DATABASE_URL environment variable is required")

primary = database_config(
    database_name="primary",
    default=True,
    database_type="postgresql",
    database_url_sync=DATABASE_URL,
)
```

### Multiple Databases

```python
import os

primary = database_config(
    database_name="primary",
    default=True,
    database_type="postgresql",
    database_url_sync=os.getenv("PRIMARY_DATABASE_URL"),
)

analytics = database_config(
    database_name="analytics",
    database_type="postgresql",
    database_url_sync=os.getenv("ANALYTICS_DATABASE_URL"),
)
```

## URL Encoding

### Special Characters

If your password contains special characters, URL-encode them:

| Character | Encoded |
|-----------|---------|
| `@` | `%40` |
| `:` | `%3A` |
| `/` | `%2F` |
| `?` | `%3F` |
| `#` | `%23` |
| `&` | `%26` |
| `%` | `%25` |

**Example:**

Password: `p@ss:word`

```python
database_url_sync="postgresql://user:p%40ss%3Aword@localhost/myapp"
```

### Python URL Encoding

```python
from urllib.parse import quote_plus

username = "user"
password = "p@ss:word"
host = "localhost"
database = "myapp"

database_url = f"postgresql://{username}:{quote_plus(password)}@{host}/{database}"
# Result: postgresql://user:p%40ss%3Aword@localhost/myapp
```

## Connection Pools

### PostgreSQL Pool Options

```python
database_url_sync="postgresql://user:pass@localhost/myapp?pool_size=20&max_overflow=10&pool_timeout=30"
```

| Option | Description | Default |
|--------|-------------|---------|
| `pool_size` | Max connections in pool | 5 |
| `max_overflow` | Extra connections if pool full | 10 |
| `pool_timeout` | Wait time for connection (seconds) | 30 |
| `pool_recycle` | Recycle connections after (seconds) | -1 (never) |

### Connection Lifetime

Recycle connections after 1 hour:

```python
database_url_sync="postgresql://user:pass@localhost/myapp?pool_recycle=3600"
```

## Testing Connections

### Verify URL Format

```python
from sqlalchemy import create_engine

try:
    engine = create_engine("postgresql://user:pass@localhost/myapp")
    with engine.connect() as conn:
        result = conn.execute("SELECT 1")
        print("Connection successful!")
except Exception as e:
    print(f"Connection failed: {e}")
```

### Test with DBWarden

```bash
# Check configuration
$ dbwarden settings show

# Test connection
$ dbwarden check-db
```

## Common Mistakes

### Forgetting Port

**Wrong:**
```python
database_url_sync="postgresql://user:pass@localhost/myapp"  # Uses default port 5432
```

**If you need a different port:**
```python
database_url_sync="postgresql://user:pass@localhost:5433/myapp"
```

### Missing Slashes

**Wrong:**
```python
database_url_sync="sqlite://./app.db"  # Only 2 slashes
```

**Correct:**
```python
database_url_sync="sqlite:///./app.db"  # 3 slashes for relative path
database_url_sync="sqlite:////absolute/path/app.db"  # 4 slashes for absolute path
```

### Special Characters Not Encoded

**Wrong:**
```python
database_url_sync="postgresql://user:p@ss@localhost/myapp"  # @ not encoded
```

**Correct:**
```python
database_url_sync="postgresql://user:p%40ss@localhost/myapp"  # @ encoded as %40
```

## What's Next?

- **[Model Discovery](model-discovery.md)** - Configure model paths
- **[Dev Mode](dev-mode.md)** - Local development URLs
- **[Production Patterns](production-patterns.md)** - Real-world examples
- **[Troubleshooting](troubleshooting.md)** - Connection issues
