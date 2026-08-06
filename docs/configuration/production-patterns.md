# Production Patterns

Real-world configuration patterns for production deployments.

## Environment Variables

### Basic Pattern

```python
import os
from dbwarden import database_config

primary = database_config(
    database_name="primary",
    default=True,
    database_type="postgresql",
    database_url_sync=os.getenv("DATABASE_URL"),
    model_paths=["app.models"],
    secure_values=True,
)
```

### With Validation

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
    model_paths=["app.models"],
    secure_values=True,
)
```

### With Defaults

```python
import os

primary = database_config(
    database_name="primary",
    default=True,
    database_type="postgresql",
    database_url_sync=os.getenv(
        "DATABASE_URL",
        "postgresql://localhost/myapp"  # Fallback
    ),
    model_paths=["app.models"],
)
```

## Docker

### Docker Compose

```yaml
# docker-compose.yml
services:
  app:
    build: .
    environment:
      DATABASE_URL: postgresql://user:password@db:5432/myapp
    depends_on:
      - db
  
  db:
    image: postgres:15
    environment:
      POSTGRES_USER: user
      POSTGRES_PASSWORD: password
      POSTGRES_DB: myapp
```

```python
# dbwarden.py
import os

primary = database_config(
    database_name="primary",
    default=True,
    database_type="postgresql",
    database_url_sync=os.getenv("DATABASE_URL"),
    model_paths=["app.models"],
)
```

### Dockerfile

```dockerfile
FROM python:3.12-slim

WORKDIR /app

COPY pyproject.toml uv.lock .
RUN uv sync

COPY . .

# Run migrations on container start
CMD ["sh", "-c", "dbwarden migrate && python app/main.py"]
```

## Kubernetes

### Secrets

```yaml
# secret.yaml
apiVersion: v1
kind: Secret
metadata:
  name: database-secret
type: Opaque
stringData:
  url: postgresql://user:password@postgres-service:5432/myapp
```

### Deployment with Init Container

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: myapp
spec:
  replicas: 3
  template:
    spec:
      initContainers:
      # Run migrations before app starts
      - name: migrate
        image: myapp:latest
        command: ["dbwarden", "migrate"]
        env:
        - name: DATABASE_URL
          valueFrom:
            secretKeyRef:
              name: database-secret
              key: url
      
      containers:
      - name: app
        image: myapp:latest
        env:
        - name: DATABASE_URL
          valueFrom:
            secretKeyRef:
              name: database-secret
              key: url
```

### ConfigMap for Model Paths

```yaml
# config.yaml
apiVersion: v1
kind: ConfigMap
metadata:
  name: app-config
data:
  model_paths: "app.models"
```

## AWS

### RDS with Secrets Manager

```python
import os
import json
import boto3

def get_database_url():
    secret_name = os.getenv("DB_SECRET_NAME")
    region = os.getenv("AWS_REGION", "us-east-1")
    
    client = boto3.client("secretsmanager", region_name=region)
    response = client.get_secret_value(SecretId=secret_name)
    secret = json.loads(response["SecretString"])
    
    return (
        f"postgresql://{secret['username']}:{secret['password']}"
        f"@{secret['host']}:{secret['port']}/{secret['dbname']}"
    )

primary = database_config(
    database_name="primary",
    default=True,
    database_type="postgresql",
    database_url_sync=get_database_url(),
    model_paths=["app.models"],
    secure_values=True,
)
```

### RDS Connection via IAM

```python
import os
import boto3

def get_rds_auth_token():
    rds_client = boto3.client("rds")
    return rds_client.generate_db_auth_token(
        DBHostname=os.getenv("DB_HOST"),
        Port=5432,
        DBUsername=os.getenv("DB_USER"),
    )

database_url = (
    f"postgresql://{os.getenv('DB_USER')}:{get_rds_auth_token()}"
    f"@{os.getenv('DB_HOST')}:5432/{os.getenv('DB_NAME')}"
)

primary = database_config(
    database_name="primary",
    default=True,
    database_type="postgresql",
    database_url_sync=database_url,
    model_paths=["app.models"],
)
```

## Multi-Environment

### Environment-Based Configuration

```python
import os

ENVIRONMENT = os.getenv("ENVIRONMENT", "dev")

if ENVIRONMENT == "production":
    database_url = os.getenv("PROD_DATABASE_URL")
    database_type = "postgresql"
elif ENVIRONMENT == "staging":
    database_url = os.getenv("STAGING_DATABASE_URL")
    database_type = "postgresql"
else:
    database_url = "sqlite:///./dev.db"
    database_type = "sqlite"

primary = database_config(
    database_name="primary",
    default=True,
    database_type=database_type,
    database_url_sync=database_url,
    model_paths=["app.models"],
    secure_values=(ENVIRONMENT != "dev"),
)
```

### Separate Config Files

```python
# dbwarden.py
import os
from importlib import import_module

environment = os.getenv("ENVIRONMENT", "dev")
config_module = import_module(f"config.{environment}")
config_module.setup_databases()
```

