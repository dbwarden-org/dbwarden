from __future__ import annotations

import typing
from typing import Any

from dbwarden.exceptions import DBWardenConfigError
from dbwarden.schema._base import DBWardenMeta, attach_meta

_FLAT_BACKEND_PREFIXES = ("pg_", "ch_", "my_", "mdb_", "sq_")

_LIST_FIELDS = {
    "indexes",
    "checks",
    "uniques",
    "primary_key",
    "pg_indexes",
    "pg_checks",
    "pg_uniques",
    "pg_excludes",
    "pg_policies",
    "pg_grants",
    "ch_indexes",
    "my_indexes",
    "my_checks",
    "my_uniques",
    "sq_indexes",
    "pg_inherits",
}

_KNOWN_FIELD_ATTRS = frozenset({
    "comment", "public",
    "pg", "ch", "my", "mdb", "sq",
})

_KNOWN_TABLE_ATTRS = frozenset({
    "comment", "indexes", "checks", "uniques", "partition", "primary_key",
    "pg_tablespace", "pg_fillfactor", "pg_unlogged", "pg_inherits",
    "pg_partition_of", "pg_partition_bound",
    "pg_indexes", "pg_checks", "pg_uniques", "pg_excludes", "pg_partition",
    "pg_rls", "pg_policies", "pg_grants",
    "pg_view_query", "pg_view_materialized", "pg_schema",
    "ch_engine", "ch_order_by", "ch_primary_key", "ch_partition_by",
    "ch_sample_by", "ch_ttl", "ch_settings", "ch_zookeeper_path",
    "ch_replica_name", "ch_object_type", "ch_select_statement",
    "ch_to_table", "ch_indexes", "ch_dictionary", "ch_dict_layout",
    "ch_dict_source", "ch_dict_lifetime", "ch_dict_primary_key",
    "ch_projections",
    "ch",  # ch_table() spec object
    "my_engine", "my_charset", "my_collate", "my_row_format",
    "my_auto_increment", "my_indexes", "my_checks", "my_uniques",
    "mdb_page_compressed", "mdb_page_compression_level",
    "sq_without_rowid", "sq_strict", "sq_indexes",
})


def apply_meta(cls: type) -> None:
    """Read ``class Meta`` from a mapped model and populate dbwarden metadata."""
    if getattr(cls, "__dbwarden_meta_applied__", False):
        return

    meta_chain = _collect_meta_chain(cls)
    if not meta_chain:
        return

    if hasattr(cls, "__table__"):
        for col in cls.__table__.columns:
            if col.info:
                raise DBWardenConfigError(
                    f"Column '{cls.__tablename__}.{col.name}' has non-empty .info before "
                    f"dbwarden metadata injection. Do not use mapped_column(info=...) - "
                    f"declare field metadata in class Meta instead."
                )

    backend = _get_backend_for_class(cls)
    merged_table: dict[str, Any] = {}
    merged_fields: dict[str, dict[str, Any]] = {}

    for meta_cls in reversed(meta_chain):
        _merge_meta_class(meta_cls, merged_table, merged_fields, backend=backend)

    if hasattr(cls, "__table__"):
        column_names = {c.name for c in cls.__table__.columns}
        for field_name, attrs in merged_fields.items():
            if field_name not in column_names:
                continue
            _type_check_field_attrs(field_name, attrs, cls)
            _write_column_info(cls.__table__.c[field_name], attrs)

    attach_meta(cls, _build_dbwarden_meta(merged_table))
    cls.__dbwarden_meta_applied__ = True


def _collect_meta_chain(cls: type) -> list[type]:
    chain: list[type] = []
    for klass in cls.__mro__:
        meta = klass.__dict__.get("Meta")
        if meta is not None and isinstance(meta, type):
            chain.append(meta)
    return chain


def _get_backend_for_class(cls: type) -> str:
    from dbwarden.databases.clickhouse import CHTableMeta
    from dbwarden.schema.table_meta import CHViewMeta
    from dbwarden.databases.pgsql import PGTableMeta, PGViewMeta
    from dbwarden.databases.mysql import MyTableMeta
    from dbwarden.databases.mariadb import MdbTableMeta
    from dbwarden.databases.sqlite import SqTableMeta
    from dbwarden.engine.model_discovery import _get_backend_name

    # Check the Meta class inheritance chain for backend-specific bases
    for klass in cls.__mro__:
        meta = klass.__dict__.get("Meta")
        if meta is not None and isinstance(meta, type):
            for base in meta.__mro__:
                if base is CHTableMeta or base is CHViewMeta:
                    return "clickhouse"
                if base is PGTableMeta or base is PGViewMeta:
                    return "postgresql"
                if base is MyTableMeta:
                    return "mysql"
                if base is MdbTableMeta:
                    return "mariadb"
                if base is SqTableMeta:
                    return "sqlite"

    return _get_backend_name()


