from __future__ import annotations

import pytest
from sqlalchemy import Integer, String
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

import dbwarden.engine.model_discovery as model_discovery
from dbwarden.databases.pgsql import (
    PGColumnMeta,
    PGTableMeta,
    PGViewMeta,
    PgFieldSpec,
    PgIndexSpec,
    PgTableSpec,
    PgViewSpec,
    exclude,
    field,
    index,
    partition_by_range,
    partition_by_list,
    partition_by_hash,
)
from dbwarden.databases import check, unique
from dbwarden.schema._base import read_meta
from dbwarden.schema._meta_reader import apply_meta
from dbwarden.engine.model_discovery import extract_table_from_model
from dbwarden.schema.constraint import CheckSpec, UniqueSpec
from dbwarden.databases.pgsql.constraint import ExcludeSpec

_tablename_counter = 0


def _next_tn(prefix: str) -> str:
    global _tablename_counter
    _tablename_counter += 1
    return f"doc_{prefix}_{_tablename_counter}"


class TestPGTableMeta:
    """PGTableMeta with fillfactor, tablespace, schema, storage_params, inherits."""

    def test_basic_table_meta_roundtrip(self):
        class Base(DeclarativeBase):
            pass

        tablename = _next_tn("user")

        class User(Base):
            __tablename__ = tablename
            id: Mapped[int] = mapped_column(Integer, primary_key=True)
            email: Mapped[str] = mapped_column(String(255))

            class Meta(PGTableMeta):
                pg_fillfactor = 80
                pg_tablespace = "fast_ssd"
                pg_schema = "app"
                pg_storage_params = {"autovacuum_enabled": "false"}
                pg_inherits = "parent_table"

        apply_meta(User)
        meta = read_meta(User)
        assert meta is not None
        assert meta.backend_table is not None
        assert isinstance(meta.backend_table, PgTableSpec)
        assert meta.backend_table.fillfactor == 80
        assert meta.backend_table.tablespace == "fast_ssd"
        assert meta.backend_table.schema == "app"
        assert meta.backend_table.inherits == ["parent_table"]

        table = extract_table_from_model(User, db_name="primary")
        assert table is not None
        assert table.name == tablename

    def test_table_meta_unlogged(self):
        class Base(DeclarativeBase):
            pass

        tablename = _next_tn("unlogged")

        class Log(Base):
            __tablename__ = tablename
            id: Mapped[int] = mapped_column(Integer, primary_key=True)

            class Meta(PGTableMeta):
                pg_unlogged = True

        apply_meta(Log)
        meta = read_meta(Log)
        assert meta is not None
        assert meta.backend_table is not None
        assert isinstance(meta.backend_table, PgTableSpec)
        assert meta.backend_table.unlogged is True

    def test_zero_fillfactor(self):
        """Explicit zero fillfactor should be preserved (not treated as None)."""
        class Base(DeclarativeBase):
            pass

        tablename = _next_tn("zero")

        class T(Base):
            __tablename__ = tablename
            id: Mapped[int] = mapped_column(Integer, primary_key=True)

            class Meta(PGTableMeta):
                pg_fillfactor = 0

        apply_meta(T)
        meta = read_meta(T)
        assert meta is not None
        assert meta.backend_table is not None
        assert meta.backend_table.fillfactor == 0


