---
seo:
  title: MySQL & MariaDB - DBWarden Documentation
  canonical: https://dbwarden.emiliano-go.com/databases/mysql
  robots: index,follow
  og:
    type: website
    title: MySQL & MariaDB - DBWarden Documentation
    description: 'DBWarden treats MySQL and its fork MariaDB as first-class backends:
      every natively supported feature is reverse-engineered, diffed, and emitted
      as correct DDL.'
    url: https://dbwarden.emiliano-go.com/databases/mysql
    image: https://dbwarden.emiliano-go.com/assets/images/og-image.png
    image:width: 1376
    image:height: 768
    image:alt: DBWarden documentation
    site_name: DBWarden Documentation
    locale: en_US
  twitter:
    card: summary_large_image
    title: MySQL & MariaDB - DBWarden Documentation
    description: 'DBWarden treats MySQL and its fork MariaDB as first-class backends:
      every natively supported feature is reverse-engineered, diffed, and emitted
      as correct DDL.'
    image: https://dbwarden.emiliano-go.com/assets/images/og-image.png
    image:alt: DBWarden documentation
    site: '@emiliano_go_'
  description: 'DBWarden treats MySQL and its fork MariaDB as first-class backends:
    every natively supported feature is reverse-engineered, diffed, and emitted as
    correct DDL.'
  schema_jsonld:
  - '@context': https://schema.org
    '@type': WebPage
    name: MySQL & MariaDB - DBWarden Documentation
    url: https://dbwarden.emiliano-go.com/databases/mysql
    description: 'DBWarden treats MySQL and its fork MariaDB as first-class backends:
      every natively supported feature is reverse-engineered, diffed, and emitted
      as correct DDL.'
    image: https://dbwarden.emiliano-go.com/assets/images/og-image.png
    publisher:
      '@type': Organization
      name: Emiliano Gandini Outeda
      logo: https://dbwarden.emiliano-go.com/assets/images/og-image.png
  - '@context': https://schema.org
    '@type': BreadcrumbList
    itemListElement:
    - '@type': ListItem
      position: 1
      name: Databases
      item: https://dbwarden.emiliano-go.com/databases
    - '@type': ListItem
      position: 2
      name: MySQL & MariaDB
      item: https://dbwarden.emiliano-go.com/databases/mysql
