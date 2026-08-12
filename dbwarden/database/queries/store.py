from enum import Enum


class QueryMethod(Enum):
    CREATE_MIGRATIONS_TABLE = "create_migrations_table"
    CREATE_LOCK_TABLE = "create_lock_table"
    ADD_LOCK_OWNER_COLUMN = "add_lock_owner_column"
    INITIALIZE_LOCK = "initialize_lock"
    INSERT_VERSION = "insert_version"
    DELETE_VERSION = "delete_version"
    GET_ALL_MIGRATIONS = "get_all_migrations"
    GET_LATEST_VERSION = "get_latest_version"
    GET_MIGRATED_VERSIONS = "get_migrated_versions"
    CHECK_IF_MIGRATIONS_TABLE_EXISTS = "check_if_migrations_table_exists"
    CHECK_IF_VERSION_EXISTS = "check_if_version_exists"
    ACQUIRE_LOCK = "acquire_lock"
    RELEASE_LOCK = "release_lock"
    FORCE_RELEASE_LOCK = "force_release_lock"
    CHECK_LOCK = "check_lock"
    GET_TABLE_NAMES = "get_table_names"
    GET_TABLE_COLUMNS = "get_table_columns"
    GET_TABLE_INDEXES = "get_table_indexes"
    GET_RUNS_ON_CHANGE_CHECKSUMS = "get_runs_on_change_checksums"
    GET_RUNS_ALWAYS_FILENAMES = "get_runs_always_filenames"
    UPSERT_REPEATABLE_MIGRATION = "upsert_repeatable_migration"
    DELETE_REPEATABLE_BY_FILENAME = "delete_repeatable_by_filename"
    CREATE_SEEDS_TABLE = "create_seeds_table"
    INSERT_SEED = "insert_seed"
    CHECK_SEED_EXISTS = "check_seed_exists"
    GET_ALL_SEEDS = "get_all_seeds"
    GET_APPLIED_SEED_VERSIONS = "get_applied_seed_versions"
    DELETE_SEED = "delete_seed"
    GET_DISTINCT_CHECKSUMS = "get_distinct_checksums"
    GET_LATEST_VERSIONS_LIMIT = "get_latest_versions_limit"
    GET_LATEST_VERSIONS_FROM = "get_latest_versions_from"
    OPTIMIZE_MIGRATIONS_TABLE = "optimize_migrations_table"
    OPTIMIZE_LOCK_TABLE = "optimize_lock_table"
    OPTIMIZE_SEEDS_TABLE = "optimize_seeds_table"
    CREATE_MODEL_STATE_TABLE = "create_model_state_table"
    UPSERT_MODEL_STATE = "upsert_model_state"
    GET_MODEL_STATE = "get_model_state"