class TestPGColumnMetaAndField:
    """PGColumnMeta + pg.field() with collation, storage, compression."""

    def test_column_meta_with_field(self):
        class Base(DeclarativeBase):
            pass

        tablename = _next_tn("user")

        class User(Base):
            __tablename__ = tablename
            id: Mapped[int] = mapped_column(Integer, primary_key=True)
            email: Mapped[str] = mapped_column(String(255))
            bio: Mapped[str | None] = mapped_column(String(500))

            class Meta(PGTableMeta):
                class email(PGColumnMeta):
                    comment = "Primary contact email"
                    pg = field(collation="en_US.UTF-8", storage="PLAIN")

                class bio(PGColumnMeta):
                    comment = "User biography"
                    pg = field(compression="pglz")

        apply_meta(User)
        email_info = User.__table__.c.email.info
        bio_info = User.__table__.c.bio.info

        assert email_info["dw_comment"] == "Primary contact email"
        assert email_info["pg_collation"] == "en_US.UTF-8"
        assert email_info["pg_storage"] == "PLAIN"
        assert bio_info["dw_comment"] == "User biography"
        assert bio_info["pg_compression"] == "pglz"

    def test_column_meta_identity(self):
        class Base(DeclarativeBase):
            pass

        tablename = _next_tn("identity")

        class User(Base):
            __tablename__ = tablename
            id: Mapped[int] = mapped_column(Integer, primary_key=True)

            class Meta(PGTableMeta):
                class id(PGColumnMeta):
                    pg = field(
                        identity="always",
                        identity_start=1000,
                        identity_increment=1,
                    )

        apply_meta(User)
        id_info = User.__table__.c.id.info
        assert id_info["pg_identity"] == "always"
        assert id_info["pg_identity_start"] == 1000
        assert id_info["pg_identity_increment"] == 1

    def test_field_spec_to_col_info(self):
        spec = field(collation="C", storage="MAIN", compression="pglz")
        info = spec.to_col_info()
        assert info["pg_collation"] == "C"
        assert info["pg_storage"] == "MAIN"
        assert info["pg_compression"] == "pglz"

    def test_field_spec_defaults(self):
        spec = field()
        assert spec.collation is None
        assert spec.storage is None
        assert spec.compression is None
        info = spec.to_col_info()
        assert info == {}


class TestPGViewMeta:
    """PGViewMeta with query, materialized, auto_refresh."""

    def test_view_meta_roundtrip(self):
        class Base(DeclarativeBase):
            pass

        tablename = _next_tn("active")

        class ActiveUsers(Base):
            __tablename__ = tablename
            id: Mapped[int] = mapped_column(Integer, primary_key=True)
            name: Mapped[str] = mapped_column(String(100))

            class Meta(PGViewMeta):
                pg_view_query = "SELECT id, name FROM users WHERE active = true"
                pg_view_materialized = False

        apply_meta(ActiveUsers)
        meta = read_meta(ActiveUsers)
        assert meta is not None
        assert meta.backend_table is not None
        assert isinstance(meta.backend_table, PgViewSpec)
        assert meta.backend_table.query == "SELECT id, name FROM users WHERE active = true"
        assert meta.backend_table.materialized is False
        assert meta.backend_table.auto_refresh is False

    def test_materialized_view_meta(self):
        class Base(DeclarativeBase):
            pass

        tablename = _next_tn("summary")

        class UserSummary(Base):
            __tablename__ = tablename
            id: Mapped[int] = mapped_column(Integer, primary_key=True)

            class Meta(PGViewMeta):
                pg_view_query = "SELECT id, COUNT(*) as total FROM users GROUP BY id"
                pg_view_materialized = True
                pg_view_auto_refresh = True
                pg_schema = "app"

        apply_meta(UserSummary)
        meta = read_meta(UserSummary)
        assert meta is not None
        assert meta.backend_table is not None
        assert isinstance(meta.backend_table, PgViewSpec)
        assert meta.backend_table.materialized is True
        assert meta.backend_table.auto_refresh is True
        assert meta.backend_table.schema == "app"

    def test_view_meta_empty_query(self):
        """Default PgViewSpec should have None query and non-materialized."""
        spec = PgViewSpec()
        assert spec.query is None
        assert spec.materialized is False
        assert spec.auto_refresh is False
        assert spec.schema is None