seo_html: "<title>MySQL &amp; MariaDB - DBWarden Documentation</title>\n<meta name=\"\
  description\" content=\"DBWarden treats MySQL and its fork MariaDB as first-class\
  \ backends: every natively supported feature is reverse-engineered, diffed, and\
  \ emitted as correct DDL.\">\n<link rel=\"canonical\" href=\"https://dbwarden.emiliano-go.com/databases/mysql\"\
  >\n<meta name=\"robots\" content=\"index,follow\">\n<meta property=\"og:type\" content=\"\
  website\">\n<meta property=\"og:title\" content=\"MySQL &amp; MariaDB - DBWarden\
  \ Documentation\">\n<meta property=\"og:description\" content=\"DBWarden treats\
  \ MySQL and its fork MariaDB as first-class backends: every natively supported feature\
  \ is reverse-engineered, diffed, and emitted as correct DDL.\">\n<meta property=\"\
  og:url\" content=\"https://dbwarden.emiliano-go.com/databases/mysql\">\n<meta property=\"\
  og:image\" content=\"https://dbwarden.emiliano-go.com/assets/images/og-image.png\"\
  >\n<meta property=\"og:image:width\" content=\"1376\">\n<meta property=\"og:image:height\"\
  \ content=\"768\">\n<meta property=\"og:image:alt\" content=\"DBWarden documentation\"\
  >\n<meta property=\"og:site_name\" content=\"DBWarden Documentation\">\n<meta property=\"\
  og:locale\" content=\"en_US\">\n<meta name=\"twitter:card\" content=\"summary_large_image\"\
  >\n<meta name=\"twitter:title\" content=\"MySQL &amp; MariaDB - DBWarden Documentation\"\
  >\n<meta name=\"twitter:description\" content=\"DBWarden treats MySQL and its fork\
  \ MariaDB as first-class backends: every natively supported feature is reverse-engineered,\
  \ diffed, and emitted as correct DDL.\">\n<meta name=\"twitter:image\" content=\"\
  https://dbwarden.emiliano-go.com/assets/images/og-image.png\">\n<meta name=\"twitter:image:alt\"\
  \ content=\"DBWarden documentation\">\n<meta name=\"twitter:site\" content=\"@emiliano_go_\"\
  >\n<script type=\"application/ld+json\">\n[\n  {\n    \"@context\": \"https://schema.org\"\
  ,\n    \"@type\": \"WebPage\",\n    \"name\": \"MySQL & MariaDB - DBWarden Documentation\"\
  ,\n    \"url\": \"https://dbwarden.emiliano-go.com/databases/mysql\",\n    \"description\"\
  : \"DBWarden treats MySQL and its fork MariaDB as first-class backends: every natively\
  \ supported feature is reverse-engineered, diffed, and emitted as correct DDL.\"\
  ,\n    \"image\": \"https://dbwarden.emiliano-go.com/assets/images/og-image.png\"\
  ,\n    \"publisher\": {\n      \"@type\": \"Organization\",\n      \"name\": \"\
  Emiliano Gandini Outeda\",\n      \"logo\": \"https://dbwarden.emiliano-go.com/assets/images/og-image.png\"\
  \n    }\n  },\n  {\n    \"@context\": \"https://schema.org\",\n    \"@type\": \"\
  BreadcrumbList\",\n    \"itemListElement\": [\n      {\n        \"@type\": \"ListItem\"\
  ,\n        \"position\": 1,\n        \"name\": \"Databases\",\n        \"item\"\
  : \"https://dbwarden.emiliano-go.com/databases\"\n      },\n      {\n        \"\
  @type\": \"ListItem\",\n        \"position\": 2,\n        \"name\": \"MySQL & MariaDB\"\
  ,\n        \"item\": \"https://dbwarden.emiliano-go.com/databases/mysql\"\n    \
  \  }\n    ]\n  }\n]\n</script>\n"
---

# MySQL & MariaDB

DBWarden treats MySQL (and its fork MariaDB) as **first-class backends**: every natively supported feature is reverse-engineered, diffed, and emitted as correct DDL.

## First-Class Features

"First-class" means the round-trip is verified: reverse-engineer a live database with `generate-models`, feed the output back into `make-migrations`, and get **zero diff**.

```bash
# Step 1: reverse-engineer your live MySQL/MariaDB database
$ dbwarden generate-models -d primary

# Step 2: feed the generated models back in, zero diff
$ dbwarden make-migrations -d primary
# -> "No new migrations to generate"  (output is empty; your models match the DB exactly)
```

The following MySQL/MariaDB features are fully supported in this round-trip:

| Category | Features |
|----------|----------|
| Engine | `ENGINE=InnoDB`, `MyISAM`, etc. via `my_engine` |
| Charset & Collation | Table: `DEFAULT CHARACTER SET` / `COLLATE` via `my_charset`, `my_collate`. Column: per-column `CHARACTER SET` / `COLLATE` via `my.field(charset=..., collate=...)` |
| Row Format | `ROW_FORMAT=DYNAMIC`, `COMPACT`, `COMPRESSED`, `REDUNDANT` via `my_row_format` |
| Auto Increment | Table-level `AUTO_INCREMENT=N` via `my_auto_increment`. Column-level toggle via `autoincrement` field |
| Unsigned | `UNSIGNED` on integer columns via `my.field(unsigned=True)` |
| ON UPDATE | `ON UPDATE CURRENT_TIMESTAMP` via `my.field(on_update="CURRENT_TIMESTAMP")` |
| Comments | Table: `ALTER TABLE t COMMENT = '...'`. Column: `MODIFY COLUMN ... COMMENT '...'` (full column definition preserved) |
| Foreign Keys | `ON DELETE` / `ON UPDATE` options; DROP uses `DROP FOREIGN KEY` (MySQL syntax) |
| Indexes | Full index support; `USING BTREE / HASH` preserved |
| Auto-increment Lifecycle | Toggle autoincrement on integer PKs via `autoincrement` field: generates `MODIFY COLUMN ... AUTO_INCREMENT` |
| Type Normalization | `TINYINT(1)` -> `BOOLEAN`, `INT`, `BIGINT`, `VARCHAR(n)`, `TEXT`, `DATETIME`, `TIMESTAMP`, `YEAR`, `DECIMAL(p,s)`, `FLOAT`, `DOUBLE`, `BLOB`, `JSON`, `ENUM`, `SET` |

### MariaDB-Specific Features

| Category | Features |
|----------|----------|
| Page Compression | `PAGE_COMPRESSED=1` / `PAGE_COMPRESSION_LEVEL=N` via `mdb_page_compressed`, `mdb_page_compression_level` on `MdbTableMeta` |
| Invisible Columns | Column invisibility via `mdb.field(invisible=True)` on `MdbColumnMeta` |
| Sequences | `CREATE SEQUENCE` support via `mdb.field(sequence=...)` |

## Installation

Install with the MySQL driver:

```bash
uv add "dbwarden[mysql]"
```

Or with uv:

```bash
uv add "dbwarden[mysql]"
```

## Configuration

The MySQL backend is enabled by setting `database_type="mysql"` (or `database_type="mariadb"`) in your dbwarden config:

```python
from dbwarden import database_config

database_config(
    database_name="primary",
    default=True,
    database_type="mysql",
    database_url_sync="mysql+pymysql://user:password@localhost:3306/mydb",
)
```

The connection URL uses the `mysql+pymysql://` scheme (the `pymysql` driver is included via the `[mysql]` extra). You can also use any SQLAlchemy-compatible MySQL driver such as `mysql+mysqlconnector://`.

## Declaring Metadata

MySQL/MariaDB metadata is declared in a `class Meta` inner class on the model. This is the **only** supported surface: `mapped_column(info=...)` raises `DBWardenConfigError`.

### Table-Level Meta

Inherit from `MyTableMeta` on your `class Meta`:

```python
from sqlalchemy import Integer, String
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from dbwarden.databases.mysql import MyTableMeta

class Base(DeclarativeBase):
    pass

class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email: Mapped[str] = mapped_column(String(255))

    class Meta(MyTableMeta):
        my_engine = "InnoDB"
        my_charset = "utf8mb4"
        my_collate = "utf8mb4_unicode_ci"
        my_row_format = "DYNAMIC"
        my_auto_increment = 1000
        comment = "Core user accounts"
```

`MyTableMeta` inherits from `TableMeta`, which provides common attributes shared across all backends:

| Attribute | Type | SQL |
|-----------|------|-----|
| `comment` | `str` | `ALTER TABLE t COMMENT = '...'` |
| `indexes` | `list[dict]` | `CREATE INDEX ...` |
| `checks` | `list[dict]` | `ALTER TABLE t ADD CONSTRAINT ... CHECK (...)` |
| `uniques` | `list[dict]` | `ALTER TABLE t ADD CONSTRAINT ... UNIQUE (...)` |

MySQL-specific `MyTableMeta` attributes:

| Attribute | Type | SQL |
|-----------|------|-----|
| `my_engine` | `str` | `ALTER TABLE t ENGINE = name` |
| `my_charset` | `str` | `ALTER TABLE t DEFAULT CHARACTER SET name` |
| `my_collate` | `str` | `ALTER TABLE t COLLATE = name` |
| `my_row_format` | `str` | `ALTER TABLE t ROW_FORMAT = name` |
| `my_auto_increment` | `int` | `ALTER TABLE t AUTO_INCREMENT = N` |