SQLITE_QUERIES = {
    QueryMethod.CREATE_MIGRATIONS_TABLE: """
        CREATE TABLE IF NOT EXISTS {migration_table} (
            version VARCHAR(255),
            description VARCHAR(500),
            filename VARCHAR(500) UNIQUE,
            migration_type VARCHAR(50),
            applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            checksum VARCHAR(128)
        )
    """,
    QueryMethod.CREATE_LOCK_TABLE: """
        CREATE TABLE IF NOT EXISTS dbwarden_lock (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            locked BOOLEAN DEFAULT FALSE,
            acquired_at TIMESTAMP,
            owner_token VARCHAR(128)
        )
    """,
    QueryMethod.INITIALIZE_LOCK: """
        INSERT INTO dbwarden_lock (id, locked, acquired_at, owner_token)
        VALUES (1, FALSE, NULL, NULL)
        ON CONFLICT (id) DO NOTHING
    """,
    QueryMethod.ADD_LOCK_OWNER_COLUMN: "ALTER TABLE dbwarden_lock ADD COLUMN owner_token VARCHAR(128)",
    QueryMethod.INSERT_VERSION: """
        INSERT INTO {migration_table} (version, description, filename, migration_type, checksum)
        VALUES (:version, :description, :filename, :migration_type, :checksum)
    """,
    QueryMethod.DELETE_VERSION: """
        DELETE FROM {migration_table} WHERE version = :version
    """,
    QueryMethod.GET_ALL_MIGRATIONS: """
        SELECT * FROM {migration_table} ORDER BY applied_at ASC
    """,
    QueryMethod.GET_LATEST_VERSION: """
        SELECT * FROM {migration_table}
        WHERE version IS NOT NULL
        ORDER BY applied_at DESC
        LIMIT 1
    """,
    QueryMethod.GET_MIGRATED_VERSIONS: """
        SELECT version FROM {migration_table} WHERE version IS NOT NULL ORDER BY applied_at ASC
    """,
    QueryMethod.CHECK_IF_MIGRATIONS_TABLE_EXISTS: """
        SELECT name FROM sqlite_master WHERE type='table' AND name='{migration_table}'
    """,
    QueryMethod.CHECK_IF_VERSION_EXISTS: """
        SELECT COUNT(*) FROM {migration_table} WHERE version = :version
    """,
    QueryMethod.ACQUIRE_LOCK: """
        UPDATE dbwarden_lock
        SET locked = TRUE, acquired_at = CURRENT_TIMESTAMP, owner_token = :owner_token
        WHERE id = 1 AND locked = FALSE
    """,
    QueryMethod.RELEASE_LOCK: """
        UPDATE dbwarden_lock
        SET locked = FALSE, acquired_at = NULL, owner_token = NULL
        WHERE id = 1 AND owner_token = :owner_token
    """,
    QueryMethod.FORCE_RELEASE_LOCK: """
        UPDATE dbwarden_lock
        SET locked = FALSE, acquired_at = NULL, owner_token = NULL
        WHERE id = 1
    """,
    QueryMethod.CHECK_LOCK: """
        SELECT locked FROM dbwarden_lock WHERE id = 1
    """,
    QueryMethod.GET_TABLE_NAMES: """
        SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name
    """,
    QueryMethod.GET_TABLE_COLUMNS: """
        PRAGMA table_info(:table_name)
    """,
    QueryMethod.GET_TABLE_INDEXES: """
        SELECT name, sql FROM sqlite_master WHERE type='index' AND tbl_name=:table_name
    """,
    QueryMethod.GET_RUNS_ON_CHANGE_CHECKSUMS: """
        SELECT filename, checksum FROM {migration_table}
        WHERE migration_type = 'runs_on_change'
    """,
    QueryMethod.GET_RUNS_ALWAYS_FILENAMES: """
        SELECT filename FROM {migration_table}
        WHERE migration_type = 'runs_always'
    """,
    QueryMethod.UPSERT_REPEATABLE_MIGRATION: """
        INSERT OR REPLACE INTO {migration_table}
        (version, description, filename, migration_type, checksum)
        VALUES (NULL, :description, :filename, :migration_type, :checksum)
    """,
    QueryMethod.DELETE_REPEATABLE_BY_FILENAME: """
        DELETE FROM {migration_table}
        WHERE filename = :filename AND migration_type IN ('runs_always', 'runs_on_change')
    """,
    QueryMethod.CREATE_SEEDS_TABLE: """
        CREATE TABLE IF NOT EXISTS {seed_table} (
            version VARCHAR(255),
            description VARCHAR(500),
            filename VARCHAR(500) UNIQUE,
            seed_type VARCHAR(10),
            applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            checksum VARCHAR(128)
        )
    """,
    QueryMethod.INSERT_SEED: """
        INSERT INTO {seed_table} (version, description, filename, seed_type, checksum)
        VALUES (:version, :description, :filename, :seed_type, :checksum)
    """,
    QueryMethod.CHECK_SEED_EXISTS: """
        SELECT COUNT(*) FROM {seed_table} WHERE version = :version
    """,
    QueryMethod.GET_ALL_SEEDS: """
        SELECT * FROM {seed_table} ORDER BY applied_at ASC
    """,
    QueryMethod.GET_APPLIED_SEED_VERSIONS: """
        SELECT version FROM {seed_table} ORDER BY applied_at ASC
    """,
    QueryMethod.DELETE_SEED: """
        DELETE FROM {seed_table} WHERE version = :version
    """,
    QueryMethod.GET_DISTINCT_CHECKSUMS: """
        SELECT DISTINCT checksum FROM {migration_table} WHERE checksum IS NOT NULL
    """,
    QueryMethod.GET_LATEST_VERSIONS_LIMIT: """
        SELECT version FROM {migration_table} WHERE version IS NOT NULL ORDER BY applied_at DESC, version DESC LIMIT :limit
    """,
    QueryMethod.GET_LATEST_VERSIONS_FROM: """
        SELECT version FROM {migration_table} WHERE version > :starting_version AND version IS NOT NULL ORDER BY applied_at ASC
    """,
    QueryMethod.CREATE_MODEL_STATE_TABLE: """
        CREATE TABLE IF NOT EXISTS _dbwarden_model_state (
            id INTEGER PRIMARY KEY DEFAULT 1 CHECK (id = 1),
            model_state TEXT,
            format_version INTEGER,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """,
    QueryMethod.UPSERT_MODEL_STATE: """
        INSERT OR REPLACE INTO _dbwarden_model_state (id, model_state, format_version, updated_at)
        VALUES (1, :state, :fmt, CURRENT_TIMESTAMP)
    """,
    QueryMethod.GET_MODEL_STATE: """
        SELECT model_state, format_version, updated_at FROM _dbwarden_model_state WHERE id = 1
    """,
}