class TestPgIndexSpec:
    """PgIndexSpec to_dict/from_dict round-trip with all features."""

    def test_basic_index(self):
        spec = PgIndexSpec("ix_email", ["email"], unique=True)
        d = spec.to_dict()
        assert d["name"] == "ix_email"
        assert d["columns"] == ["email"]
        assert d["unique"] is True
        restored = PgIndexSpec.from_dict(d)
        assert restored.name == "ix_email"
        assert restored.columns == ["email"]
        assert restored.unique is True

    def test_index_with_using_where(self):
        spec = PgIndexSpec(
            "ix_active_users",
            ["email"],
            unique=True,
            using="gin",
            where="active = true",
        )
        d = spec.to_dict()
        assert d["using"] == "gin"
        assert d["where"] == "active = true"
        restored = PgIndexSpec.from_dict(d)
        assert restored.using == "gin"
        assert restored.where == "active = true"

    def test_covering_index(self):
        spec = PgIndexSpec("ix_users_covering", ["id"], include=["email", "name"])
        d = spec.to_dict()
        assert d["include"] == ["email", "name"]
        restored = PgIndexSpec.from_dict(d)
        assert restored.include == ["email", "name"]

    def test_index_with_postgresql_ops(self):
        spec = PgIndexSpec(
            "ix_users_data",
            ["data"],
            using="gin",
            postgresql_ops={"data": "jsonb_path_ops"},
        )
        d = spec.to_dict()
        assert d["postgresql_ops"] == {"data": "jsonb_path_ops"}
        restored = PgIndexSpec.from_dict(d)
        assert restored.postgresql_ops == {"data": "jsonb_path_ops"}

    def test_index_with_nulls_not_distinct(self):
        spec = PgIndexSpec("ix_unique_email", ["email"], unique=True, nulls_not_distinct=True)
        d = spec.to_dict()
        assert d["nulls_not_distinct"] is True
        restored = PgIndexSpec.from_dict(d)
        assert restored.nulls_not_distinct is True

    def test_expression_index(self):
        spec = PgIndexSpec("ix_lower_email", [], expression="lower(email)")
        d = spec.to_dict()
        assert d["expression"] == "lower(email)"
        restored = PgIndexSpec.from_dict(d)
        assert restored.expression == "lower(email)"

    def test_index_with_tablespace(self):
        spec = PgIndexSpec("ix_big", ["id"], tablespace="fast_ssd")
        d = spec.to_dict()
        assert d["tablespace"] == "fast_ssd"
        restored = PgIndexSpec.from_dict(d)
        assert restored.tablespace == "fast_ssd"

    def test_index_column_sorting(self):
        spec = PgIndexSpec("ix_sort", ["created_at"], column_sorting={"created_at": "DESC NULLS LAST"})
        d = spec.to_dict()
        assert d["column_sorting"] == {"created_at": "DESC NULLS LAST"}
        restored = PgIndexSpec.from_dict(d)
        assert restored.column_sorting == {"created_at": "DESC NULLS LAST"}

    def test_index_factory_function(self):
        d = index("ix_email", ["email"], unique=True, using="gin")
        assert d["name"] == "ix_email"
        assert d["columns"] == ["email"]
        assert d["unique"] is True
        assert d["using"] == "gin"

    def test_index_in_meta_extraction(self):
        class Base(DeclarativeBase):
            pass

        tablename = _next_tn("indexed")

        class User(Base):
            __tablename__ = tablename
            id: Mapped[int] = mapped_column(Integer, primary_key=True)
            email: Mapped[str] = mapped_column(String(255))
            data: Mapped[str] = mapped_column(String(500))

            class Meta(PGTableMeta):
                pg_indexes = [
                    PgIndexSpec("ix_email", ["email"], unique=True),
                    PgIndexSpec("ix_data", ["data"], using="gin",
                                postgresql_ops={"data": "jsonb_path_ops"}),
                ]

        apply_meta(User)
        meta = read_meta(User)
        assert meta is not None
        assert len(meta.pg_indexes) == 2
        assert meta.pg_indexes[0]["name"] == "ix_email"
        assert meta.pg_indexes[0]["unique"] is True
        assert meta.pg_indexes[1]["name"] == "ix_data"
        assert meta.pg_indexes[1]["using"] == "gin"
        assert meta.pg_indexes[1]["postgresql_ops"] == {"data": "jsonb_path_ops"}