For MariaDB, use `MdbTableMeta`:

```python
from dbwarden.databases.mariadb import MdbTableMeta

class Meta(MdbTableMeta):
    my_engine = "InnoDB"
    mdb_page_compressed = True
    mdb_page_compression_level = 3
```

MariaDB-specific `MdbTableMeta` attributes (in addition to all `MyTableMeta` attributes):

| Attribute | Type | SQL |
|-----------|------|-----|
| `mdb_page_compressed` | `bool` | `PAGE_COMPRESSED=1` |
| `mdb_page_compression_level` | `int` | `PAGE_COMPRESSION_LEVEL=N` |

### Column-Level Meta

Use `MyColumnMeta` inner classes for per-column metadata. The inner class must be named after the column. Use `my = my.field(...)` to set column-level options:

```python
from sqlalchemy import Integer, String, Text, TIMESTAMP
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from dbwarden.databases.mysql import MyTableMeta, MyColumnMeta, my

class Base(DeclarativeBase):
    pass

class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email: Mapped[str] = mapped_column(String(255))
    bio: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[str] = mapped_column(TIMESTAMP)

    class Meta(MyTableMeta):
        class id(MyColumnMeta):
            comment = "Primary key"
            my = my.field(unsigned=True)

        class email(MyColumnMeta):
            my = my.field(charset="utf8mb4", collate="utf8mb4_unicode_ci")

        class updated_at(MyColumnMeta):
            my = my.field(on_update="CURRENT_TIMESTAMP")
```

`MyColumnMeta` includes common column attributes shared across all backends:

| Attribute | Type | SQL |
|-----------|------|-----|
| `comment` | `str` | `MODIFY COLUMN ... COMMENT '...'` |
| `public` | `bool` | Controls field visibility in `@auto_schema` (requires `dbwarden-fastapi` plugin) |
| `my` | `MyFieldSpec` | MySQL-specific column options (see table below) |

MySQL-specific `MyFieldSpec` fields (set via `my.field(...)`):

| Keyword | Type | SQL |
|---------|------|-----|
| `unsigned` | `bool` | `UNSIGNED` on integer columns |
| `charset` | `str` | `CHARACTER SET name` (per-column charset) |
| `collate` | `str` | `COLLATE name` (per-column collation) |
| `on_update` | `str` | `ON UPDATE CURRENT_TIMESTAMP` (typically on TIMESTAMP columns) |

For MariaDB, use `MdbColumnMeta` and `mdb.field(...)`:

```python
from dbwarden.databases.mariadb import MdbColumnMeta
from dbwarden.databases.mariadb import mdb

class Meta(MdbTableMeta):
    class id(MdbColumnMeta):
        mdb = mdb.field(invisible=True)
```

MariaDB-specific `MdbFieldSpec` fields (set via `mdb.field(...)`):

| Keyword | Type | SQL |
|---------|------|-----|
| `invisible` | `bool` | `ALTER TABLE ... ALTER COLUMN c SET INVISIBLE` |
| `sequence` | `str` | Sequence name for MariaDB sequence support |
| `unsigned` | `bool` | `UNSIGNED` on integer columns |
| `charset` | `str` | `CHARACTER SET name` |
| `collate` | `str` | `COLLATE name` |
| `on_update` | `str` | `ON UPDATE CURRENT_TIMESTAMP` |

### Foreign Key Options

Foreign key options (`ondelete`, `onupdate`) are captured from the database by `generate-models` and emitted in the `ForeignKey` constructor:

```python
from sqlalchemy import ForeignKey
from sqlalchemy.orm import Mapped, mapped_column

class OrderItem(Base):
    __tablename__ = "order_items"

    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id", ondelete="CASCADE"), nullable=False)
```

