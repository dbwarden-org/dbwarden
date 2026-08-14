# Named collections

**Requires the `dbwarden-ch-rbac` plugin:** `dbwarden plugin add dbwarden-ch-rbac`. The `ch_named_collection` object handler ships in that plugin, not in core.

Named collections are the mechanism for declaring credentials without exposing secret values. They are declared in the config layer, referenced by name from engine and RBAC specs. The examples use the supported `database_config(...)` function form for plugin-owned settings; new projects may declare the same plugin configuration on a `DbwardenDatabase` subclass.

## Declaration

```python
from dbwarden import database_config
from dbwarden.databases.clickhouse import named_collection

database_config(
    database_name="analytics",
    database_type="clickhouse",
    database_url_sync="clickhouse://...",
    ch_named_collections=[
        named_collection(
            name="kafka_prod",
            keys={
                "sasl_username": "kafka_user",
# sasl_password is NOT declared here: it comes from the
        # secret store. See "declare-only" below.
            },
        ),
        named_collection(
            name="s3_prod",
            keys={
                "region": "us-east-1",
                "access_key_id": "AKIA...",
            },
        ),
    ],
)
```

## Key-set diffed, values declare-only

Named collections are diffed on their **key set**: what keys are declared and what values are referenced. The **values themselves are never diffed**. This is the "declare-only" principle:

- `named_collection("kafka_prod", keys={"sasl_username": "kafka_user"})` declares that a collection named `kafka_prod` should have the key `sasl_username`.
- The value `"kafka_user"` is metadata for dbwarden's diff output but is **never compared** to the server state. Secret values (`password`, `sasl_password`, `secret_access_key`) are not declared at all: they come from ClickHouse's secret store.

This means dbwarden will detect that a key exists in a model but is missing from the server, and emit `CREATE NAMED COLLECTION ...`. But it will never emit `ALTER NAMED COLLECTION` with a changed password value: it can't know the real value.

## Additional model examples

### Named collection for Postgres engine

```python
named_collection(
    name="pg_source",
    keys={
        "connection_string": "postgresql://user:pass@pg-host:5432/db",
        "port": "5432",
    },
)
```

### Named collection with cluster and secret reference

```python
named_collection(
    name="kafka_secure",
    keys={
        "bootstrap_servers": "kafka-broker:9092",
        "sasl_mechanism": "SCRAM-SHA-256",
        "sasl_username": "ch_user",
        # sasl_password = SECRET(...) stored in ClickHouse secret store
        "security_protocol": "sasl_ssl",
    },
)
```

Engine usage:

```python
engine = kafka(
    named_collection="kafka_secure",
    topic="events",
    format="Avro",
    group_name="dbwarden",
)
```

### Dict config (raw path)

```python
database_config(
    database_name="analytics",
    database_type="clickhouse",
    database_url_sync="clickhouse://localhost:9000",
    ch_named_collections=[
        {
            "name": "s3_data",
            "keys": {
                "region": "us-east-1",
                "url": "https://s3.amazonaws.com/mybucket",
            },
        },
    ],
)
```

## Reference from engines

```python
engine = kafka(
    named_collection="kafka_prod",
    topic="events",
)
```

The engine gets credentials from the named collection. The named collection is referenced by name only in the engine settings (`kafka_named_collection`, `s3_named_collection`, etc.).

## Reference from RBAC

```python
ChUserSpec(
    named_collection="ldap_prod",
    ...
)
```

## What changes are allowed

| Change | Safety |
|--------|--------|
| Add named collection | INFO |
| Drop named collection | WARN (may break references) |
| Add key to declaration | INFO |
| Remove key from declaration | WARN |
| Change a non-secret value | INFO |
| Secret values | Not tracked |

## Rollback behavior

`DROP NAMED COLLECTION` rolls back as `CREATE NAMED COLLECTION`. Key changes roll back as inverse key changes.
# Named Collections

The examples use the supported `database_config(...)` function form for
plugin-owned settings. New projects may declare the same plugin configuration
on a `DbwardenDatabase` subclass.