class TestPGConstraints:
    """Check, Unique, Exclude specs."""

    def test_check_spec_roundtrip(self):
        spec = CheckSpec(expression="age >= 0", name="ck_age", no_inherit=True)
        assert spec.expression == "age >= 0"
        assert spec.name == "ck_age"
        assert spec.no_inherit is True

    def test_check_factory(self):
        d = check("ck_age", "age >= 0", no_inherit=True)
        assert d["name"] == "ck_age"
        assert d["expression"] == "age >= 0"
        assert d["no_inherit"] is True

    def test_check_in_meta(self):
        class Base(DeclarativeBase):
            pass

        tablename = _next_tn("checked")

        class User(Base):
            __tablename__ = tablename
            id: Mapped[int] = mapped_column(Integer, primary_key=True)
            age: Mapped[int] = mapped_column(Integer)

            class Meta(PGTableMeta):
                pg_checks = [
                    {"name": "ck_age", "expression": "age >= 0"},
                    check("ck_age_max", "age <= 150"),
                ]

        apply_meta(User)
        meta = read_meta(User)
        assert meta is not None
        assert len(meta.pg_checks) == 2

    def test_unique_spec_roundtrip(self):
        spec = UniqueSpec(
            columns=["email"],
            name="uq_email",
            nulls_not_distinct=True,
            deferrable=True,
            initially_deferred=True,
            include=["id"],
        )
        assert spec.columns == ["email"]
        assert spec.nulls_not_distinct is True
        assert spec.deferrable is True
        assert spec.initially_deferred is True
        assert spec.include == ["id"]

    def test_unique_factory(self):
        d = unique("uq_email", ["email"], nulls_not_distinct=True)
        assert d["name"] == "uq_email"
        assert d["columns"] == ["email"]
        assert d["nulls_not_distinct"] is True

    def test_unique_in_meta(self):
        class Base(DeclarativeBase):
            pass

        tablename = _next_tn("unique")

        class User(Base):
            __tablename__ = tablename
            id: Mapped[int] = mapped_column(Integer, primary_key=True)
            email: Mapped[str] = mapped_column(String(255))

            class Meta(PGTableMeta):
                pg_uniques = [
                    unique("uq_email", ["email"]),
                ]

        apply_meta(User)
        meta = read_meta(User)
        assert meta is not None
        assert len(meta.pg_uniques) == 1
        assert meta.pg_uniques[0]["name"] == "uq_email"

    def test_exclude_spec(self):
        spec = ExcludeSpec(name="ex_clash", using="gist",
                           elements=[{"column": "period", "operator": "&&"}])
        assert spec.name == "ex_clash"
        assert spec.using == "gist"
        assert spec.elements == [{"column": "period", "operator": "&&"}]

    def test_exclude_factory(self):
        d = exclude("ex_clash", using="gist",
                    elements=[{"column": "period", "operator": "&&"}])
        assert d["name"] == "ex_clash"
        assert d["using"] == "gist"
        assert d["elements"] == [{"column": "period", "operator": "&&"}]

    def test_exclude_in_meta(self):
        class Base(DeclarativeBase):
            pass

        tablename = _next_tn("excluded")

        class Reservation(Base):
            __tablename__ = tablename
            id: Mapped[int] = mapped_column(Integer, primary_key=True)
            period: Mapped[str] = mapped_column(String(100))

            class Meta(PGTableMeta):
                pg_excludes = [
                    exclude("ex_clash", elements=[{"column": "period", "operator": "&&"}]),
                ]

        apply_meta(Reservation)
        meta = read_meta(Reservation)
        assert meta is not None
        assert len(meta.pg_excludes) == 1
        assert meta.pg_excludes[0]["name"] == "ex_clash"
        assert meta.pg_excludes[0]["using"] == "gist"
        assert meta.pg_excludes[0]["elements"] == [{"column": "period", "operator": "&&"}]

    def test_typed_exclude_spec_in_meta(self, monkeypatch):
        class Base(DeclarativeBase):
            pass

        tablename = _next_tn("excluded_typed")

        class Reservation(Base):
            __tablename__ = tablename
            id: Mapped[int] = mapped_column(Integer, primary_key=True)
            period: Mapped[str] = mapped_column(String(100))

            class Meta(PGTableMeta):
                pg_excludes = [
                    ExcludeSpec(name="ex_clash", using="gist", elements=[{"column": "period", "operator": "&&"}]),
                ]

        apply_meta(Reservation)
        meta = read_meta(Reservation)
        assert meta is not None
        assert len(meta.pg_excludes) == 1
        assert isinstance(meta.pg_excludes[0], ExcludeSpec)
        assert meta.pg_excludes[0].as_dict() == {
            "name": "ex_clash",
            "using": "gist",
            "elements": [{"column": "period", "operator": "&&"}],
        }

        monkeypatch.setattr(model_discovery.type_mapping, "_get_backend_name", lambda db_name=None: "postgresql")
        table = extract_table_from_model(Reservation)
        assert table is not None
        assert table.excludes == [
            {"name": "ex_clash", "using": "gist", "elements": [{"column": "period", "operator": "&&"}]}
        ]