### Model Example (Generated)

Here is the complete generated model output for a MySQL table with engine, charset, unsigned PK, ON UPDATE, and per-column charset:

```python
from sqlalchemy import BigInteger, Column, Integer, String, TIMESTAMP, Text, text
from sqlalchemy.orm import DeclarativeBase

Base = declarative_base()

from dbwarden.databases.mysql import MyColumnMeta, MyTableMeta, my

class User(Base):
    __tablename__ = 'users'

    id = Column('id', Integer, primary_key=True, nullable=False)
    email = Column('email', String(255), nullable=False)
    bio = Column('bio', Text)
    updated_at = Column('updated_at', TIMESTAMP, nullable=False, server_default=text('CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP'))

    class Meta(MyTableMeta):
        my_engine = 'InnoDB'
        my_charset = 'utf8mb4'
        my_collate = 'utf8mb4_unicode_ci'
        my_row_format = 'DYNAMIC'
        comment = 'Core user accounts'

        class id(MyColumnMeta):
            comment = 'Primary key'
            my = my.field(unsigned=True)

        class email(MyColumnMeta):
            my = my.field(charset='utf8mb4', collate='utf8mb4_unicode_ci')

        class updated_at(MyColumnMeta):
            my = my.field(on_update='CURRENT_TIMESTAMP')
```

## DDL Behavior

### DDL Is NOT Transactional

MySQL and MariaDB DDL is **non-transactional**: each DDL statement implicitly commits the current transaction. If a migration file contains multiple statements and one fails, the prior DDL cannot be rolled back. This makes MySQL/MariaDB more fragile than PostgreSQL for automated migration runs.

### Column Type Changes

Emits `ALTER TABLE t MODIFY COLUMN c newtype`. Unlike PostgreSQL, MySQL requires the full column definition on every `MODIFY COLUMN`. DBWarden handles this by re-emitting all column attributes (type, unsigned, nullable, default, comment, charset, collate, auto_increment) in a single statement:

```sql
ALTER TABLE users MODIFY COLUMN email VARCHAR(255) NOT NULL COMMENT 'User email';
```

### Column Nullable Changes

Emits `ALTER TABLE t MODIFY COLUMN c type [NULL | NOT NULL]`, again with the full column type.

### Column Meta Changes

When MySQL-specific column metadata changes (unsigned, charset, collate, on_update), DBWarden generates a full `MODIFY COLUMN` that preserves the column's type, nullable, default, comment, and autoincrement state:

```sql
ALTER TABLE users MODIFY COLUMN id INT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT 'Primary key';
```

### Table Option Changes

MySQL table-level option changes generate individual `ALTER TABLE` statements:

| Change | Generated SQL |
|--------|---------------|
| Engine | `ALTER TABLE t ENGINE = InnoDB` |
| Charset | `ALTER TABLE t DEFAULT CHARACTER SET utf8mb4` |
| Collation | `ALTER TABLE t COLLATE = utf8mb4_unicode_ci` |
| Row Format | `ALTER TABLE t ROW_FORMAT = DYNAMIC` |
| Auto Increment | `ALTER TABLE t AUTO_INCREMENT = 1000` |

### Auto-increment Lifecycle

DBWarden supports toggling auto-increment on integer primary key columns. The `autoincrement` field in your model controls whether a column uses auto-increment:

```python
class User(Base):
    __tablename__ = "users"

    # Autoincrement enabled: same as default behavior
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    class Meta(MyTableMeta):
        class id(MyColumnMeta):
            pass  # uses default autoincrement from model
```

To explicitly disable auto-increment on a PK column:

```python
class User(Base):
    __tablename__ = "users"

    # Plain integer PK: no auto-increment
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
```

**What happens when autoincrement changes:**

