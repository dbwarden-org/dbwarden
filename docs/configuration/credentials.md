# Credentials and Secrets

Never hardcode database credentials in `dbwarden.py`. This page covers how to inject secrets safely.

## The problem

The quick-start examples show inline connection strings:

```python
# Do not ship this
primary = database_config(
    database_url_sync="postgresql://admin:s3cr3t@localhost:5432/myapp",
    ...
)
```

`dbwarden.py` is Python source. It ends up in version control. Credentials in source are a liability.

## Environment variables

The standard pattern: read from the environment at config load time.

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

`secure_values=True` tells DBWarden to redact the URL in CLI output and logs.

### Fail fast on missing env var

```python
import os
from dbwarden import database_config

DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    raise ValueError("DATABASE_URL is required")

primary = database_config(
    database_name="primary",
    default=True,
    database_type="postgresql",
    database_url_sync=DATABASE_URL,
    model_paths=["app.models"],
    secure_values=True,
)
```

Without the guard, a missing env var silently passes `None` to `database_url_sync`, which fails later with a confusing error.

## `.env` files with python-dotenv

For local development, use a `.env` file to avoid setting vars manually each session.

Install:

```bash
uv add python-dotenv
```

Create `.env` in your project root:

```
DATABASE_URL=postgresql://dev_user:dev_pass@localhost:5432/myapp_dev
```

Load it at the top of `dbwarden.py`:

```python
import os
from dotenv import load_dotenv
from dbwarden import database_config

load_dotenv()

primary = database_config(
    database_name="primary",
    default=True,
    database_type="postgresql",
    database_url_sync=os.getenv("DATABASE_URL"),
    model_paths=["app.models"],
    secure_values=True,
)
```

Add `.env` to `.gitignore`. Commit a `.env.example` with placeholder values:

```
DATABASE_URL=postgresql://user:password@localhost:5432/myapp
```

## Multi-database with separate secrets

Each database gets its own env var:

```python
import os
from dbwarden import database_config

primary = database_config(
    database_name="primary",
    default=True,
    database_type="postgresql",
    database_url_sync=os.getenv("PRIMARY_DATABASE_URL"),
    model_paths=["app.models"],
    secure_values=True,
)

analytics = database_config(
    database_name="analytics",
    database_type="clickhouse",
    database_url_sync=os.getenv("ANALYTICS_DATABASE_URL"),
    model_paths=["app.models.analytics"],
    secure_values=True,
)
```

## Dev mode with secrets

Dev mode can also use env vars, keeping SQLite paths out of source:

```python
primary = database_config(
    database_name="primary",
    default=True,
    database_type="postgresql",
    database_url_sync=os.getenv("DATABASE_URL"),
    dev_database_type="sqlite",
    dev_database_url=os.getenv("DEV_DATABASE_URL", "sqlite:///./dev.db"),
    model_paths=["app.models"],
    secure_values=True,
)
```

`DEV_DATABASE_URL` defaults to a local SQLite path if not set, which is reasonable for development.

## CI/CD environments

In GitHub Actions, set secrets in the repository settings and reference them in the workflow:

```yaml
- name: Run migrations
  env:
    DATABASE_URL: ${{ secrets.DATABASE_URL }}
  run: dbwarden migrate --database primary
```

In GitLab CI, use masked CI/CD variables:

```yaml
migrate:
  script:
    - dbwarden migrate --database primary
  variables:
    DATABASE_URL: $DATABASE_URL  # set in GitLab CI/CD settings
```

## Third-party secret managers

For production systems using Vault, AWS Secrets Manager, or Infisical, fetch the secret before passing it to `database_config()`:

```python
import os
import boto3
import json
from dbwarden import database_config

def get_secret(secret_name: str) -> str:
    client = boto3.client("secretsmanager")
    response = client.get_secret_value(SecretId=secret_name)
    secret = json.loads(response["SecretString"])
    return secret["database_url"]

primary = database_config(
    database_name="primary",
    default=True,
    database_type="postgresql",
    database_url_sync=get_secret("myapp/production/database"),
    model_paths=["app.models"],
    secure_values=True,
)
```

`dbwarden.py` is plain Python, so any secret retrieval logic is valid here.

## `secure_values=True`

When set, DBWarden redacts the connection URL in:

- `dbwarden settings show` output
- `dbwarden settings show` output
- Log lines

The URL is still used internally for connections. This prevents accidental credential exposure in terminal output shared in screenshots or logs.

See also: [Production Patterns](production-patterns.md) | [CI/CD Patterns](../advanced/ci-cd-patterns.md)