class TestPGPartitionAndRLS:
    """Partition, RLS, policies in Meta."""

    def test_partition_range(self):
        d = partition_by_range("created_at", interval="1 month")
        assert d["strategy"] == "RANGE"
        assert d["columns"] == ["created_at"]
        assert d["interval"] == "1 month"

    def test_partition_list(self):
        d = partition_by_list("region")
        assert d["strategy"] == "LIST"
        assert d["columns"] == ["region"]

    def test_partition_hash(self):
        d = partition_by_hash("id", partitions=4)
        assert d["strategy"] == "HASH"
        assert d["columns"] == ["id"]
        assert d["partitions"] == 4

    def test_partition_in_meta(self):
        class Base(DeclarativeBase):
            pass

        tablename = _next_tn("partitioned")

        class Event(Base):
            __tablename__ = tablename
            id: Mapped[int] = mapped_column(Integer, primary_key=True)
            created_at: Mapped[str] = mapped_column(String(100))

            class Meta(PGTableMeta):
                pg_partition = partition_by_range("created_at", interval="1 month")
                pg_schema = "app"

        apply_meta(Event)
        meta = read_meta(Event)
        assert meta is not None
        assert meta.backend_table is not None
        assert meta.backend_table.partition is not None
        assert meta.backend_table.partition["strategy"] == "RANGE"
        assert meta.backend_table.partition["columns"] == ["created_at"]

    def test_rls_in_meta(self):
        class Base(DeclarativeBase):
            pass

        tablename = _next_tn("rls")

        class User(Base):
            __tablename__ = tablename
            id: Mapped[int] = mapped_column(Integer, primary_key=True)
            tenant: Mapped[str] = mapped_column(String(100))

            class Meta(PGTableMeta):
                pg_rls = True
                pg_rls_force = True
                pg_policies = [
                    {
                        "name": "tenant_isolation",
                        "using": "tenant = current_setting('app.tenant_id')",
                    },
                ]

        apply_meta(User)
        meta = read_meta(User)
        assert meta is not None
        assert meta.table_attrs.get("pg_rls") is True
        assert meta.table_attrs.get("pg_rls_force") is True
        assert len(meta.table_attrs.get("pg_policies", [])) == 1
        assert meta.table_attrs["pg_policies"][0]["name"] == "tenant_isolation"


class TestPGAPISurface:
    """database_config() keyword and pg_extensions list."""

    def test_pg_table_spec_schema(self):
        spec = PgTableSpec(schema="app")
        assert spec.schema == "app"

    def test_pg_view_spec_schema(self):
        spec = PgViewSpec(query="SELECT 1", schema="app")
        assert spec.schema == "app"

    def test_pg_table_spec_defaults(self):
        spec = PgTableSpec()
        assert spec.tablespace is None
        assert spec.fillfactor is None
        assert spec.unlogged is False
        assert spec.inherits is None
        assert spec.schema is None
        assert spec.partition is None

    def test_pg_field_spec_defaults(self):
        spec = PgFieldSpec()
        assert spec.collation is None
        assert spec.storage is None
        assert spec.compression is None
        assert spec.generated is None
        assert spec.identity is None

    def test_pg_index_spec_defaults(self):
        spec = PgIndexSpec("ix", ["col"])
        assert spec.unique is False
        assert spec.using is None
        assert spec.where is None
        assert spec.include is None
        assert spec.nulls_not_distinct is False
        assert spec.expression is None
        assert spec.concurrently is True

    def test_exclude_spec_defaults(self):
        spec = ExcludeSpec()
        assert spec.name is None
        assert spec.using == "gist"
        assert spec.where is None
        assert spec.elements is None