| Change | Generated SQL |
|--------|---------------|
| Add autoincrement | `ALTER TABLE t MODIFY COLUMN c INT NOT NULL AUTO_INCREMENT` |
| Remove autoincrement | `ALTER TABLE t MODIFY COLUMN c INT NOT NULL` |

### Comments

Unlike PostgreSQL, MySQL has no `COMMENT ON` syntax. DBWarden generates the correct MySQL syntax:

```sql
-- Table comment
ALTER TABLE users COMMENT = 'Core user accounts';

-- Column comment (full MODIFY COLUMN preserving all attributes)
ALTER TABLE users MODIFY COLUMN email VARCHAR(255) NOT NULL COMMENT 'User email address';
```

When a comment is cleared, MySQL syntax is used:

```sql
ALTER TABLE users COMMENT = '';
ALTER TABLE users MODIFY COLUMN email VARCHAR(255) NOT NULL COMMENT '';
```

## Snapshot Format

When `database_type` is `"mysql"` or `"mariadb"`, the snapshot captures MySQL-specific metadata in `my_column` and `my_table` blocks.

### Column Extras

```json
{
  "columns": {
    "id": {
      "name": "id",
      "type": "int",
      "nullable": false,
      "default": null,
      "autoincrement": true,
      "primary_key": true,
      "comment": "Primary key",
      "my_column": {
        "my_unsigned": true,
        "my_charset": null,
        "my_collate": null,
        "my_on_update": null
      }
    },
    "updated_at": {
      "name": "updated_at",
      "type": "timestamp",
      "nullable": false,
      "default": "CURRENT_TIMESTAMP",
      "autoincrement": false,
      "primary_key": false,
      "my_column": {
        "my_unsigned": false,
        "my_on_update": "CURRENT_TIMESTAMP"
      }
    }
  }
}
```

### Table Extras

```json
{
  "my_table": {
    "my_engine": "InnoDB",
    "my_charset": "utf8mb4",
    "my_collate": "utf8mb4_unicode_ci",
    "my_row_format": "Dynamic",
    "my_auto_increment": 1000
  }
}
```

For MariaDB, additional fields appear:

```json
{
  "my_table": {
    "mdb_page_compressed": false,
    "mdb_page_compression_level": null
  }
}
```

## Reverse Engineering

`generate-models` queries `information_schema.TABLES` and `information_schema.COLUMNS` to reverse-engineer all MySQL/MariaDB metadata. The emitted model uses `class Meta` with `MyTableMeta` and `MyColumnMeta` inner classes.

```bash
$ dbwarden generate-models -d primary
```

Generated output includes automatic detection of:
- Engine, charset, collation, row format from `information_schema.TABLES`
- Column unsigned, charset, collation, on_update from `information_schema.COLUMNS`
- Foreign key options (`ON DELETE`, `ON UPDATE`)
- Auto-increment columns
- Column comments

## Safety Classification

DBWarden classifies migration changes using the `Safety` enum:

```python
from dbwarden.engine.safety import Safety

assert Safety.SAFE == "SAFE"
assert Safety.INFO == "INFO"
assert Safety.WARN == "WARN"
assert Safety.CRITICAL == "CRITICAL"
```

| Change Type | Severity | Flag Required |
|-------------|----------|---------------|
| Add column | `INFO` | None |
| Drop column | `WARNING` | `--force` |
| Change column type | `WARNING` | `--force` |
| Change column nullable | `WARNING` | `--force` |
| Change column comment | `INFO` | None |
| Change MySQL column meta | `WARNING` | `--force` |
| Change engine | `INFO` | None |
| Change charset | `INFO` | None |
| Change collation | `INFO` | None |
| Change row format | `INFO` | None |
| Change auto_increment | `INFO` | None |
| Change table comment | `INFO` | None |
| Add / drop index | `INFO` / `WARNING` | `--force` |
| Add / drop FK | `INFO` / `WARNING` | `--force` |