POSTGRES_QUERIES = {
    QueryMethod.CREATE_MIGRATIONS_TABLE: """
        CREATE TABLE IF NOT EXISTS {schema}.{migration_table} (
            version VARCHAR(255) UNIQUE,
            description VARCHAR(500),
            filename VARCHAR(500) UNIQUE,
            migration_type VARCHAR(50),
            applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            checksum VARCHAR(128)
        )
    """,
    QueryMethod.CREATE_LOCK_TABLE: """
        CREATE TABLE IF NOT EXISTS {schema}.dbwarden_lock (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            locked BOOLEAN DEFAULT FALSE,
            acquired_at TIMESTAMP,
            owner_token VARCHAR(128)
        )
    """,
    QueryMethod.INITIALIZE_LOCK: """
        INSERT INTO {schema}.dbwarden_lock (id, locked, acquired_at, owner_token)
        VALUES (1, FALSE, NULL, NULL)
        ON CONFLICT (id) DO NOTHING
    """,
    QueryMethod.ADD_LOCK_OWNER_COLUMN: "ALTER TABLE {schema}.dbwarden_lock ADD COLUMN IF NOT EXISTS owner_token VARCHAR(128)",
    QueryMethod.INSERT_VERSION: """
        INSERT INTO {schema}.{migration_table} (version, description, filename, migration_type, checksum)
        VALUES (:version, :description, :filename, :migration_type, :checksum)
        ON CONFLICT (version) DO UPDATE SET
            checksum = EXCLUDED.checksum,
            applied_at = CURRENT_TIMESTAMP
    """,
    QueryMethod.DELETE_VERSION: """
        DELETE FROM {schema}.{migration_table} WHERE version = :version
    """,
    QueryMethod.GET_ALL_MIGRATIONS: """
        SELECT version, description, filename, migration_type, applied_at, checksum
        FROM {schema}.{migration_table}
        ORDER BY version ASC
    """,
    QueryMethod.GET_LATEST_VERSION: """
        SELECT version FROM {schema}.{migration_table}
        WHERE migration_type = 'version'
        ORDER BY version DESC LIMIT 1
    """,
    QueryMethod.GET_MIGRATED_VERSIONS: """
        SELECT version FROM {schema}.{migration_table}
        WHERE migration_type = 'version'
        ORDER BY version ASC
    """,
    QueryMethod.CHECK_IF_MIGRATIONS_TABLE_EXISTS: """
        SELECT tablename FROM pg_catalog.pg_tables
        WHERE schemaname = '{schema}' AND tablename = '{migration_table}'
    """,
    QueryMethod.CHECK_IF_VERSION_EXISTS: """
        SELECT COUNT(*) FROM {schema}.{migration_table} WHERE version = :version
    """,
    QueryMethod.ACQUIRE_LOCK: """
        UPDATE {schema}.dbwarden_lock
        SET locked = TRUE, acquired_at = CURRENT_TIMESTAMP, owner_token = :owner_token
        WHERE id = 1 AND locked = FALSE
    """,
    QueryMethod.RELEASE_LOCK: """
        UPDATE {schema}.dbwarden_lock
        SET locked = FALSE, acquired_at = NULL, owner_token = NULL
        WHERE id = 1 AND owner_token = :owner_token
    """,
    QueryMethod.FORCE_RELEASE_LOCK: """
        UPDATE {schema}.dbwarden_lock
        SET locked = FALSE, acquired_at = NULL, owner_token = NULL
        WHERE id = 1
    """,
    QueryMethod.CHECK_LOCK: """
        SELECT locked FROM {schema}.dbwarden_lock WHERE id = 1
    """,
    QueryMethod.GET_TABLE_NAMES: """
        SELECT tablename AS name
        FROM pg_catalog.pg_tables
        WHERE schemaname = current_schema()
        ORDER BY tablename
    """,
    QueryMethod.GET_TABLE_COLUMNS: """
        SELECT column_name AS name, data_type AS type
        FROM information_schema.columns
        WHERE table_schema = current_schema() AND table_name = :table_name
        ORDER BY ordinal_position
    """,
    QueryMethod.GET_TABLE_INDEXES: """
        SELECT indexname AS name, indexdef AS sql
        FROM pg_indexes
        WHERE schemaname = current_schema() AND tablename = :table_name
    """,
    QueryMethod.GET_RUNS_ON_CHANGE_CHECKSUMS: """
        SELECT filename, checksum FROM {schema}.{migration_table}
        WHERE migration_type = 'runs_on_change'
    """,
    QueryMethod.GET_RUNS_ALWAYS_FILENAMES: """
        SELECT filename FROM {schema}.{migration_table}
        WHERE migration_type = 'runs_always'
    """,
    QueryMethod.UPSERT_REPEATABLE_MIGRATION: """
        INSERT INTO {schema}.{migration_table}
        (version, description, filename, migration_type, checksum)
        VALUES (NULL, :description, :filename, :migration_type, :checksum)
        ON CONFLICT (filename)
        DO UPDATE SET
            description = EXCLUDED.description,
            migration_type = EXCLUDED.migration_type,
            checksum = EXCLUDED.checksum,
            applied_at = CURRENT_TIMESTAMP
    """,
    QueryMethod.DELETE_REPEATABLE_BY_FILENAME: """
        DELETE FROM {schema}.{migration_table} WHERE filename = :filename
    """,
    QueryMethod.CREATE_SEEDS_TABLE: """
        CREATE TABLE IF NOT EXISTS {schema}.{seed_table} (
            version VARCHAR(255) UNIQUE,
            description VARCHAR(500),
            filename VARCHAR(500) UNIQUE,
            seed_type VARCHAR(10),
            applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            checksum VARCHAR(128)
        )
    """,
    QueryMethod.INSERT_SEED: """
        INSERT INTO {schema}.{seed_table} (version, description, filename, seed_type, checksum)
        VALUES (:version, :description, :filename, :seed_type, :checksum)
        ON CONFLICT (version) DO UPDATE SET
            checksum = EXCLUDED.checksum,
            applied_at = CURRENT_TIMESTAMP
    """,
    QueryMethod.CHECK_SEED_EXISTS: """
        SELECT COUNT(*) FROM {schema}.{seed_table} WHERE version = :version
    """,
    QueryMethod.GET_ALL_SEEDS: """
        SELECT * FROM {schema}.{seed_table} ORDER BY applied_at ASC
    """,
    QueryMethod.GET_APPLIED_SEED_VERSIONS: """
        SELECT version FROM {schema}.{seed_table} ORDER BY applied_at ASC
    """,
    QueryMethod.DELETE_SEED: """
        DELETE FROM {schema}.{seed_table} WHERE version = :version
    """,
    QueryMethod.GET_DISTINCT_CHECKSUMS: """
        SELECT DISTINCT checksum FROM {schema}.{migration_table} WHERE checksum IS NOT NULL
    """,
    QueryMethod.GET_LATEST_VERSIONS_LIMIT: """
        SELECT version FROM {schema}.{migration_table} WHERE version IS NOT NULL ORDER BY applied_at DESC, version DESC LIMIT :limit
    """,
    QueryMethod.GET_LATEST_VERSIONS_FROM: """
        SELECT version FROM {schema}.{migration_table} WHERE version > :starting_version AND version IS NOT NULL ORDER BY applied_at ASC
    """,
    QueryMethod.CREATE_MODEL_STATE_TABLE: """
        CREATE TABLE IF NOT EXISTS {schema}._dbwarden_model_state (
            id INTEGER PRIMARY KEY DEFAULT 1 CHECK (id = 1),
            model_state JSONB,
            format_version INTEGER,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """,
    QueryMethod.UPSERT_MODEL_STATE: """
        INSERT INTO {schema}._dbwarden_model_state (id, model_state, format_version, updated_at)
        VALUES (1, :state::jsonb, :fmt, CURRENT_TIMESTAMP)
        ON CONFLICT (id)
        DO UPDATE SET model_state = EXCLUDED.model_state, format_version = EXCLUDED.format_version, updated_at = CURRENT_TIMESTAMP
    """,
    QueryMethod.GET_MODEL_STATE: """
        SELECT model_state, format_version, updated_at FROM {schema}._dbwarden_model_state WHERE id = 1
    """,
}