def _merge_meta_class(
    meta_cls: type,
    merged_table: dict[str, Any],
    merged_fields: dict[str, dict[str, Any]],
    backend: str = "postgresql",
) -> None:
    for name, value in vars(meta_cls).items():
        if name.startswith("__"):
            continue
        if isinstance(value, type):
            field_attrs = merged_fields.setdefault(name, {})
            _reject_flat_backend_attrs(name, value)
            for attr, attr_value in vars(value).items():
                if attr.startswith("__"):
                    continue
                field_attrs[attr] = attr_value
            continue

        if callable(value):
            continue

        if name in _LIST_FIELDS and isinstance(value, list):
            merged_table[name] = list(merged_table.get(name, [])) + value
        else:
            merged_table[name] = value


def _reject_flat_backend_attrs(field_name: str, inner_cls: type) -> None:
    for attr_name in vars(inner_cls):
        if attr_name.startswith("__"):
            continue
        if isinstance(getattr(inner_cls, attr_name, None), type):
            continue
        if attr_name.startswith(_FLAT_BACKEND_PREFIXES):
            prefix = attr_name.split("_")[0]
            spec_name = {"pg": "pg", "ch": "ch", "my": "my", "mdb": "mdb", "sq": "sq"}.get(prefix, prefix)
            attr_suffix = attr_name[len(prefix) + 1:]
            raise DBWardenConfigError(
                f"Field '{field_name}': use '{spec_name} = {spec_name}.field({attr_suffix}=...)' "
                f"instead of '{attr_name} = ...'. Flat backend-specific field attrs are no longer supported."
            )


def _type_check_field_attrs(field_name: str, attrs: dict[str, Any], model_cls: type) -> None:
    for attr_name, attr_value in attrs.items():
        if attr_name in ("comment", "public"):
            continue
        if hasattr(attr_value, "to_col_info"):
            continue
        if isinstance(attr_value, type):
            continue


def _write_column_info(col, attrs: dict[str, Any]) -> None:
    for attr, value in attrs.items():
        if value is None:
            continue
        if attr == "comment":
            col.info["dw_comment"] = value
        elif attr == "public":
            col.info["dw_public"] = True if value else False
        elif hasattr(value, "to_col_info"):
            for k, v in value.to_col_info().items():
                col.info[k] = v
        elif value is False:
            continue
        else:
            col.info[attr] = value


def _to_dict(value: Any) -> Any:
    if hasattr(value, "to_dict"):
        return value.to_dict()
    return value