```python
# config/production.py
import os
from dbwarden import database_config

def setup_databases():
    primary = database_config(
        database_name="primary",
        default=True,
        database_type="postgresql",
        database_url_sync=os.getenv("DATABASE_URL"),
        model_paths=["app.models"],
        secure_values=True,
    )
```

## Connection Pools

### PostgreSQL with Pooling

```python
primary = database_config(
    database_name="primary",
    default=True,
    database_type="postgresql",
    database_url_sync=(
        "postgresql://user:pass@localhost/myapp"
        "?pool_size=20"
        "&max_overflow=10"
        "&pool_timeout=30"
        "&pool_recycle=3600"
    ),
    model_paths=["app.models"],
)
```

### External Pooler (PgBouncer)

```python
# Connection through PgBouncer
primary = database_config(
    database_name="primary",
    default=True,
    database_type="postgresql",
    database_url_sync="postgresql://user:pass@pgbouncer:6432/myapp",
    model_paths=["app.models"],
)
```

## SSL/TLS

### PostgreSQL with SSL

```python
primary = database_config(
    database_name="primary",
    default=True,
    database_type="postgresql",
    database_url_sync=(
        "postgresql://user:pass@host/myapp"
        "?sslmode=require"
        "&sslrootcert=/path/to/ca.pem"
        "&sslcert=/path/to/client-cert.pem"
        "&sslkey=/path/to/client-key.pem"
    ),
    model_paths=["app.models"],
)
```

### Environment-Based SSL

```python
import os

ssl_mode = os.getenv("DB_SSL_MODE", "prefer")
ca_cert = os.getenv("DB_CA_CERT_PATH", "")

ssl_params = f"?sslmode={ssl_mode}"
if ca_cert:
    ssl_params += f"&sslrootcert={ca_cert}"

database_url = f"postgresql://user:pass@host/myapp{ssl_params}"

primary = database_config(
    database_name="primary",
    default=True,
    database_type="postgresql",
    database_url_sync=database_url,
    model_paths=["app.models"],
)
```

## High Availability

### Multiple Replicas

```python
# Primary (writes)
primary = database_config(
    database_name="primary",
    default=True,
    database_type="postgresql",
    database_url_sync=os.getenv("PRIMARY_DATABASE_URL"),
    model_paths=["app.models"],
)

# Replica (reads)
replica = database_config(
    database_name="replica",
    database_type="postgresql",
    database_url_sync=os.getenv("REPLICA_DATABASE_URL"),
    model_paths=["app.models"],
    overlap_models=True,
)
```

### Automatic Failover

```python
import os

# Try primary, fallback to replica
primary_url = os.getenv("PRIMARY_DATABASE_URL")
replica_url = os.getenv("REPLICA_DATABASE_URL")

# Application logic handles failover
database_url = primary_url  # Start with primary

primary = database_config(
    database_name="primary",
    default=True,
    database_type="postgresql",
    database_url_sync=database_url,
    model_paths=["app.models"],
)
```

## Monitoring

### Application Name

```python
import os

app_name = os.getenv("APP_NAME", "myapp")
hostname = os.getenv("HOSTNAME", "unknown")

database_url = (
    f"postgresql://user:pass@host/myapp"
    f"?application_name={app_name}-{hostname}"
)

primary = database_config(
    database_name="primary",
    default=True,
    database_type="postgresql",
    database_url_sync=database_url,
    model_paths=["app.models"],
)
```

Check active connections:

```sql
SELECT application_name, count(*)
FROM pg_stat_activity
GROUP BY application_name;
```

## Security Best Practices

### Never Commit Credentials

```python
#  Bad
database_url_sync="postgresql://user:password@localhost/myapp"

#  Good
database_url_sync=os.getenv("DATABASE_URL")
```

### Use Least Privilege

Create application user with minimal permissions:

```sql
CREATE USER myapp_user WITH PASSWORD 'secret';
GRANT CONNECT ON DATABASE myapp TO myapp_user;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO myapp_user;
-- Don't grant DROP, TRUNCATE, CREATE, etc.
```

### Rotate Credentials

```python
# Use short-lived tokens
database_url = get_temporary_database_credentials()

primary = database_config(
    database_name="primary",
    default=True,
    database_type="postgresql",
    database_url_sync=database_url,
    model_paths=["app.models"],
)
```

## CI/CD Integration

### GitHub Actions

```yaml
name: Deploy

on:
  push:
    branches: [main]

jobs:
  migrate:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      
      - name: Install dependencies
        run: uv add dbwarden
      
      - name: Run migrations
        run: dbwarden migrate --database primary
        env:
          DATABASE_URL: ${{ secrets.DATABASE_URL }}
```

### GitLab CI

```yaml
migrate:
  stage: deploy
  script:
    - uv add dbwarden
    - dbwarden migrate --database primary
  environment:
    name: production
  only:
    - main
  variables:
    DATABASE_URL: $DATABASE_URL
```

## What's Next?

- **[Troubleshooting](troubleshooting.md)** - Common production issues
- **[Configuration API Reference](../reference/configuration-api.md)** - Complete parameter docs