MYSQL_QUERIES = {
    QueryMethod.CREATE_MIGRATIONS_TABLE: """
        CREATE TABLE IF NOT EXISTS {migration_table} (
            version VARCHAR(255),
            description VARCHAR(500),
            filename VARCHAR(500) UNIQUE,
            migration_type VARCHAR(50),
            applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            checksum VARCHAR(128)
        )
    """,
    QueryMethod.CREATE_LOCK_TABLE: """
        CREATE TABLE IF NOT EXISTS dbwarden_lock (
            id INT PRIMARY KEY,
            locked BOOLEAN DEFAULT FALSE,
            acquired_at TIMESTAMP NULL,
            owner_token VARCHAR(128) NULL
        )
    """,
    QueryMethod.INITIALIZE_LOCK: """
        INSERT INTO dbwarden_lock (id, locked, acquired_at, owner_token)
        VALUES (1, FALSE, NULL, NULL)
        ON DUPLICATE KEY UPDATE id = id
    """,
    QueryMethod.ADD_LOCK_OWNER_COLUMN: "ALTER TABLE dbwarden_lock ADD COLUMN owner_token VARCHAR(128) NULL",
    QueryMethod.INSERT_VERSION: SQLITE_QUERIES[QueryMethod.INSERT_VERSION],
    QueryMethod.DELETE_VERSION: SQLITE_QUERIES[QueryMethod.DELETE_VERSION],
    QueryMethod.GET_ALL_MIGRATIONS: SQLITE_QUERIES[QueryMethod.GET_ALL_MIGRATIONS],
    QueryMethod.GET_LATEST_VERSION: SQLITE_QUERIES[QueryMethod.GET_LATEST_VERSION],
    QueryMethod.GET_MIGRATED_VERSIONS: SQLITE_QUERIES[QueryMethod.GET_MIGRATED_VERSIONS],
    QueryMethod.CHECK_IF_MIGRATIONS_TABLE_EXISTS: """
        SELECT table_name AS name
        FROM information_schema.tables
        WHERE table_schema = DATABASE() AND table_name = '{migration_table}'
    """,
    QueryMethod.CHECK_IF_VERSION_EXISTS: SQLITE_QUERIES[QueryMethod.CHECK_IF_VERSION_EXISTS],
    QueryMethod.ACQUIRE_LOCK: """
        UPDATE dbwarden_lock
        SET locked = TRUE, acquired_at = CURRENT_TIMESTAMP, owner_token = :owner_token
        WHERE id = 1 AND locked = FALSE
    """,
    QueryMethod.RELEASE_LOCK: """
        UPDATE dbwarden_lock
        SET locked = FALSE, acquired_at = NULL, owner_token = NULL
        WHERE id = 1 AND owner_token = :owner_token
    """,
    QueryMethod.FORCE_RELEASE_LOCK: """
        ALTER TABLE dbwarden_lock UPDATE locked = 0, acquired_at = NULL, owner_token = NULL WHERE id = 1
    """,
    QueryMethod.CHECK_LOCK: SQLITE_QUERIES[QueryMethod.CHECK_LOCK],
    QueryMethod.GET_TABLE_NAMES: """
        SELECT table_name AS name
        FROM information_schema.tables
        WHERE table_schema = DATABASE()
        ORDER BY table_name
    """,
    QueryMethod.GET_TABLE_COLUMNS: """
        SELECT column_name AS name, column_type AS type
        FROM information_schema.columns
        WHERE table_schema = DATABASE() AND table_name = :table_name
        ORDER BY ordinal_position
    """,
    QueryMethod.GET_TABLE_INDEXES: """
        SELECT index_name AS name, NULL AS sql
        FROM information_schema.statistics
        WHERE table_schema = DATABASE() AND table_name = :table_name
    """,
    QueryMethod.GET_RUNS_ON_CHANGE_CHECKSUMS: SQLITE_QUERIES[QueryMethod.GET_RUNS_ON_CHANGE_CHECKSUMS],
    QueryMethod.GET_RUNS_ALWAYS_FILENAMES: SQLITE_QUERIES[QueryMethod.GET_RUNS_ALWAYS_FILENAMES],
    QueryMethod.UPSERT_REPEATABLE_MIGRATION: """
        INSERT INTO {migration_table}
        (version, description, filename, migration_type, checksum)
        VALUES (NULL, :description, :filename, :migration_type, :checksum)
        ON DUPLICATE KEY UPDATE
            description = VALUES(description),
            migration_type = VALUES(migration_type),
            checksum = VALUES(checksum),
            applied_at = CURRENT_TIMESTAMP
    """,
    QueryMethod.DELETE_REPEATABLE_BY_FILENAME: SQLITE_QUERIES[QueryMethod.DELETE_REPEATABLE_BY_FILENAME],
    QueryMethod.CREATE_SEEDS_TABLE: """
        CREATE TABLE IF NOT EXISTS {seed_table} (
            version VARCHAR(255),
            description VARCHAR(500),
            filename VARCHAR(500) UNIQUE,
            seed_type VARCHAR(10),
            applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            checksum VARCHAR(128)
        )
    """,
    QueryMethod.INSERT_SEED: """
        INSERT INTO {seed_table} (version, description, filename, seed_type, checksum)
        VALUES (:version, :description, :filename, :seed_type, :checksum)
        ON DUPLICATE KEY UPDATE
            checksum = VALUES(checksum),
            applied_at = CURRENT_TIMESTAMP
    """,
    QueryMethod.CHECK_SEED_EXISTS: SQLITE_QUERIES[QueryMethod.CHECK_SEED_EXISTS],
    QueryMethod.GET_ALL_SEEDS: SQLITE_QUERIES[QueryMethod.GET_ALL_SEEDS],
    QueryMethod.GET_APPLIED_SEED_VERSIONS: SQLITE_QUERIES[QueryMethod.GET_APPLIED_SEED_VERSIONS],
    QueryMethod.DELETE_SEED: SQLITE_QUERIES[QueryMethod.DELETE_SEED],
    QueryMethod.GET_DISTINCT_CHECKSUMS: SQLITE_QUERIES[QueryMethod.GET_DISTINCT_CHECKSUMS],
    QueryMethod.GET_LATEST_VERSIONS_LIMIT: SQLITE_QUERIES[QueryMethod.GET_LATEST_VERSIONS_LIMIT],
    QueryMethod.GET_LATEST_VERSIONS_FROM: SQLITE_QUERIES[QueryMethod.GET_LATEST_VERSIONS_FROM],
    QueryMethod.CREATE_MODEL_STATE_TABLE: """
        CREATE TABLE IF NOT EXISTS _dbwarden_model_state (
            id INT PRIMARY KEY DEFAULT 1,
            model_state JSON,
            format_version INT,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """,
    QueryMethod.UPSERT_MODEL_STATE: """
        INSERT INTO _dbwarden_model_state (id, model_state, format_version, updated_at)
        VALUES (1, :state, :fmt, CURRENT_TIMESTAMP)
        ON DUPLICATE KEY UPDATE
            model_state = VALUES(model_state),
            format_version = VALUES(format_version),
            updated_at = CURRENT_TIMESTAMP
    """,
    QueryMethod.GET_MODEL_STATE: """
        SELECT model_state, format_version, updated_at FROM _dbwarden_model_state WHERE id = 1
    """,
}


