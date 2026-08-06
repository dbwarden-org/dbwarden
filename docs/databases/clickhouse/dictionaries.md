# Dictionaries

## Declaration

Dictionaries are declared with flat `ch_dict_*` attributes on `class Meta`, not with builder functions. Set `ch_dictionary = True` to mark the model as a dictionary:

```python
from sqlalchemy.orm import Mapped, mapped_column
from dbwarden.databases.clickhouse import CHTableMeta

class CountryLookup(Base):
    __tablename__ = "country_lookup"

    code: Mapped[str] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column()

    class Meta(CHTableMeta):
        ch_dictionary = True
        ch_dict_primary_key = "code"
        ch_dict_layout = "flat"
        ch_dict_source = {
            "clickhouse": {"query": "SELECT code, name FROM source_countries"},
        }
        ch_dict_lifetime = 300
```

The five recognised attributes are `ch_dictionary`, `ch_dict_primary_key`, `ch_dict_layout`, `ch_dict_source`, and `ch_dict_lifetime`. Because `CHTableMeta` is validated at import time, a misspelled attribute raises `DBWardenConfigError` when the module loads.

There is also a `dictionary()` helper that builds a `DictSpec` directly, for code that constructs specs programmatically rather than declaring them on a model:

```python
from dbwarden.databases.clickhouse import dictionary

spec = dictionary(
    layout="hashed",
    source={"clickhouse": {"table": "dim_users"}},
    lifetime=300,
    primary_key="id",
)
```

## Additional model examples

### MySQL-sourced dictionary

```python
class MySQLCountry(Base):
    __tablename__ = "mysql_country"

    code: Mapped[str] = mapped_column()
    name: Mapped[str] = mapped_column()

    class Meta(CHTableMeta):
        ch_dictionary = True
        ch_dict_primary_key = "code"
        ch_dict_layout = "hashed"
        ch_dict_source = {
            "mysql": {
                "named_collection": "mysql_dict",
                "query": "SELECT iso_code, full_name FROM ref.countries",
            },
        }
        ch_dict_lifetime = "MIN 60 MAX 300"
```

### HTTP-sourced dictionary with complex key

```python
class CurrencyRate(Base):
    __tablename__ = "currency_rate"

    currency: Mapped[str] = mapped_column()
    rate: Mapped[float] = mapped_column()

    class Meta(CHTableMeta):
        ch_dictionary = True
        ch_dict_primary_key = "currency"
        ch_dict_layout = "cache"
        ch_dict_source = {
            "http": {
                "url": "https://api.example.com/rates",
                "format": "JSONEachRow",
            },
        }
        ch_dict_lifetime = 3600
```

### Range-hashed dictionary for time-based lookup

```python
class TaxRate(Base):
    __tablename__ = "tax_rate"

    region: Mapped[str] = mapped_column()
    rate: Mapped[float] = mapped_column()

    class Meta(CHTableMeta):
        ch_dictionary = True
        ch_dict_primary_key = ["region", "valid_from"]
        ch_dict_layout = "range_hashed"
        ch_dict_source = {
            "clickhouse": {
                "query": "SELECT region, valid_from, valid_to, rate FROM ref.tax_rates",
            },
        }
        ch_dict_lifetime = 86400
```

Usage in queries:

```sql
SELECT dictGet('tax_rate', 'rate', ('CA', today()))
```

## Source types

`ch_dict_source` is a dict keyed by source type, whose value carries that source's settings:

| Source type | `ch_dict_source` |
|-------------|------------------|
| ClickHouse | `{"clickhouse": {"query": "..."}}` |
| MySQL | `{"mysql": {...}}` |
| PostgreSQL | `{"postgresql": {...}}` |
| MongoDB | `{"mongodb": {...}}` |
| HTTP(S) | `{"http": {...}}` |
| Local file | `{"file": {...}}` |
| Executable | `{"executable": {...}}` |

For connection secrets, reference a named collection rather than inlining credentials:

```python
ch_dict_source = {"clickhouse": {"named_collection": "clickhouse_dict_source"}}
```

## Layout types

`ch_dict_layout` is the layout name as a string:

```python
ch_dict_layout = "flat"                # One key, single value
ch_dict_layout = "hashed"              # Hash table, all in memory
ch_dict_layout = "sparse_hashed"       # Like hashed but sparse
ch_dict_layout = "cache"               # LRU cache
ch_dict_layout = "complex_key_hashed"  # Composite keys
ch_dict_layout = "ip_trie"             # IP prefix matching
ch_dict_layout = "direct"              # No caching
ch_dict_layout = "range_hashed"        # Time ranges
```

## Lifetime

`ch_dict_lifetime` accepts an integer for a fixed interval, or a string for ClickHouse's ranged form:

```python
ch_dict_lifetime = 300              # Fixed interval, in seconds
ch_dict_lifetime = "MIN 300 MAX 600"  # Ranged
```

## What changes are allowed

| Change | Safety |
|--------|--------|
| Lifetime adjustment | INFO |
| Layout change | CRITICAL: requires recreate |
| Source connection change | INFO (named collection swap) |
| Query/SELECT change | WARN |
| Primary key change | CRITICAL: requires recreate |

## Rollback behavior

Dictionary changes that require a recreate follow the full pipeline. See [Safety](safety.md).