def _build_dbwarden_meta(table_attrs: dict[str, Any]) -> DBWardenMeta:
    from dbwarden.databases.pgsql import PgTableSpec, PgViewSpec
    from dbwarden.databases.clickhouse import (
        AggregatingViewSpec, ChTableSpec, MaterializedViewSpec,
    )
    from dbwarden.databases.mysql import MyTableSpec
    from dbwarden.databases.mariadb import MdbTableSpec
    from dbwarden.databases.sqlite import SqTableSpec

    meta = DBWardenMeta()
    meta.comment = table_attrs.get("comment")
    meta.indexes = [_to_dict(i) for i in table_attrs.get("indexes", [])]
    meta.checks = list(table_attrs.get("checks", []))
    meta.uniques = list(table_attrs.get("uniques", []))
    meta.partition = table_attrs.get("partition") or table_attrs.get("pg_partition")
    meta.pg_indexes = [_to_dict(i) for i in table_attrs.get("pg_indexes", [])]
    meta.pg_checks = list(table_attrs.get("pg_checks", []))
    meta.pg_uniques = list(table_attrs.get("pg_uniques", []))
    meta.pg_excludes = list(table_attrs.get("pg_excludes", []))
    meta.ch_indexes = [_to_dict(i) for i in table_attrs.get("ch_indexes", [])]
    meta.my_indexes = list(table_attrs.get("my_indexes", []))
    meta.my_checks = list(table_attrs.get("my_checks", []))
    meta.my_uniques = list(table_attrs.get("my_uniques", []))
    meta.primary_key = list(table_attrs.get("primary_key", []))
    meta.sq_indexes = list(table_attrs.get("sq_indexes", []))
    meta.table_attrs = dict(table_attrs)

    if isinstance(table_attrs.get("ch"), MaterializedViewSpec):
        mv: MaterializedViewSpec = table_attrs["ch"]
        meta.backend_table = mv
        meta.table_attrs.update(mv.to_dict())
    elif isinstance(table_attrs.get("ch"), AggregatingViewSpec):
        agg_spec: AggregatingViewSpec = table_attrs["ch"]
        meta.backend_table = agg_spec
        agg_dict = agg_spec.to_dict()
        meta.table_attrs["ch_agg_target"] = agg_dict["ch_agg_target"]
        meta.table_attrs["ch_agg_mv"] = agg_dict["ch_agg_mv"]
    elif any(k.startswith("pg_view_") for k in table_attrs):
        meta.backend_table = PgViewSpec(
            query=table_attrs.get("pg_view_query"),
            materialized=table_attrs.get("pg_view_materialized", False),
            schema=table_attrs.get("pg_schema") or None,
            auto_refresh=table_attrs.get("pg_view_auto_refresh", False),
        )
    elif any(k.startswith("pg_") and k not in ("pg_indexes", "pg_checks", "pg_uniques", "pg_excludes", "pg_partition", "pg_view_query", "pg_view_materialized") for k in table_attrs):
        pg_inherits = table_attrs.get("pg_inherits")
        if isinstance(pg_inherits, str):
            pg_inherits = [pg_inherits]
        meta.backend_table = PgTableSpec(
            tablespace=table_attrs.get("pg_tablespace"),
            fillfactor=table_attrs.get("pg_fillfactor"),
            unlogged=table_attrs.get("pg_unlogged", False),
            inherits=list(pg_inherits) if pg_inherits else None,
            schema=table_attrs.get("pg_schema") or None,
            partition=table_attrs.get("pg_partition"),
        )
    elif isinstance(table_attrs.get("ch"), ChTableSpec):
        ts: ChTableSpec = table_attrs["ch"]
        meta.backend_table = ts
        loose_ch_present = any(k.startswith("ch_") for k in table_attrs if k != "ch")
        if loose_ch_present:
            table_attrs["_ch_from_loose"] = True
            meta.table_attrs["_ch_from_loose"] = True
        if ts.indexes:
            meta.ch_indexes = [i.to_dict() if hasattr(i, "to_dict") else i for i in ts.indexes]
        if ts.projections:
            proj_dicts = [p.to_dict() if hasattr(p, "to_dict") else p for p in ts.projections]
            meta.table_attrs["ch_projections"] = proj_dicts
    elif any(k.startswith("ch_") and k not in ("ch_indexes", "ch_projections", "ch_settings", "ch_dict_layout", "ch_dict_source", "ch_dict_lifetime", "ch_dict_primary_key") for k in table_attrs):
        table_attrs["_ch_from_loose"] = True
        meta.table_attrs["_ch_from_loose"] = True
        is_loose_mv = bool(table_attrs.get("ch_select_statement"))
        if is_loose_mv:
            meta.backend_table = MaterializedViewSpec(
                name=table_attrs.get("ch_name"),
                select=table_attrs.get("ch_select_statement"),
                to=table_attrs.get("ch_to_table"),
                engine=table_attrs.get("ch_engine"),
                order_by=table_attrs.get("ch_order_by"),
                partition_by=table_attrs.get("ch_partition_by"),
                ttl=table_attrs.get("ch_ttl"),
                settings=dict(table_attrs.get("ch_settings", {})) if table_attrs.get("ch_settings") else None,
            )
        else:
            meta.backend_table = ChTableSpec(
                engine=table_attrs.get("ch_engine", "MergeTree"),
                order_by=list(table_attrs.get("ch_order_by", [])) if table_attrs.get("ch_order_by") else None,
                primary_key=table_attrs.get("ch_primary_key"),
                partition_by=table_attrs.get("ch_partition_by"),
                sample_by=table_attrs.get("ch_sample_by"),
                ttl=table_attrs.get("ch_ttl"),
                settings=dict(table_attrs.get("ch_settings", {})) if table_attrs.get("ch_settings") else None,
                zookeeper_path=table_attrs.get("ch_zookeeper_path"),
                replica_name=table_attrs.get("ch_replica_name"),
            )
    elif any(k.startswith("mdb_") and k not in ("mdb_page_compressed", "mdb_page_compression_level") or k.startswith("my_") and k not in ("my_indexes", "my_checks", "my_uniques") for k in table_attrs):
        is_mariadb = any(k.startswith("mdb_") for k in table_attrs)
        cls = MdbTableSpec if is_mariadb else MyTableSpec
        meta.backend_table = cls(
            engine=table_attrs.get("my_engine", "InnoDB"),
            charset=table_attrs.get("my_charset", "utf8mb4"),
            collate=table_attrs.get("my_collate", "utf8mb4_unicode_ci"),
            row_format=table_attrs.get("my_row_format"),
            auto_increment=table_attrs.get("my_auto_increment"),
            **({
                "page_compressed": table_attrs.get("mdb_page_compressed", False),
                "page_compression_level": table_attrs.get("mdb_page_compression_level"),
            } if is_mariadb else {}),
        )
    elif any(k.startswith("sq_") and k not in ("sq_indexes",) for k in table_attrs):
        meta.backend_table = SqTableSpec(
            without_rowid=table_attrs.get("sq_without_rowid", False),
            strict=table_attrs.get("sq_strict", False),
        )

    return meta