CLICKHOUSE_QUERIES = {
    QueryMethod.CREATE_MIGRATIONS_TABLE: """
        CREATE TABLE IF NOT EXISTS {migration_table} (
            version String,
            description String,
            filename String,
            migration_type String,
            applied_at DateTime DEFAULT now(),
            checksum String
        ) ENGINE = MergeTree()
        ORDER BY filename
    """,
    QueryMethod.CREATE_LOCK_TABLE: """
        CREATE TABLE IF NOT EXISTS dbwarden_lock (
            id Int32,
            locked UInt8 DEFAULT 0,
            acquired_at Nullable(DateTime),
            owner_token Nullable(String)
        ) ENGINE = MergeTree()
        ORDER BY id
    """,
    QueryMethod.INITIALIZE_LOCK: """
        INSERT INTO dbwarden_lock (id, locked, acquired_at, owner_token)
        VALUES (1, 0, NULL, NULL)
    """,
    QueryMethod.ADD_LOCK_OWNER_COLUMN: "ALTER TABLE dbwarden_lock ADD COLUMN IF NOT EXISTS owner_token Nullable(String)",
    QueryMethod.INSERT_VERSION: """
        INSERT INTO {migration_table} (version, description, filename, migration_type, checksum)
        VALUES (:version, :description, :filename, :migration_type, :checksum)
    """,
    QueryMethod.DELETE_VERSION: """
        ALTER TABLE {migration_table} DELETE WHERE version = :version
    """,
    QueryMethod.OPTIMIZE_MIGRATIONS_TABLE: """
        OPTIMIZE TABLE {migration_table} FINAL
    """,
    QueryMethod.OPTIMIZE_LOCK_TABLE: """
        OPTIMIZE TABLE dbwarden_lock FINAL
    """,
    QueryMethod.OPTIMIZE_SEEDS_TABLE: """
        OPTIMIZE TABLE {seed_table} FINAL
    """,
    QueryMethod.GET_ALL_MIGRATIONS: """
        SELECT version, description, filename, migration_type, applied_at, checksum
        FROM {migration_table}
        ORDER BY applied_at ASC
    """,
    QueryMethod.GET_LATEST_VERSION: """
        SELECT version, description, filename, migration_type, applied_at, checksum
        FROM {migration_table}
        WHERE version IS NOT NULL
        ORDER BY applied_at DESC
        LIMIT 1
    """,
    QueryMethod.GET_MIGRATED_VERSIONS: """
        SELECT version FROM {migration_table}
        WHERE version IS NOT NULL
        ORDER BY applied_at ASC
    """,
    QueryMethod.CHECK_IF_MIGRATIONS_TABLE_EXISTS: """
        SELECT name FROM system.tables
        WHERE database = currentDatabase() AND name = '{migration_table}'
    """,
    QueryMethod.CHECK_IF_VERSION_EXISTS: """
        SELECT count() FROM {migration_table} WHERE version = :version
    """,
    QueryMethod.ACQUIRE_LOCK: """
        ALTER TABLE dbwarden_lock UPDATE locked = 1, acquired_at = now(), owner_token = :owner_token WHERE id = 1 AND locked = 0
    """,
    QueryMethod.RELEASE_LOCK: """
        ALTER TABLE dbwarden_lock UPDATE locked = 0, acquired_at = NULL, owner_token = NULL WHERE id = 1 AND owner_token = :owner_token
    """,
    QueryMethod.CHECK_LOCK: """
        SELECT locked FROM dbwarden_lock WHERE id = 1
    """,
    QueryMethod.GET_TABLE_NAMES: """
        SELECT name FROM system.tables
        WHERE database = currentDatabase()
        ORDER BY name
    """,
    QueryMethod.GET_TABLE_COLUMNS: """
        SELECT name, type, is_nullable, default_expression
        FROM system.columns
        WHERE database = currentDatabase() AND table = :table_name
    """,
    QueryMethod.GET_TABLE_INDEXES: """
        SELECT name, expression FROM system.data_skipping_indices
        WHERE database = currentDatabase() AND table = :table_name
    """,
    QueryMethod.GET_RUNS_ON_CHANGE_CHECKSUMS: """
        SELECT filename, checksum FROM {migration_table}
        WHERE migration_type = 'runs_on_change'
    """,
    QueryMethod.GET_RUNS_ALWAYS_FILENAMES: """
        SELECT filename FROM {migration_table}
        WHERE migration_type = 'runs_always'
    """,
    QueryMethod.UPSERT_REPEATABLE_MIGRATION: """
        INSERT INTO {migration_table} (version, description, filename, migration_type, checksum)
        VALUES (NULL, :description, :filename, :migration_type, :checksum)
    """,
    QueryMethod.DELETE_REPEATABLE_BY_FILENAME: """
        ALTER TABLE {migration_table} DELETE WHERE filename = :filename AND migration_type IN ('runs_always', 'runs_on_change')
    """,
    QueryMethod.CREATE_SEEDS_TABLE: """
        CREATE TABLE IF NOT EXISTS {seed_table} (
            version String,
            description String,
            filename String,
            seed_type String,
            applied_at DateTime DEFAULT now(),
            checksum String
        ) ENGINE = MergeTree()
        ORDER BY version
    """,
    QueryMethod.INSERT_SEED: """
        INSERT INTO {seed_table} (version, description, filename, seed_type, checksum)
        VALUES (:version, :description, :filename, :seed_type, :checksum)
    """,
    QueryMethod.CHECK_SEED_EXISTS: """
        SELECT count() FROM {seed_table} WHERE version = :version
    """,
    QueryMethod.GET_ALL_SEEDS: """
        SELECT version, description, filename, seed_type, applied_at, checksum
        FROM {seed_table}
        ORDER BY applied_at ASC
    """,
    QueryMethod.GET_APPLIED_SEED_VERSIONS: """
        SELECT version FROM {seed_table}
        ORDER BY applied_at ASC
    """,
    QueryMethod.DELETE_SEED: """
        ALTER TABLE {seed_table} DELETE WHERE version = :version
    """,
    QueryMethod.GET_DISTINCT_CHECKSUMS: """
        SELECT DISTINCT checksum FROM {migration_table} WHERE checksum IS NOT NULL
    """,
    QueryMethod.GET_LATEST_VERSIONS_LIMIT: """
        SELECT version FROM {migration_table} WHERE version IS NOT NULL ORDER BY applied_at DESC, version DESC LIMIT :limit
    """,
    QueryMethod.GET_LATEST_VERSIONS_FROM: """
        SELECT version FROM {migration_table} WHERE version > :starting_version AND version IS NOT NULL ORDER BY applied_at ASC
    """,
    QueryMethod.CREATE_MODEL_STATE_TABLE: """
        CREATE TABLE IF NOT EXISTS _dbwarden_model_state (
            id Int32,
            model_state String,
            format_version Int32,
            updated_at DateTime DEFAULT now()
        ) ENGINE = ReplacingMergeTree()
        ORDER BY id
    """,
    QueryMethod.UPSERT_MODEL_STATE: """
        INSERT INTO _dbwarden_model_state (id, model_state, format_version, updated_at)
        VALUES (1, :state, :fmt, now())
    """,
    QueryMethod.GET_MODEL_STATE: """
        SELECT model_state, format_version, updated_at FROM _dbwarden_model_state WHERE id = 1
    """,
}
