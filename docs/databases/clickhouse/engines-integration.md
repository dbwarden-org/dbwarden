# Integration engines

dbwarden supports 13 integration engines. Credentials use the [named-collections](named-collections.md) declare-only pattern: no secret values are diffed.

## Kafka

```python
from dbwarden.databases.clickhouse import kafka

class Meta(CHTableMeta):
    ch = ch_table(
        engine=kafka(
            named_collection="kafka_prod",
            topic_list="events",
            format="JSONEachRow",
            group_name="dbwarden_consumer",
        ),
    )
```

Generated DDL:

```sql
CREATE TABLE kafka_events (
    payload String
) ENGINE = Kafka
SETTINGS kafka_named_collection = 'kafka_prod',
         kafka_topic_list = 'events',
         kafka_format = 'JSONEachRow',
         kafka_group_name = 'dbwarden_consumer';
```

Parameters that can be set directly (overriding the named collection):

| Factory parameter | DDL setting |
|------------------|-------------|
| `named_collection` | collection name argument |
| `broker_list` | `kafka_broker_list` |
| `topic_list` | `kafka_topic_list` |
| `group_name` | `kafka_group_name` |
| `format` | `kafka_format` |
| `num_consumers` | `kafka_num_consumers` |

Other Kafka settings (`kafka_thread_per_consumer`, `kafka_handle_error_mode`,
`kafka_commit_every_batch`, ...) are set through the `KafkaSettings` TypedDict
below, not as `kafka()` parameters.

`KafkaSettings` is a fully-typed TypedDict for arbitrary Kafka engine settings:

```python
from dbwarden.databases.clickhouse import KafkaSettings

settings: KafkaSettings = {
    "kafka_max_block_size": 524288,
}
```

## S3

```python
from dbwarden.databases.clickhouse import s3

class Meta(CHTableMeta):
    ch = ch_table(
        engine=s3(
            named_collection="s3_prod",
            path="events/*.parquet",
            format="Parquet",
        ),
    )
```

Parameters:

| Parameter | DDL setting |
|-----------|-------------|
| `named_collection` | collection name argument |
| `path` | `url` |
| `format` | `format` |
| `compression` | `compression` |

`S3Settings` for arbitrary settings. Key naming variants (`s3_*`, `s3queue_*`) are all typed.

## S3Queue

```python
from dbwarden.databases.clickhouse import s3_queue

class Meta(CHTableMeta):
    ch = ch_table(
        engine=s3_queue(
            named_collection="s3_prod",
            path="incoming/*.json",
            format="JSONEachRow",
        ),
    )
```

`S3QueueSettings` covers all s3queue-specific settings.

## RabbitMQ

```python
from dbwarden.databases.clickhouse import rabbitmq

class Meta(CHTableMeta):
    ch = ch_table(
        engine=rabbitmq(
            named_collection="rabbit_prod",
            format="JSONEachRow",
        ),
    )
```

`RabbitMQSettings`, typed TypedDict.

## NATS

```python
from dbwarden.databases.clickhouse import nats

class Meta(CHTableMeta):
    ch = ch_table(
        engine=nats(
            named_collection="nats_prod",
            format="JSONEachRow",
        ),
    )
```

`NatsSettings`, typed TypedDict.

## MySQL, PostgreSQL, MongoDB, Redis

```python
from dbwarden.databases.clickhouse import mysql_engine, postgresql_engine, mongodb, redis

# MySQL engine
engine = mysql_engine("mysql-host.example.com", 3306, "source_db", "source_table", "reader", "secret")

# PostgreSQL engine
engine = postgresql_engine("pg-host.example.com", 5432, "source_db", "source_table", "reader", "secret")

# MongoDB engine
engine = mongodb("mongo-host.example.com", 27017, "source_db", "source_collection", "reader", "secret")

# Redis engine (storage is the database index)
engine = redis("redis-host.example.com", 6379, "secret", 0)
```

Each has an associated `*Settings` TypedDict for engine-specific settings.

## Additional model examples

### Named collection for multi-engine reuse

```python
# Single named collection reused by Kafka and S3 engines
named_collection(
    name="aws_prod",
    keys={
        "region": "us-east-1",
        "access_key_id": "AKIA...",
        # secret_access_key from secret store
    },
)

engine = kafka(
    named_collection="aws_prod",
    topic_list="events",
    format="JSONEachRow",
    group_name="ch_consumer",
)

engine2 = s3(
    named_collection="aws_prod",
    path="data/*.parquet",
    format="Parquet",
)
```

### S3Queue with complex settings

```python
engine = s3_queue(
    named_collection="aws_prod",
    path="incoming/*.json",
    format="JSONEachRow",
)

# With custom settings
settings: S3QueueSettings = {
    "s3queue_processing_threads": 8,
    "s3queue_polling_min_timeout_ms": 1000,
    "s3queue_polling_max_timeout_ms": 30000,
    "s3queue_tracked_files_limit": 100000,
}
```

### PostgreSQL engine

```python
engine = postgresql_engine("pg-host.example.com", 5432, "source_db", "users", "reader", "secret")
```

### URL engine with multiple formats

```python
# CSV
engine = url_engine("https://data.example.com/export.csv", "CSV")

# JSONEachRow
engine = url_engine("https://data.example.com/events.json", "JSONEachRow")
```

## URL

```python
from dbwarden.databases.clickhouse import url_engine

class Meta(CHTableMeta):
    ch = ch_table(
        engine=url_engine("https://data.example.com/export.csv", "CSV"),
    )
```

`URLSettings`.

## File

```python
from dbwarden.databases.clickhouse import file_engine

class Meta(CHTableMeta):
    ch = ch_table(
        engine=file_engine(
            path="/var/lib/clickhouse/user_files/export.csv",
            format="CSV",
        ),
    )
```

## HDFS

```python
from dbwarden.databases.clickhouse import hdfs

class Meta(CHTableMeta):
    ch = ch_table(
        engine=hdfs("hdfs://namenode:9000/data/events.parquet", "Parquet"),
    )
```

`HDFSSettings`.

## Per-engine settings TypedDicts

Every integration engine has its own `*Settings` TypedDict for arbitrary settings. These are all defined in `dbwarden.databases.clickhouse`:

| Engine | TypedDict |
|--------|-----------|
| Kafka | `KafkaSettings` |
| S3 | `S3Settings` |
| S3Queue | `S3QueueSettings` |
| RabbitMQ | `RabbitMQSettings` |
| NATS | `NatsSettings` |
| MySQL | `MySQLSettings` |
| PostgreSQL | `PostgreSQLSettings` |
| MongoDB | `MongoDBSettings` |
| Redis | `RedisSettings` |
| URL | `URLSettings` |
| HDFS | `HDFSSettings` |

## What changes are allowed

| Change | Safety |
|--------|--------|
| Named collection swap | CRITICAL (metadata only, not data) |
| Any setting | INFO: `ALTER TABLE MODIFY SETTING` |
| Format change | WARN: requires data re-ingestion |
| Pattern / query / key change | WARN |

## Rollback behavior

Settings changes revert via `RESET SETTING`. Named collection swaps require reversing the collection reference.
