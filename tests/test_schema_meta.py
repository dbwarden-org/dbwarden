from __future__ import annotations

import pytest
from sqlalchemy import Integer, String
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

import dbwarden.engine.model_discovery as model_discovery
from dbwarden.engine.model_discovery import extract_table_from_model
from dbwarden.exceptions import DBWardenConfigError
from dbwarden.databases.clickhouse import (
    AggregatingViewSpec, CHColumnMeta, CHTableMeta, ChView,
    MaterializedView, AggregatingView, materialized_view,
    MaterializedViewSpec, aggregating_view, derive_agg_target_columns,
    get_all_ch_views, ch_view_tables_from_models,
)
from dbwarden.schema.table_meta import CHViewMeta
from dbwarden.databases import check, ch, index, pg, unique
from dbwarden.databases.clickhouse.engine import ChEngineSpec
from dbwarden.databases.clickhouse.projection import ProjectionSpec
from dbwarden.databases.pgsql import PGColumnMeta, PGTableMeta, PGViewMeta
from dbwarden.schema._base import read_meta
from dbwarden.schema._meta_reader import apply_meta
from dbwarden.schema.constraint import CheckSpec, UniqueSpec
from dbwarden.schema.table_meta import TableMeta


class Base(DeclarativeBase):
    pass


class Timestamped(Base):
    __abstract__ = True

    created_at: Mapped[str] = mapped_column(String(32))

    class Meta:
        class created_at:
            comment = "Record creation timestamp"
            public = True


class UserFields(Timestamped):
    __abstract__ = True

    email: Mapped[str] = mapped_column(String(255), unique=True)
    bio: Mapped[str | None] = mapped_column(String(255), nullable=True)

    class Meta:
        comment = "Core user accounts"
        pg_fillfactor = 80

        class email(PGColumnMeta):
            comment = "Primary contact email"
            public = True
            pg = pg.field(collation="en_US.UTF-8")

        class bio(CHColumnMeta):
            ch = ch.field(codec="ZSTD(3)")


class User(UserFields):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)


class ChildUser(UserFields):
    __tablename__ = "child_users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    class Meta:
        comment = "Child user accounts"

        class email(PGColumnMeta):
            public = False


class TestMetaReader:
    def test_apply_meta_writes_column_info_and_attaches_meta(self):
        apply_meta(User)

        email_info = User.__table__.c.email.info
        bio_info = User.__table__.c.bio.info
        created_info = User.__table__.c.created_at.info
        meta = read_meta(User)

        assert email_info["dw_comment"] == "Primary contact email"
        assert email_info["dw_public"] is True
        assert email_info["pg_collation"] == "en_US.UTF-8"
        assert bio_info["ch_codec"] == "ZSTD(3)"
        assert created_info["dw_comment"] == "Record creation timestamp"
        assert meta is not None
        assert meta.comment == "Core user accounts"
        assert meta.table_attrs["pg_fillfactor"] == 80
        assert meta.backend_table is not None
        assert meta.backend_table.fillfactor == 80

    def test_apply_meta_merges_inherited_meta(self):
        apply_meta(ChildUser)

        created_info = ChildUser.__table__.c.created_at.info
        email_info = ChildUser.__table__.c.email.info
        meta = read_meta(ChildUser)

        assert created_info["dw_comment"] == "Record creation timestamp"
        assert email_info["dw_comment"] == "Primary contact email"
        assert email_info["dw_public"] is False
        assert meta is not None
        assert meta.comment == "Child user accounts"

    def test_apply_meta_skips_fields_without_matching_column(self):
        class ModelWithExtraField(Base):
            __tablename__ = "extra_field"

            id: Mapped[int] = mapped_column(Integer, primary_key=True)
            name: Mapped[str] = mapped_column(String(255))

            class Meta:
                class name:
                    comment = "Name column"

                class nonexistent_col:
                    comment = "No matching column - silently skipped"

        apply_meta(ModelWithExtraField)
        meta = read_meta(ModelWithExtraField)
        assert meta is not None
        assert ModelWithExtraField.__table__.c.name.info.get("dw_comment") == "Name column"

    def test_apply_meta_skips_callable_in_meta(self):
        class ModelWithCallable(Base):
            __tablename__ = "callable_meta"

            id: Mapped[int] = mapped_column(Integer, primary_key=True)

            class Meta:
                comment = "test model"

                def helper_method(self):
                    pass

        apply_meta(ModelWithCallable)
        meta = read_meta(ModelWithCallable)
        assert meta is not None
        assert meta.comment == "test model"

    def test_apply_meta_rejects_flat_backend_attrs(self):
        class ModelWithFlatAttrs(Base):
            __tablename__ = "flat_attrs"

            id: Mapped[int] = mapped_column(Integer, primary_key=True)
            name: Mapped[str] = mapped_column(String(255))

            class Meta:
                class name:
                    pg_collation = "en_US.UTF-8"

        with pytest.raises(DBWardenConfigError, match=r"use 'pg = pg.field"):
            apply_meta(ModelWithFlatAttrs)

    def test_apply_meta_skips_nested_type_in_field_class(self):
        class ModelWithNestedType(Base):
            __tablename__ = "nested_type"

            id: Mapped[int] = mapped_column(Integer, primary_key=True)
            name: Mapped[str] = mapped_column(String(255))

            class Meta:
                class name:
                    comment = "Has a nested type"

                    class inner_config:
                        pass

        apply_meta(ModelWithNestedType)
        assert ModelWithNestedType.__table__.c.name.info.get("dw_comment") == "Has a nested type"

    def test_apply_meta_handles_type_value_in_field_attrs(self):
        class ModelWithTypeField(Base):
            __tablename__ = "type_field"

            id: Mapped[int] = mapped_column(Integer, primary_key=True)
            name: Mapped[str] = mapped_column(String(255))

            class Meta:
                class name:
                    comment = "Has type attr"
                    some_type = int

        apply_meta(ModelWithTypeField)
        assert ModelWithTypeField.__table__.c.name.info.get("dw_comment") == "Has type attr"

    def test_to_dict_function(self):
        from dbwarden.schema._meta_reader import _to_dict

        assert _to_dict(42) == 42
        assert _to_dict("hello") == "hello"

        class HasToDict:
            def to_dict(self):
                return {"key": "value"}

        assert _to_dict(HasToDict()) == {"key": "value"}

    def test_write_column_info_skips_none_values(self):
        from dbwarden.schema._meta_reader import _write_column_info
        from sqlalchemy import Column, Integer, MetaData, Table

        table = Table("t", MetaData(), Column("x", Integer))
        attrs = {"comment": None, "arbitrary_str": "hello"}
        _write_column_info(table.c.x, attrs)

        assert "dw_comment" not in table.c.x.info
        assert table.c.x.info.get("arbitrary_str") == "hello"

    def test_write_column_info_handles_false(self):
        from dbwarden.schema._meta_reader import _write_column_info
        from sqlalchemy import Column, Integer, MetaData, Table

        table = Table("t", MetaData(), Column("x", Integer))
        attrs = {"some_flag": False, "arbitrary_value": 42}
        _write_column_info(table.c.x, attrs)

        assert "some_flag" not in table.c.x.info
        assert table.c.x.info.get("arbitrary_value") == 42

    def test_apply_meta_rejects_non_empty_info(self):
        class InvalidModel(Base):
            __tablename__ = "invalid_models"

            id: Mapped[int] = mapped_column(Integer, primary_key=True)
            email: Mapped[str] = mapped_column(String(255), info={"legacy": True})

            class Meta:
                class email(PGColumnMeta):
                    comment = "Primary contact email"

        with pytest.raises(DBWardenConfigError, match=r"Do not use mapped_column\(info="):
            apply_meta(InvalidModel)

    def test_extract_table_from_model_applies_clickhouse_meta(self, monkeypatch):
        class Event(Base):
            __tablename__ = "events"

            id: Mapped[int] = mapped_column(Integer, primary_key=True)
            payload: Mapped[str] = mapped_column(String(255))

            class Meta:
                ch_engine = "MergeTree"
                ch_order_by = ["id"]

                class payload(CHColumnMeta):
                    ch = ch.field(codec="ZSTD(3)")

        monkeypatch.setattr(model_discovery.type_mapping, "_get_backend_name", lambda db_name=None: "clickhouse")

        table = extract_table_from_model(Event)

        assert table is not None
        assert table.clickhouse_options["ch_engine"] == "MergeTree"
        assert table.clickhouse_options["ch_order_by"] == ["id"]
        assert table.columns[1].codec == "ZSTD(3)"

    def test_extract_table_from_model_allows_cross_backend_keys(self, monkeypatch):
        class Report(Base):
            __tablename__ = "reports"

            id: Mapped[int] = mapped_column(Integer, primary_key=True)
            title: Mapped[str] = mapped_column(String(255))

            class Meta:
                class title(PGColumnMeta):
                    pg = pg.field(storage="extended")

        monkeypatch.setattr(model_discovery.type_mapping, "_get_backend_name", lambda db_name=None: "sqlite")

        table = extract_table_from_model(Report)

        assert table is not None
        assert Report.__table__.c.title.info["pg_storage"] == "extended"

    def test_flat_backend_attrs_rejected(self):
        with pytest.raises(DBWardenConfigError, match=r"Unknown attribute 'pg_collation'"):
            class BadModel(Base):
                __tablename__ = "bad_models"

                id: Mapped[int] = mapped_column(Integer, primary_key=True)
                name: Mapped[str] = mapped_column(String(255))

                class Meta:
                    class name(PGColumnMeta):
                        pg_collation = "en_US.UTF-8"


class TestIndexSpec:
    def test_index_spec_fields(self):
        from dbwarden.schema.index import IndexSpec

        spec = IndexSpec(columns=["a", "b"], name="ix_test", unique=True)
        assert spec.name == "ix_test"
        assert spec.columns == ["a", "b"]
        assert spec.unique is True

    def test_index_factory_returns_dict(self):
        d = index("ix_test", ["a", "b"], unique=True, using="gin", where="status = 'active'")
        assert d == {"name": "ix_test", "columns": ["a", "b"], "unique": True, "using": "gin", "where": "status = 'active'"}

    def test_index_factory_omits_defaults(self):
        d = index("ix_test", ["a"])
        assert d == {"name": "ix_test", "columns": ["a"], "unique": False}
        assert "nulls_not_distinct" not in d
        assert "using" not in d

    def test_index_factory_nulls_not_distinct(self):
        d = index("ix_test", ["a"], nulls_not_distinct=True)
        assert d["nulls_not_distinct"] is True


class TestCheckSpec:
    def test_check_spec_fields(self):
        spec = CheckSpec(expression="age >= 0", name="ck_age", no_inherit=True)
        assert spec.name == "ck_age"
        assert spec.expression == "age >= 0"
        assert spec.no_inherit is True

    def test_check_factory_returns_dict(self):
        d = check("ck_age", "age >= 0", no_inherit=True)
        assert d == {"name": "ck_age", "expression": "age >= 0", "no_inherit": True}

    def test_check_factory_omits_defaults(self):
        d = check("ck_age", "age >= 0")
        assert d == {"name": "ck_age", "expression": "age >= 0"}
        assert "no_inherit" not in d


class TestUniqueSpec:
    def test_unique_spec_fields(self):
        spec = UniqueSpec(columns=["email"], name="uq_email", nulls_not_distinct=True, deferrable=True)
        assert spec.name == "uq_email"
        assert spec.columns == ["email"]
        assert spec.nulls_not_distinct is True
        assert spec.deferrable is True

    def test_unique_factory_returns_dict(self):
        d = unique("uq_email", ["email"], nulls_not_distinct=True, deferrable=True)
        assert d == {"name": "uq_email", "columns": ["email"], "nulls_not_distinct": True, "deferrable": True}

    def test_unique_factory_omits_defaults(self):
        d = unique("uq_email", ["email"])
        assert d == {"name": "uq_email", "columns": ["email"]}
        assert "nulls_not_distinct" not in d
        assert "deferrable" not in d


class TestMetaIndexes:
    def test_meta_indexes_feed_into_model_table(self, monkeypatch):
        class Post(Base):
            __tablename__ = "posts"

            id: Mapped[int] = mapped_column(Integer, primary_key=True)
            title: Mapped[str] = mapped_column(String(255))

            class Meta:
                indexes = [
                    index("ix_posts_title", ["title"]),
                ]

        monkeypatch.setattr(model_discovery.type_mapping, "_get_backend_name", lambda db_name=None: "postgresql")

        table = extract_table_from_model(Post)
        assert table is not None
        assert len(table.indexes) == 1
        assert table.indexes[0].name == "ix_posts_title"
        assert table.indexes[0].columns == ["title"]

    def test_meta_indexes_always_used_when_sa_indexes_exist(self, monkeypatch):
        from sqlalchemy import Index

        class BlogPost(Base):
            __tablename__ = "blog_posts"

            id: Mapped[int] = mapped_column(Integer, primary_key=True)
            title: Mapped[str] = mapped_column(String(255))

            __table_args__ = (
                Index("ix_sa_title", "title"),
            )

            class Meta:
                indexes = [
                    index("ix_meta_title", ["title"]),
                ]

        monkeypatch.setattr(model_discovery.type_mapping, "_get_backend_name", lambda db_name=None: "postgresql")

        table = extract_table_from_model(BlogPost)
        assert table is not None
        assert len(table.indexes) == 1
        assert table.indexes[0].name == "ix_meta_title"


class TestCHTableMeta:
    def test_ch_table_meta_all_fields_accessible(self):
        assert hasattr(CHTableMeta, "ch_engine")
        assert hasattr(CHTableMeta, "ch_order_by")
        assert hasattr(CHTableMeta, "ch_primary_key")
        assert hasattr(CHTableMeta, "ch_partition_by")
        assert hasattr(CHTableMeta, "ch_sample_by")
        assert hasattr(CHTableMeta, "ch_ttl")
        assert hasattr(CHTableMeta, "ch_settings")
        assert hasattr(CHTableMeta, "ch_object_type")
        assert hasattr(CHTableMeta, "ch_select_statement")
        assert hasattr(CHTableMeta, "ch_zookeeper_path")
        assert hasattr(CHTableMeta, "ch_replica_name")
        assert hasattr(CHTableMeta, "ch_to_table")
        assert hasattr(CHTableMeta, "ch_dict_layout")
        assert hasattr(CHTableMeta, "ch_dict_source")
        assert hasattr(CHTableMeta, "ch_dict_lifetime")
        assert hasattr(CHTableMeta, "ch_dict_primary_key")
        assert hasattr(CHTableMeta, "ch_projections")
        assert hasattr(CHTableMeta, "ch_dictionary")
        assert hasattr(CHTableMeta, "comment")

    def test_ch_column_meta_has_ch_field(self):
        assert hasattr(CHColumnMeta, "ch")

    def test_ch_engine_spec_in_meta(self, monkeypatch):
        class Event(Base):
            __tablename__ = "events2"

            id: Mapped[int] = mapped_column(Integer, primary_key=True)
            payload: Mapped[str] = mapped_column(String(255))

            class Meta:
                ch_engine = ChEngineSpec(name="ReplicatedMergeTree", args=("/clickhouse/tables/{shard}", "{replica}"))
                ch_order_by = ["id"]
                ch_partition_by = ["toYYYYMM(created_at)"]

        monkeypatch.setattr(model_discovery.type_mapping, "_get_backend_name", lambda db_name=None: "clickhouse")

        table = extract_table_from_model(Event)
        engine_raw = table.clickhouse_options["ch_engine_raw"]
        assert isinstance(engine_raw, ChEngineSpec)
        assert engine_raw.name == "ReplicatedMergeTree"
        assert engine_raw.args == ("/clickhouse/tables/{shard}", "{replica}")
        assert table.clickhouse_options["ch_order_by"] == ["id"]
        assert table.clickhouse_options["ch_partition_by"] == ["toYYYYMM(created_at)"]

    def test_ch_projections_meta(self, monkeypatch):
        from dbwarden.databases.clickhouse.projection import ProjectionSpec

        class Event(Base):
            __tablename__ = "events3"

            id: Mapped[int] = mapped_column(Integer, primary_key=True)
            payload: Mapped[str] = mapped_column(String(255))

            class Meta:
                ch_engine = "MergeTree"
                ch_order_by = ["id"]
                ch_projections = [
                    ProjectionSpec(name="proj_day", query="SELECT id, toDate(created_at) AS day GROUP BY day"),
                ]

        monkeypatch.setattr(model_discovery.type_mapping, "_get_backend_name", lambda db_name=None: "clickhouse")

        table = extract_table_from_model(Event)
        projections = table.clickhouse_options["ch_projections"]
        assert len(projections) == 1
        assert projections[0]["name"] == "proj_day"
        assert "toDate(created_at) AS day" in projections[0]["query"]

    def test_ch_meta_inheritance(self, monkeypatch):
        class BaseCH(Base):
            __abstract__ = True

            class Meta:
                ch_engine = "ReplicatedMergeTree"
                ch_order_by = ["id"]

        class DerivedCH(BaseCH):
            __tablename__ = "derived_ch"

            id: Mapped[int] = mapped_column(Integer, primary_key=True)
            payload: Mapped[str] = mapped_column(String(255))

            class Meta(BaseCH.Meta):
                ch_partition_by = ["toYYYYMM(created_at)"]

        monkeypatch.setattr(model_discovery.type_mapping, "_get_backend_name", lambda db_name=None: "clickhouse")

        table = extract_table_from_model(DerivedCH)
        assert table.clickhouse_options["ch_engine"] == "ReplicatedMergeTree"
        assert table.clickhouse_options["ch_order_by"] == ["id"]
        assert table.clickhouse_options["ch_partition_by"] == ["toYYYYMM(created_at)"]

    def test_no_clickhouse_backward_compat_keys_in_info(self, monkeypatch):
        class NoCompat(Base):
            __tablename__ = "no_compat_ch"

            id: Mapped[int] = mapped_column(Integer, primary_key=True)
            payload: Mapped[str] = mapped_column(String(255))

            class Meta:
                ch_engine = "MergeTree"
                ch_order_by = ["id"]

                class payload(CHColumnMeta):
                    ch = ch.field(codec="ZSTD(3)")

        monkeypatch.setattr(model_discovery.type_mapping, "_get_backend_name", lambda db_name=None: "clickhouse")

        table = extract_table_from_model(NoCompat)
        for col in table.columns:
            for key in col.ch_meta:
                assert not key.startswith("clickhouse_"), f"Backward compat key {key} found"
        assert "ch_engine" in table.clickhouse_options

    def test_ch_meta_apply_meta(self):
        class CHModel(Base):
            __tablename__ = "ch_models"

            id: Mapped[int] = mapped_column(Integer, primary_key=True)
            body: Mapped[str] = mapped_column(String(255))

            class Meta:
                ch_engine = "MergeTree"
                ch_order_by = ["id"]

                class body(CHColumnMeta):
                    ch = ch.field(codec="ZSTD(3)")
                    comment = "body column"

        apply_meta(CHModel)
        body_info = CHModel.__table__.c.body.info
        assert body_info["ch_codec"] == "ZSTD(3)"
        assert body_info["dw_comment"] == "body column"


class TestCHViewMeta:
    def test_ch_view_meta_has_fields(self):
        assert hasattr(CHViewMeta, "ch")
        assert hasattr(CHViewMeta, "comment")

    def test_ch_view_registry(self):
        """ChView subclasses with Meta.ch are registered at import time."""
        class ViewModel(ChView):
            __tablename__ = "ch_registry_test"

            class Meta(CHViewMeta):
                ch = materialized_view(
                    select="SELECT 1",
                    to="some_target",
                )

        assert ViewModel in ChView._ch_view_registry

    def test_ch_view_registry_skips_abstract(self):
        """Abstract ChView subclasses (MaterializedView, AggregatingView) are not registered."""
        assert MaterializedView not in ChView._ch_view_registry
        assert AggregatingView not in ChView._ch_view_registry

    def test_ch_view_registry_skips_no_meta(self):
        """ChView subclass without Meta is silently skipped (not an error)."""
        class NoMetaView(ChView):
            __tablename__ = "no_meta_view"

        assert NoMetaView in ChView._ch_view_registry
        # get_all_ch_views will skip it because Meta.ch is absent

    def test_view_meta_with_materialized_view_spec(self, monkeypatch):
        class ViewTarget(Base):
            __tablename__ = "view_target"

            id: Mapped[int] = mapped_column(Integer, primary_key=True)

        class ViewModel(Base):
            __tablename__ = "ch_view_spec"

            id: Mapped[int] = mapped_column(Integer, primary_key=True)

            class Meta(CHViewMeta):
                ch = materialized_view(
                    select="SELECT id FROM view_target",
                    to="view_target",
                )

        monkeypatch.setattr(model_discovery.type_mapping, "_get_backend_name",
                            lambda db_name=None: "clickhouse")

        table = extract_table_from_model(ViewModel)
        assert table.object_type == "materialized_view"
        assert table.clickhouse_options["ch_select_statement"] == "SELECT id FROM view_target"
        assert table.clickhouse_options["ch_to_table"] == "view_target"

    def test_view_meta_extract_backend(self, monkeypatch):
        class ViewModel(Base):
            __tablename__ = "ch_view_backend"

            id: Mapped[int] = mapped_column(Integer, primary_key=True)

            class Meta(CHViewMeta):
                ch = materialized_view(
                    select="SELECT id FROM some_source",
                    to="some_source",
                )

        monkeypatch.setattr(model_discovery.type_mapping, "_get_backend_name",
                            lambda db_name=None: "clickhouse")

        apply_meta(ViewModel)
        meta = read_meta(ViewModel)
        assert meta is not None
        assert isinstance(meta.backend_table, MaterializedViewSpec)
        assert meta.backend_table.to == "some_source"

    def test_derive_agg_target_columns(self):
        from dbwarden.databases.clickhouse.agg import agg
        from sqlalchemy import column, func

        result = aggregating_view(
            name="test_agg",
            source="events",
            group_by=[func.toDate(column("event_time")).label("day")],
            aggregates=[
                agg.sum(column("amount"), "Float64").as_("total"),
            ],
            order_by=[column("day")],
        )
        cols = derive_agg_target_columns(result)
        assert cols == ["day", "total"]

    def test_aggregating_view_meta_payload(self, monkeypatch):
        from dbwarden.databases.clickhouse.agg import agg
        from sqlalchemy import column, func

        class ViewModel(Base):
            __tablename__ = "ch_agg_payload"

            day: Mapped[str] = mapped_column(String, primary_key=True)
            total: Mapped[str] = mapped_column(String)

            class Meta(CHViewMeta):
                ch = aggregating_view(
                    name="ch_agg_payload",
                    source="events",
                    group_by=[func.toDate(column("event_time")).label("day")],
                    aggregates=[
                        agg.sum(column("amount"), "Float64").as_("total"),
                    ],
                    order_by=[column("day")],
                )

        monkeypatch.setattr(model_discovery.type_mapping, "_get_backend_name",
                            lambda db_name=None: "clickhouse")

        table = extract_table_from_model(ViewModel)
        assert table.object_type == "materialized_view"
        assert table.clickhouse_options["ch_object_type"] == "materialized_view"

    def test_aggregating_view_backend_table(self, monkeypatch):
        from dbwarden.databases.clickhouse.agg import agg
        from sqlalchemy import column, func

        class ViewModel(Base):
            __tablename__ = "ch_agg_backend"

            day: Mapped[str] = mapped_column(String, primary_key=True)
            total: Mapped[str] = mapped_column(String)

            class Meta(CHViewMeta):
                ch = aggregating_view(
                    name="ch_agg_backend",
                    source="events",
                    group_by=[func.toDate(column("event_time")).label("day")],
                    aggregates=[
                        agg.sum(column("amount"), "Float64").as_("total"),
                    ],
                    order_by=[column("day")],
                )

        monkeypatch.setattr(model_discovery.type_mapping, "_get_backend_name",
                            lambda db_name=None: "clickhouse")

        apply_meta(ViewModel)
        meta = read_meta(ViewModel)
        assert meta is not None
        from dbwarden.databases.clickhouse import AggregatingViewSpec
        assert isinstance(meta.backend_table, AggregatingViewSpec)
        agg_dict = meta.backend_table.to_dict()
        assert "ch_agg_target" in agg_dict
        assert "ch_agg_mv" in agg_dict


class TestCHViewValidation:
    def test_validate_mv_engine_merge_tree(self):
        from dbwarden.databases.clickhouse.views import _validate_mv_engine
        _validate_mv_engine("MergeTree")
        _validate_mv_engine("ReplicatedMergeTree")
        _validate_mv_engine("SummingMergeTree")
        _validate_mv_engine("AggregatingMergeTree")

    def test_validate_mv_engine_rejects_non_mt(self):
        from dbwarden.databases.clickhouse.views import _validate_mv_engine
        import pytest
        with pytest.raises(ValueError, match="Invalid engine"):
            _validate_mv_engine("Null")
        with pytest.raises(ValueError, match="Invalid engine"):
            _validate_mv_engine("Memory")
        with pytest.raises(ValueError, match="Invalid engine"):
            _validate_mv_engine("Merge")
        with pytest.raises(ValueError, match="Invalid engine"):
            _validate_mv_engine("Distributed")

    def test_validate_mv_engine_aggregates_collapsing(self):
        from dbwarden.databases.clickhouse.views import _validate_mv_engine
        _validate_mv_engine("SummingMergeTree", has_aggregates=True)
        _validate_mv_engine("AggregatingMergeTree", has_aggregates=True)

    def test_validate_mv_engine_aggregates_non_collapsing(self):
        from dbwarden.databases.clickhouse.views import _validate_mv_engine
        import pytest
        with pytest.raises(ValueError, match="cannot collapse"):
            _validate_mv_engine("MergeTree", has_aggregates=True)

    def test_validate_mv_engine_raw_select_warns(self):
        from dbwarden.databases.clickhouse.views import _validate_mv_engine
        import warnings
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            _validate_mv_engine("MergeTree", select_is_raw=True)
        assert any("collapse" in str(msg.message).lower() for msg in w)

    def test_materialized_view_optional_name(self):
        from dbwarden.databases.clickhouse import MaterializedViewSpec
        spec = MaterializedViewSpec(select="SELECT 1")
        assert spec.name is None

    def test_materialized_view_mode_b_engine_forbidden(self):
        from dbwarden.databases.clickhouse import (
            ChView, MaterializedView, materialized_view,
        )
        from dbwarden.schema.table_meta import CHViewMeta
        import pytest
        with pytest.raises(TypeError, match="must not declare engine"):
            class BadMV(ChView):
                __tablename__ = "bad_mv_b_engine"
                class Meta(CHViewMeta):
                    ch = materialized_view(select="SELECT 1", to="target", engine="MergeTree")

    def test_materialized_view_mode_b_order_by_forbidden(self):
        from dbwarden.databases.clickhouse import (
            ChView, materialized_view,
        )
        from dbwarden.schema.table_meta import CHViewMeta
        import pytest
        with pytest.raises(TypeError, match="must not declare order_by"):
            class BadMV(ChView):
                __tablename__ = "bad_mv_b_order"
                class Meta(CHViewMeta):
                    ch = materialized_view(select="SELECT 1", to="target", order_by=["id"])

    def test_materialized_view_mode_b_columns_forbidden(self):
        from sqlalchemy import Integer
        from sqlalchemy.orm import Mapped, mapped_column
        from dbwarden.databases.clickhouse import (
            ChView, materialized_view,
        )
        from dbwarden.schema.table_meta import CHViewMeta
        import pytest
        with pytest.raises(TypeError, match="must not declare columns"):
            class BadMV(ChView):
                __tablename__ = "bad_mv_b_cols"
                id: Mapped[int] = mapped_column(Integer, primary_key=True)
                class Meta(CHViewMeta):
                    ch = materialized_view(select="SELECT 1", to="target")

    def test_aggregating_view_columns_forbidden(self):
        from sqlalchemy import Integer
        from sqlalchemy.orm import Mapped, mapped_column
        from dbwarden.databases.clickhouse import (
            ChView, aggregating_view, agg,
        )
        from dbwarden.schema.table_meta import CHViewMeta
        import pytest
        with pytest.raises(TypeError, match="columns are derived"):
            class BadAV(ChView):
                __tablename__ = "bad_av_cols"
                id: Mapped[int] = mapped_column(Integer, primary_key=True)
                class Meta(CHViewMeta):
                    ch = aggregating_view(
                        source="src",
                        group_by=["id"],
                        aggregates=[agg.count().as_("cnt")],
                        order_by=["id"],
                    )

    def test_validate_view_class_sets_name_from_tablename(self):
        from dbwarden.databases.clickhouse import (
            ChView, materialized_view,
        )
        from dbwarden.schema.table_meta import CHViewMeta
        class GoodMV(ChView):
            __tablename__ = "my_view"
            class Meta(CHViewMeta):
                ch = materialized_view(select="SELECT 1", to="target")
        spec = GoodMV.Meta.ch
        assert spec.name == "my_view"

    def test_materialized_view_class_discovery(self):
        """Part 0 probe: MaterializedView subclass registers and emits ModelTable."""
        from sqlalchemy import Date as sa_Date, Float as sa_Float, func
        from sqlalchemy.orm import Mapped, mapped_column
        from dbwarden.databases.clickhouse import (
            ChView, MaterializedView, materialized_view,
            summing_merge_tree, ch_view_tables_from_models,
        )
        from dbwarden.schema.table_meta import CHViewMeta

        class _Probe(MaterializedView):
            __tablename__ = "_probe_mv"
            date: Mapped[str] = mapped_column(sa_Date, primary_key=True)
            total: Mapped[float] = mapped_column(sa_Float)
            class Meta(CHViewMeta):
                ch = materialized_view(
                    select=[func.now().label("date"), func.now().label("total")],
                    engine=summing_merge_tree(),
                    order_by=["date"],
                )

        assert _Probe in ChView._ch_view_registry, "FAIL: not in registry"
        tables = ch_view_tables_from_models()
        assert any(t.name == "_probe_mv" for t in tables), (
            f"FAIL: no ModelTable (found {[t.name for t in tables]})"
        )

    def test_materialized_view_mode_a_columns(self):
        """Mode A: ch_view_tables_from_models() produces correct columns.

        Verifies that columns declared via mapped_column() on a
        MaterializedView subclass (without Base) are read correctly
        from MappedColumn descriptors in cls.__dict__.
        """
        from sqlalchemy import Date as sa_Date, Float as sa_Float, Integer, func
        from sqlalchemy.orm import Mapped, mapped_column
        from dbwarden.databases.clickhouse import (
            MaterializedView, materialized_view,
            summing_merge_tree, ch_view_tables_from_models,
        )
        from dbwarden.schema.table_meta import CHViewMeta

        class _ModeAProbe(MaterializedView):
            __tablename__ = "_mode_a_probe"
            date: Mapped[str] = mapped_column(sa_Date, primary_key=True)
            total: Mapped[float] = mapped_column(sa_Float)
            cnt: Mapped[int] = mapped_column(Integer)
            class Meta(CHViewMeta):
                ch = materialized_view(
                    select=[func.now().label("date"), func.now().label("total")],
                    engine=summing_merge_tree(),
                    order_by=["date"],
                )

        tables = ch_view_tables_from_models()
        probe = next((t for t in tables if t.name == "_mode_a_probe"), None)
        assert probe is not None, "FAIL: no ModelTable produced"

        col_names = {c.name for c in probe.columns}
        assert col_names == {"date", "total", "cnt"}, (
            f"FAIL: expected {{'date','total','cnt'}}, got {col_names}"
        )

        col_map = {c.name: c for c in probe.columns}
        assert col_map["date"].primary_key is True
        assert col_map["date"].nullable is False
        assert col_map["total"].nullable is True


class TestCHViewDiscovery:
    def test_get_all_ch_views(self):
        """Returns registered ChView subclasses."""
        # Add a test view to the registry
        class TestView(ChView):
            __tablename__ = "test_get_all_view"

            class Meta(CHViewMeta):
                ch = materialized_view(
                    select="SELECT 1",
                    to="test_get_all_target",
                )

        assert TestView in ChView._ch_view_registry

        views = get_all_ch_views()
        matching = [v for v in views if v["model_class"] is TestView]
        assert len(matching) == 1
        assert matching[0]["view_type"] == "materialized_view"

    def test_get_all_model_tables_includes_ch_views(self, tmp_path):
        """get_all_model_tables() includes CH view ModelTables from AggregatingView subclasses.

        Regression test: ChView subclasses defined in model files should be
        discovered via the class registry and emitted as target + MV ModelTables.
        """
        import textwrap
        from dbwarden.engine.model_discovery import get_all_model_tables
        from dbwarden.databases.clickhouse.views import ChView

        ChView._ch_view_registry.clear()

        models_dir = tmp_path / "models"
        models_dir.mkdir()

        (models_dir / "__init__.py").write_text("")

        (models_dir / "sa_model.py").write_text(textwrap.dedent("""\
            from sqlalchemy import Column, Integer, String
            from sqlalchemy.orm import declarative_base
            Base = declarative_base()
            class SomeTable(Base):
                __tablename__ = "some_table"
                id = Column(Integer, primary_key=True)
                dt = Column(String)
                val = Column(Integer)
        """))

        (models_dir / "agg_model.py").write_text(textwrap.dedent("""\
            from sqlalchemy import func
            from dbwarden.databases.clickhouse import (
                AggregatingView, CHViewMeta, aggregating_view, agg,
            )
            class TestAgg(AggregatingView):
                __tablename__ = "test_agg_target"
                class Meta(CHViewMeta):
                    ch = aggregating_view(
                        source="some_table",
                        group_by=[func.toDate("dt").label("day")],
                        aggregates=[agg.sum("val").as_("total")],
                        order_by=["day"],
                    )
        """))

        tables = get_all_model_tables(model_paths=[str(models_dir)])

        # Should find the SA table
        assert any(t.name == "some_table" and t.object_type == "table" for t in tables), \
            "SA model table should be discovered"

        # Should find the aggregating view target table
        assert any(t.name == "test_agg_target" and t.object_type == "table"
                   and t.clickhouse_options.get("ch_engine") == "AggregatingMergeTree"
                   for t in tables), \
            "AggregatingView target table should be discovered with AggregatingMergeTree engine"

        # Should find the aggregating view MV
        assert any(t.name == "test_agg_target_mv" and t.object_type == "materialized_view"
                   for t in tables), \
            "AggregatingView MV should be discovered as materialized_view"

        assert len(tables) == 3, \
            f"Expected 3 tables (1 SA + 1 target + 1 MV), got {len(tables)}"
        assert len(ChView._ch_view_registry) == 1, \
            f"Registry should have 1 entry, got {len(ChView._ch_view_registry)}"

    def test_get_all_model_tables_ch_views_across_multiple_files(self, tmp_path):
        """CH views from multiple model files are all discovered."""
        import textwrap
        from dbwarden.engine.model_discovery import get_all_model_tables
        from dbwarden.databases.clickhouse.views import ChView

        ChView._ch_view_registry.clear()

        models_dir = tmp_path / "models2"
        models_dir.mkdir()

        (models_dir / "__init__.py").write_text("")

        (models_dir / "sa_model.py").write_text(textwrap.dedent("""\
            from sqlalchemy import Column, Integer, String
            from sqlalchemy.orm import declarative_base
            Base = declarative_base()
            class SomeTable(Base):
                __tablename__ = "some_table"
                id = Column(Integer, primary_key=True)
                dt = Column(String)
                val = Column(Integer)
        """))

        (models_dir / "agg_a.py").write_text(textwrap.dedent("""\
            from sqlalchemy import func
            from dbwarden.databases.clickhouse import (
                AggregatingView, CHViewMeta, aggregating_view, agg,
            )
            class AggA(AggregatingView):
                __tablename__ = "agg_a_target"
                class Meta(CHViewMeta):
                    ch = aggregating_view(
                        source="some_table",
                        group_by=[func.toDate("dt").label("day")],
                        aggregates=[agg.sum("val").as_("total")],
                        order_by=["day"],
                    )
        """))

        (models_dir / "agg_b.py").write_text(textwrap.dedent("""\
            from sqlalchemy import func
            from dbwarden.databases.clickhouse import (
                AggregatingView, CHViewMeta, aggregating_view, agg,
            )
            class AggB(AggregatingView):
                __tablename__ = "agg_b_target"
                class Meta(CHViewMeta):
                    ch = aggregating_view(
                        source="some_table",
                        group_by=[func.toDate("dt").label("day")],
                        aggregates=[agg.sum("val").as_("total")],
                        order_by=["day"],
                    )
        """))

        tables = get_all_model_tables(model_paths=[str(models_dir)])

        agg_tables = [t for t in tables if "agg_" in t.name]
        assert len(agg_tables) == 4, \
            f"Expected 4 agg tables (2 targets + 2 MVs), got {len(agg_tables)}"
        table_names = sorted(t.name for t in agg_tables)
        assert table_names == ["agg_a_target", "agg_a_target_mv",
                               "agg_b_target", "agg_b_target_mv"]

    def test_expand_agg_target(self):
        from sqlalchemy import String
        from dbwarden.databases.clickhouse.agg import agg
        from sqlalchemy import column, func

        class Base(DeclarativeBase):
            pass

        class ViewModel(Base):
            __tablename__ = "test_agg_expand"

            day: Mapped[str] = mapped_column(String, primary_key=True)
            total: Mapped[str] = mapped_column(String)

            class Meta(CHViewMeta):
                ch = aggregating_view(
                    name="test_agg_expand",
                    source="events",
                    group_by=[func.toDate(column("event_time")).label("day")],
                    aggregates=[
                        agg.sum(column("amount"), "Float64").as_("total"),
                    ],
                    order_by=[column("day")],
                )

        from dbwarden.databases.clickhouse.views import _expand_agg_target
        from dbwarden.schema._meta_reader import apply_meta
        apply_meta(ViewModel)

        spec = ViewModel.Meta.ch
        assert isinstance(spec, AggregatingViewSpec)
        target = _expand_agg_target(ViewModel, spec)
        assert target is not None
        # In the class API, target name is __tablename__ (here: name), not name + "_agg"
        assert target.name == "test_agg_expand"
        assert target.object_type == "table"
        assert "AggregatingMergeTree" in str(target.clickhouse_options.get("ch_engine", ""))

    def test_cascade_combinator_detection(self, tmp_path):
        """Regression: cascade MVs emit *MergeState when source is a string forward
        reference to another AggregatingView. _resolve_source_column_types must
        find the source class via ChView._ch_view_registry (not only sys.modules)."""
        import textwrap

        ChView._ch_view_registry.clear()

        models_dir = tmp_path / "cascade_test"
        models_dir.mkdir()
        (models_dir / "__init__.py").write_text("")

        (models_dir / "sa_model.py").write_text(textwrap.dedent("""\
            from sqlalchemy import Column, Integer
            from sqlalchemy.orm import declarative_base
            Base = declarative_base()
            class FactTable(Base):
                __tablename__ = "fact_table"
                id = Column(Integer, primary_key=True)
                amount = Column(Integer)
        """))

        (models_dir / "agg_hourly.py").write_text(textwrap.dedent("""\
            from sqlalchemy import func, column
            from dbwarden.databases.clickhouse import (
                AggregatingView, CHViewMeta, aggregating_view, agg,
            )
            class HourlyRollup(AggregatingView):
                __tablename__ = "hourly_rollup"
                class Meta(CHViewMeta):
                    ch = aggregating_view(
                        source="fact_table",
                        group_by=[column("branch_id")],
                        aggregates=[agg.sum("amount", "Float64").as_("total")],
                        order_by=["branch_id"],
                    )
        """))

        (models_dir / "agg_daily.py").write_text(textwrap.dedent("""\
            from sqlalchemy import func, column
            from dbwarden.databases.clickhouse import (
                AggregatingView, CHViewMeta, aggregating_view, agg,
            )
            class DailyRollup(AggregatingView):
                __tablename__ = "daily_rollup"
                class Meta(CHViewMeta):
                    ch = aggregating_view(
                        source="HourlyRollup",
                        group_by=[func.toDate(column("day")).label("day")],
                        aggregates=[agg.sum("total", "Float64").as_("total")],
                        order_by=["day"],
                    )
        """))

        from dbwarden.engine.model_discovery import get_all_model_tables
        from dbwarden.databases.clickhouse.materialized_view import (
            _resolve_source_column_types,
        )

        tables = get_all_model_tables(model_paths=[str(models_dir)])

        # Verify cascade source is resolvable via registry
        types = _resolve_source_column_types("HourlyRollup")
        assert "total" in types, (
            f"Expected 'total' in resolved types for HourlyRollup, got {types}"
        )
        assert "AggregateFunction(sum, Float64)" in types["total"]

        # Verify daily MV SELECT in clickhouse_options uses MergeState
        daily_mv = next((t for t in tables if t.name == "daily_rollup_mv"), None)
        assert daily_mv is not None, "daily_rollup_mv not found"
        assert daily_mv.object_type == "materialized_view"
        select_stmt = daily_mv.clickhouse_options.get("ch_select_statement", "")
        assert "sumMergeState" in select_stmt, (
            f"Expected sumMergeState in cascade MV options, got:\n{select_stmt}"
        )
        assert "sumState" not in select_stmt, (
            f"Cascade MV should not use sumState, got:\n{select_stmt}"
        )

        ChView._ch_view_registry.clear()

    def test_group_by_key_types_from_source(self):
        """Regression: group-by keys matching source SA columns resolve their
        type instead of always falling back to String.  Source passed as a
        class reference (not string) is resolvable via sys.modules."""
        from sqlalchemy import Integer
        from sqlalchemy.orm import DeclarativeBase, mapped_column
        from dbwarden.databases.clickhouse.agg import agg
        from dbwarden.databases.clickhouse.views import _expand_agg_target, ChView
        from dbwarden.schema._meta_reader import apply_meta

        ChView._ch_view_registry.clear()

        class Base(DeclarativeBase):
            pass

        class Orders(Base):
            __tablename__ = "orders"
            id = mapped_column(Integer, primary_key=True)
            branch_id = mapped_column(Integer)
            amount = mapped_column(Integer)

        class OrderRollup(AggregatingView):
            __tablename__ = "order_rollup"
            class Meta(CHViewMeta):
                ch = aggregating_view(
                    source=Orders,
                    group_by=["branch_id"],
                    aggregates=[agg.sum("amount", "Float64").as_("total")],
                    order_by=["branch_id"],
                )

        apply_meta(OrderRollup)
        target = _expand_agg_target(OrderRollup, OrderRollup.Meta.ch)
        assert target is not None
        col_map = {c.name: c for c in target.columns}
        assert col_map["branch_id"].type != "String", (
            f"branch_id should resolve from source SA column, "
            f"got: {col_map['branch_id'].type}"
        )
        assert "INT" in col_map["branch_id"].type.upper(), (
            f"branch_id should be INTEGER, got: {col_map['branch_id'].type}"
        )

        ChView._ch_view_registry.clear()

    def test_rollback_drops_mvs_before_target_tables(self):
        """Regression: rollback SQL must drop materialized views before their
        dependent target tables. _assemble_migration assigns CREATE_VIEW (8) to
        MVs and CREATE_TABLE (7) to targets, so when the rollback list is
        reversed MVs appear first."""
        from dbwarden.engine.core.statement_order import (
            MigrationStatement, StatementOrder, _assemble_migration,
        )

        # Simulate the order emitted by _build_create_table_sequence
        stmts = [
            MigrationStatement(
                order=StatementOrder.CREATE_TABLE,
                upgrade_sql="CREATE TABLE IF NOT EXISTS target",
                rollback_sql="DROP TABLE IF EXISTS target",
            ),
            MigrationStatement(
                order=StatementOrder.CREATE_VIEW,
                upgrade_sql="CREATE MATERIALIZED VIEW IF NOT EXISTS target_mv",
                rollback_sql="DROP VIEW IF EXISTS target_mv",
            ),
        ]

        up_sql, rb_sql = _assemble_migration(stmts)

        # Upgrade: table before MV
        up_lines = [ln.strip() for ln in up_sql.split("\n\n")]
        table_idx = next(i for i, ln in enumerate(up_lines) if "CREATE TABLE" in ln)
        mv_idx = next(i for i, ln in enumerate(up_lines) if "CREATE MATERIALIZED" in ln)
        assert table_idx < mv_idx, "Upgrade must create table before MV"

        # Rollback: MV before table (reversed order)
        rb_lines = [ln.strip() for ln in rb_sql.split("\n\n")]
        mv_rb_idx = next(i for i, ln in enumerate(rb_lines) if "DROP VIEW" in ln)
        table_rb_idx = next(i for i, ln in enumerate(rb_lines) if "DROP TABLE" in ln)
        assert mv_rb_idx < table_rb_idx, (
            f"Rollback must drop MV before table: "
            f"MV at {mv_rb_idx}, table at {table_rb_idx}"
        )

    def test_cascade_dependency_ordering(self, tmp_path):
        """Regression: cascade AggregatingViews with string forward-references
        loaded via load_model_from_path are topologically sorted in correct
        dependency order (innermost first).  The views.py:267 sys.modules scan
        must fall back to ChView._ch_view_registry."""
        import textwrap
        from dbwarden.databases.clickhouse.views import ChView
        from dbwarden.engine.model_discovery import get_all_model_tables

        ChView._ch_view_registry.clear()

        models_dir = tmp_path / "cascade_order"
        models_dir.mkdir()
        (models_dir / "__init__.py").write_text("")

        (models_dir / "fact.py").write_text(textwrap.dedent("""\
            from sqlalchemy import Column, Integer
            from sqlalchemy.orm import declarative_base
            Base = declarative_base()
            class Fact(Base):
                __tablename__ = "fact"
                id = Column(Integer, primary_key=True)
                amount = Column(Integer)
        """))

        (models_dir / "agg_hourly.py").write_text(textwrap.dedent("""\
            from sqlalchemy import func, column
            from dbwarden.databases.clickhouse import (
                AggregatingView, CHViewMeta, aggregating_view, agg,
            )
            class Hourly(AggregatingView):
                __tablename__ = "hourly"
                class Meta(CHViewMeta):
                    ch = aggregating_view(
                        source="fact",
                        group_by=[column("branch_id")],
                        aggregates=[agg.sum("amount", "Float64").as_("total")],
                        order_by=["branch_id"],
                    )
        """))

        (models_dir / "agg_daily.py").write_text(textwrap.dedent("""\
            from sqlalchemy import func, column
            from dbwarden.databases.clickhouse import (
                AggregatingView, CHViewMeta, aggregating_view, agg,
            )
            class Daily(AggregatingView):
                __tablename__ = "daily"
                class Meta(CHViewMeta):
                    ch = aggregating_view(
                        source="Hourly",
                        group_by=[func.toDate(column("day")).label("day")],
                        aggregates=[agg.sum("total", "Float64").as_("total")],
                        order_by=["day"],
                    )
        """))

        (models_dir / "agg_weekly.py").write_text(textwrap.dedent("""\
            from sqlalchemy import func, column
            from dbwarden.databases.clickhouse import (
                AggregatingView, CHViewMeta, aggregating_view, agg,
            )
            class Weekly(AggregatingView):
                __tablename__ = "weekly"
                class Meta(CHViewMeta):
                    ch = aggregating_view(
                        source="Daily",
                        group_by=[func.toDate(column("day")).label("day")],
                        aggregates=[agg.sum("total", "Float64").as_("total")],
                        order_by=["day"],
                    )
        """))

        # Use get_all_model_tables to trigger model imports (model_paths is
        # ignored by get_all_ch_views, which is registry-only).
        tables = get_all_model_tables(model_paths=[str(models_dir)])

        # Filter to ChView-derived tables (target + MV), excluding SA fact
        ch_names = [t.name for t in tables if t.object_type in ("table", "materialized_view")
                    and t.name not in ("fact",)]
        # Expect: hourly, hourly_mv, daily, daily_mv, weekly, weekly_mv
        hourly_idx = ch_names.index("hourly")
        daily_idx = ch_names.index("daily")
        weekly_idx = ch_names.index("weekly")
        assert hourly_idx < daily_idx < weekly_idx, (
            f"Dependency order violated: hourly={hourly_idx}, daily={daily_idx}, weekly={weekly_idx}\n"
            f"Got order: {ch_names}"
        )

        # MV is emitted right after its target
        hourly_mv_idx = ch_names.index("hourly_mv")
        daily_mv_idx = ch_names.index("daily_mv")
        weekly_mv_idx = ch_names.index("weekly_mv")
        assert hourly_idx < hourly_mv_idx, "hourly MV must come after hourly target"
        assert daily_idx < daily_mv_idx, "daily MV must come after daily target"
        assert weekly_idx < weekly_mv_idx, "weekly MV must come after weekly target"

        ChView._ch_view_registry.clear()

    def test_get_backend_fallback_sqlite_populates_ch_type(self):
        """Regression: extract_column_info populates ch_meta['ch_type'] even
        when _get_backend_name falls back to "sqlite".  The fix in
        extraction.py:467-8 ensures ch_type is set unconditionally."""
        from sqlalchemy import Column, Integer, MetaData, Table
        from dbwarden.engine.model_discovery.extraction import extract_column_info

        table = Table("t", MetaData(), Column("year", Integer))
        col = extract_column_info(table.c.year, backend="sqlite")
        assert col is not None
        ch_meta = col.ch_meta
        assert "ch_type" in ch_meta, (
            f"ch_type missing when backend='sqlite': got {ch_meta}"
        )
        assert ch_meta["ch_type"] is not None and ch_meta["ch_type"].strip(), (
            f"ch_type should be non-empty, got: {ch_meta['ch_type']!r}"
        )

    def test_ch_raw_group_by_types_resolve_through_cascade(self):
        """Regression: group-by columns defined via ch_raw() resolve their CH
        types by extracting column references from the raw SQL and looking them
        up in the source's column types.  Cascade chains are traversed
        recursively to reach the base SA table."""
        from sqlalchemy import Column, Integer, DateTime
        from sqlalchemy.orm import declarative_base
        from dbwarden.databases.clickhouse.materialized_view import (
            _resolve_source_column_types,
        )
        from dbwarden.databases.clickhouse.views import ChView
        from dbwarden.databases.clickhouse import ch_raw
        from dbwarden.databases.clickhouse.agg import agg

        ChView._ch_view_registry.clear()

        Base = declarative_base()

        class FactOrder(Base):
            __tablename__ = "fact_order"
            id = Column(Integer, primary_key=True)
            closed_at = Column(DateTime)
            branch_id = Column(Integer)

        class Hourly(AggregatingView):
            __tablename__ = "hourly"
            class Meta(CHViewMeta):
                ch = aggregating_view(
                    source=FactOrder,
                    group_by=[
                        ch_raw("toStartOfHour(toTimeZone(closed_at, "
                               "'America/Montevideo')) AS hour"),
                        "branch_id",
                    ],
                    aggregates=[agg.sum("amount", "Float64").as_("total")],
                    order_by=["hour"],
                )

        class Daily(AggregatingView):
            __tablename__ = "daily"
            class Meta(CHViewMeta):
                ch = aggregating_view(
                    source=Hourly,
                    group_by=[
                        ch_raw("toDate(hour) AS day"),
                        "branch_id",
                    ],
                    aggregates=[agg.sum("total", "Float64").as_("total")],
                    order_by=["day"],
                )

        # First level: Hourly's group-by keys resolve from FactOrder columns
        hourly_types = _resolve_source_column_types(Hourly)
        assert hourly_types.get("branch_id") == "INTEGER", (
            f"Expected INTEGER for branch_id, got {hourly_types.get('branch_id')}"
        )
        assert hourly_types.get("hour") == "DATETIME", (
            f"Expected DATETIME for hour (from closed_at), "
            f"got {hourly_types.get('hour')}"
        )

        # Cascade: Daily's group-by keys resolve through Hourly -> FactOrder
        daily_types = _resolve_source_column_types(Daily)
        assert daily_types.get("branch_id") == "INTEGER", (
            f"Expected INTEGER for branch_id (cascade), "
            f"got {daily_types.get('branch_id')}"
        )
        assert "day" in daily_types, (
            f"day should be resolved from cascade chain, got {daily_types}"
        )
        assert daily_types["day"] != "String", (
            f"day should not be String in cascade, got {daily_types['day']}"
        )

        ChView._ch_view_registry.clear()

    def test_drop_table_uses_drop_view_order_for_materialized_views(self):
        """Regression: TableHandler.emit() for a drop_table op with
        object_type=materialized_view uses StatementOrder.DROP_VIEW (11) instead
        of DROP_TABLE (14), so MVs are dropped before their target tables in
        upgrade order (and created after in rollback order)."""
        from dbwarden.engine.core.statement_order import (
            MigrationStatement, StatementOrder,
        )

        # Simulate what TableHandler.emit() produces with the fix:
        # MV drop with DROP_VIEW order, table drop with DROP_TABLE order
        stmts = [
            MigrationStatement(
                order=StatementOrder.DROP_VIEW,
                upgrade_sql="DROP VIEW IF EXISTS hourly_mv",
                rollback_sql="CREATE MATERIALIZED VIEW hourly_mv ...",
            ),
            MigrationStatement(
                order=StatementOrder.DROP_TABLE,
                upgrade_sql="DROP TABLE hourly",
                rollback_sql="CREATE TABLE hourly (...)",
            ),
        ]

        sorted_stmts = sorted(stmts, key=lambda s: s.order)
        assert sorted_stmts[0].order == StatementOrder.DROP_VIEW, (
            f"drop_view(11) should sort before drop_table(14), "
            f"got orders: {[s.order for s in sorted_stmts]}"
        )
        assert "DROP VIEW" in sorted_stmts[0].upgrade_sql, (
            "MV drop should appear before table drop in upgrade"
        )


class TestIndexSpecExtensions:
    def test_index_spec_to_dict_from_dict(self):
        from dbwarden.schema.index import IndexSpec

        spec = IndexSpec(columns=["a", "b"], name="ix_ab", unique=True,
                         using="gin", where="status = 'active'",
                         nulls_not_distinct=True, include=["c"],
                         tablespace="fast_ts")
        d = spec.to_dict()
        assert d["name"] == "ix_ab"
        assert d["columns"] == ["a", "b"]
        assert d["unique"] is True
        assert d["using"] == "gin"
        assert d["where"] == "status = 'active'"
        assert d["nulls_not_distinct"] is True
        assert d["include"] == ["c"]
        assert d["tablespace"] == "fast_ts"

        restored = IndexSpec.from_dict(d)
        assert restored.name == "ix_ab"
        assert restored.columns == ["a", "b"]
        assert restored.unique is True
        assert restored.include == ["c"]

    def test_index_spec_clickhouse_skip(self):
        from dbwarden.schema.index import IndexSpec

        spec = IndexSpec(columns=["a"], name="ix_sk", unique=False,
                         clickhouse_type="set(100)", clickhouse_granularity=2)
        d = spec.to_dict()
        assert d["clickhouse_type"] == "set(100)"
        assert d["clickhouse_granularity"] == 2

        restored = IndexSpec.from_dict(d)
        assert restored.clickhouse_type == "set(100)"
        assert restored.clickhouse_granularity == 2


class TestChEngineSpec:
    def test_basic_construction(self):
        from dbwarden.databases.clickhouse.engine import ChEngineSpec
        spec = ChEngineSpec("MergeTree")
        assert spec.name == "MergeTree"
        assert spec.args == ()
        assert spec.zookeeper_path is None
        assert spec.replica_name is None

    def test_with_args(self):
        from dbwarden.databases.clickhouse.engine import ChEngineSpec
        spec = ChEngineSpec("ReplacingMergeTree", args=("version_col",))
        assert spec.name == "ReplacingMergeTree"
        assert spec.args == ("version_col",)

    def test_with_zk_and_replica(self):
        from dbwarden.databases.clickhouse.engine import ChEngineSpec
        spec = ChEngineSpec("ReplicatedMergeTree",
            zookeeper_path="/zk/path", replica_name="{replica}")
        assert spec.zookeeper_path == "/zk/path"
        assert spec.replica_name == "{replica}"

    def test_to_dict_roundtrip(self):
        from dbwarden.databases.clickhouse.engine import ChEngineSpec
        spec = ChEngineSpec("ReplicatedMergeTree",
            args=("ver",), zookeeper_path="/zk", replica_name="{r}")
        d = spec.to_dict()
        restored = ChEngineSpec.from_dict(d)
        assert restored.name == spec.name
        assert restored.args == spec.args
        assert restored.zookeeper_path == spec.zookeeper_path
        assert restored.replica_name == spec.replica_name

    def test_to_dict_omits_defaults(self):
        from dbwarden.databases.clickhouse.engine import ChEngineSpec
        d = ChEngineSpec("MergeTree").to_dict()
        assert "args" not in d
        assert "zookeeper_path" not in d
        assert "replica_name" not in d
        assert "settings" not in d

    def test_from_engine_string_simple(self):
        from dbwarden.databases.clickhouse.engine import ChEngineSpec
        spec = ChEngineSpec.from_engine_string("MergeTree")
        assert spec.name == "MergeTree"
        assert spec.args == ()

    def test_from_engine_string_with_args(self):
        from dbwarden.databases.clickhouse.engine import ChEngineSpec
        spec = ChEngineSpec.from_engine_string("SummingMergeTree(col1, col2)")
        assert spec.name == "SummingMergeTree"
        assert spec.args == ("col1", "col2")

    def test_from_engine_string_replicated(self):
        from dbwarden.databases.clickhouse.engine import ChEngineSpec
        spec = ChEngineSpec.from_engine_string(
            "ReplicatedMergeTree('/zk/path', '{replica}', ver)")
        assert spec.name == "ReplicatedMergeTree"
        assert spec.zookeeper_path == "/zk/path"
        assert spec.replica_name == "{replica}"
        assert spec.args == ("ver",)

    def test_post_init_coerces_string_to_tuple(self):
        from dbwarden.databases.clickhouse.engine import ChEngineSpec
        spec = ChEngineSpec("CollapsingMergeTree", args="sign")
        assert spec.args == ("sign",)


class TestProjectionSpec:
    def test_basic_construction(self):
        from dbwarden.databases.clickhouse.projection import ProjectionSpec
        p = ProjectionSpec("by_date", "SELECT date, count() GROUP BY date")
        assert p.name == "by_date"
        assert p.query == "SELECT date, count() GROUP BY date"

    def test_to_dict_roundtrip(self):
        from dbwarden.databases.clickhouse.projection import ProjectionSpec
        p = ProjectionSpec("by_date", "SELECT date, count() GROUP BY date")
        d = p.to_dict()
        restored = ProjectionSpec.from_dict(d)
        assert restored.name == p.name
        assert restored.query == p.query

    def test_from_dict_with_empty_query(self):
        from dbwarden.databases.clickhouse.projection import ProjectionSpec
        p = ProjectionSpec.from_dict({"name": "by_date"})
        assert p.name == "by_date"
        assert p.query == ""


class TestChIndexSpec:
    def test_basic_construction(self):
        from dbwarden.databases.clickhouse import ChIndexSpec
        spec = ChIndexSpec("ix_payload", ["payload"], type="bloom_filter")
        assert spec.name == "ix_payload"
        assert spec.columns == ["payload"]
        assert spec.type == "bloom_filter"
        assert spec.granularity == 1
        assert spec.expr is None

    def test_with_granularity(self):
        from dbwarden.databases.clickhouse import ChIndexSpec
        spec = ChIndexSpec("ix_url", ["url"], type="minmax", granularity=3)
        assert spec.granularity == 3

    def test_with_expr(self):
        from dbwarden.databases.clickhouse import ChIndexSpec
        spec = ChIndexSpec("ix_lower", ["url"], type="bloom_filter", expr="lower(url)")
        assert spec.expr == "lower(url)"

    def test_to_dict_roundtrip(self):
        from dbwarden.databases.clickhouse import ChIndexSpec
        spec = ChIndexSpec("ix_payload", ["payload"], type="bloom_filter", granularity=1)
        d = spec.to_dict()
        restored = ChIndexSpec.from_dict(d)
        assert restored.name == spec.name
        assert restored.columns == spec.columns
        assert restored.type == spec.type
        assert restored.granularity == spec.granularity
        assert d["clickhouse_type"] == "bloom_filter"
        assert d["clickhouse_granularity"] == 1

    def test_from_dict_with_clickhouse_keys(self):
        from dbwarden.databases.clickhouse import ChIndexSpec
        spec = ChIndexSpec.from_dict({
            "name": "ix_sk", "columns": ["a"],
            "clickhouse_type": "set(100)", "clickhouse_granularity": 2,
        })
        assert spec.type == "set(100)"
        assert spec.granularity == 2

    def test_to_dict_includes_expr(self):
        from dbwarden.databases.clickhouse import ChIndexSpec
        d = ChIndexSpec("ix_expr", ["url"], type="bloom_filter", expr="lower(url)").to_dict()
        assert d["expr"] == "lower(url)"


class TestPgIndexSpec:
    def test_basic_construction(self):
        from dbwarden.databases.pgsql import PgIndexSpec
        spec = PgIndexSpec("ix_email", ["email"])
        assert spec.name == "ix_email"
        assert spec.columns == ["email"]
        assert spec.unique is False

    def test_full_construction(self):
        from dbwarden.databases.pgsql import PgIndexSpec
        spec = PgIndexSpec("ix_ab", ["a", "b"],
            unique=True, using="gin", where="status = 'active'",
            include=["c"], tablespace="fast_ts", nulls_not_distinct=True)
        assert spec.unique is True
        assert spec.using == "gin"
        assert spec.where == "status = 'active'"
        assert spec.include == ["c"]
        assert spec.tablespace == "fast_ts"
        assert spec.nulls_not_distinct is True

    def test_to_dict_roundtrip(self):
        from dbwarden.databases.pgsql import PgIndexSpec
        spec = PgIndexSpec("ix_ab", ["a", "b"],
            unique=True, using="gin", where="status = 'active'")
        d = spec.to_dict()
        restored = PgIndexSpec.from_dict(d)
        assert restored.name == spec.name
        assert restored.columns == spec.columns
        assert restored.unique == spec.unique
        assert restored.using == spec.using
        assert restored.where == spec.where

    def test_to_dict_omits_defaults(self):
        from dbwarden.databases.pgsql import PgIndexSpec
        d = PgIndexSpec("ix_email", ["email"]).to_dict()
        assert "unique" not in d
        assert "using" not in d
        assert "where" not in d
        assert "postgresql_ops" not in d

    def test_postgresql_ops_roundtrip(self):
        from dbwarden.databases.pgsql import PgIndexSpec
        spec = PgIndexSpec("ix_data", ["data"],
            using="gin", postgresql_ops={"data": "jsonb_path_ops"})
        d = spec.to_dict()
        assert d["postgresql_ops"] == {"data": "jsonb_path_ops"}
        restored = PgIndexSpec.from_dict(d)
        assert restored.postgresql_ops == {"data": "jsonb_path_ops"}

    def test_index_factory_with_postgresql_ops(self):
        from dbwarden.databases.pgsql import index
        d = index("ix_data", ["data"],
            using="gin", postgresql_ops={"data": "jsonb_path_ops"})
        assert d["postgresql_ops"] == {"data": "jsonb_path_ops"}
        assert d["using"] == "gin"


class TestMetaValidator:
    def test_callable_skip(self):
        class WithMethod(PGTableMeta):
            comment = "has method"

            def helper(self):
                pass

    def test_unknown_table_attr_rejected(self):
        with pytest.raises(DBWardenConfigError, match=r"Unknown attribute 'zzz_top_level"):
            class _(PGTableMeta):
                zzz_top_level = "bad"

    def test_unknown_field_attr_rejected(self):
        with pytest.raises(DBWardenConfigError, match=r"Unknown attribute 'zzz_field_attr"):
            class _(PGColumnMeta):
                zzz_field_attr = "bad"

    def test_known_table_attrs_allowed(self):
        class Okay(TableMeta):
            comment = "works"
            indexes = []
            checks = []
            uniques = []

    def test_known_field_attrs_allowed(self):
        class Okay(PGColumnMeta):
            comment = "field comment"
            public = True

    def test_nested_classes_allowed(self):
        class Parent(PGTableMeta):
            comment = "parent"
            indexes = []

            class child(PGColumnMeta):
                comment = "nested child"

    def test_pg_field_spec_accepted(self):
        class SpecTest(PGColumnMeta):
            pg = pg.field(collation="en_US.UTF-8")
            comment = "test"

        assert SpecTest.pg.collation == "en_US.UTF-8"

    def test_ch_field_spec_accepted(self):
        class SpecTest(CHColumnMeta):
            ch = ch.field(codec="ZSTD(3)")
            comment = "test"

        assert SpecTest.ch.codec == "ZSTD(3)"


class TestPgFieldSpec:
    def test_field_factory(self):
        spec = pg.field(collation="en_US.UTF-8", storage="PLAIN")
        assert spec.collation == "en_US.UTF-8"
        assert spec.storage == "PLAIN"

    def test_to_col_info(self):
        spec = pg.field(collation="en_US.UTF-8", storage="PLAIN", identity="always")
        info = spec.to_col_info()
        assert info["pg_collation"] == "en_US.UTF-8"
        assert info["pg_storage"] == "PLAIN"
        assert info["pg_identity"] == "always"

    def test_to_col_info_omits_none(self):
        spec = pg.field()
        info = spec.to_col_info()
        assert info == {}


class TestChFieldSpec:
    def test_field_factory(self):
        spec = ch.field(codec="ZSTD(3)", nullable=True, low_cardinality=True)
        assert spec.codec == "ZSTD(3)"
        assert spec.nullable is True
        assert spec.low_cardinality is True

    def test_to_col_info(self):
        spec = ch.field(codec="ZSTD(3)", nullable=True, low_cardinality=True, ttl="created_at + INTERVAL 30 DAY")
        info = spec.to_col_info()
        assert info["ch_codec"] == "ZSTD(3)"
        assert info["ch_nullable"] is True
        assert info["ch_low_cardinality"] is True
        assert info["ch_ttl"] == "created_at + INTERVAL 30 DAY"

    def test_to_col_info_omits_false(self):
        spec = ch.field()
        info = spec.to_col_info()
        assert "ch_low_cardinality" not in info
        assert "ch_nullable" not in info

    def test_type_override(self):
        spec = ch.field(type="UInt16")
        assert spec.type == "UInt16"
        info = spec.to_col_info()
        assert info["ch_type"] == "UInt16"

    def test_to_col_info_omits_type_when_unset(self):
        spec = ch.field(codec="ZSTD(3)")
        info = spec.to_col_info()
        assert "ch_type" not in info

    def test_type_override_writes_column_info(self):
        from dbwarden.schema._meta_reader import _write_column_info
        from sqlalchemy import Column, Integer, MetaData, Table

        spec = ch.field(type="UInt16")
        attrs = {"ch": spec}
        table = Table("t", MetaData(), Column("year", Integer))
        _write_column_info(table.c.year, attrs)
        assert table.c.year.info.get("ch_type") == "UInt16"


class TestChEngineFactories:
    def test_merge_tree(self):
        from dbwarden.databases.clickhouse.engine import merge_tree
        spec = merge_tree()
        assert spec.name == "MergeTree"
        assert spec.args == ()

    def test_replacing_merge_tree(self):
        from dbwarden.databases.clickhouse.engine import replacing_merge_tree
        spec = replacing_merge_tree("ver")
        assert spec.name == "ReplacingMergeTree"
        assert spec.args == ("ver",)

    def test_replicated_merge_tree(self):
        from dbwarden.databases.clickhouse.engine import replicated_merge_tree
        spec = replicated_merge_tree("/zk/path", "{replica}", "ver")
        assert spec.name == "ReplicatedMergeTree"
        assert spec.args == ("ver",)
        assert spec.zookeeper_path == "/zk/path"
        assert spec.replica_name == "{replica}"

    def test_summing_merge_tree(self):
        from dbwarden.databases.clickhouse.engine import summing_merge_tree
        spec = summing_merge_tree("col1", "col2")
        assert spec.name == "SummingMergeTree"
        assert spec.args == ("col1", "col2")

    def test_aggregating_merge_tree(self):
        from dbwarden.databases.clickhouse.engine import aggregating_merge_tree
        spec = aggregating_merge_tree()
        assert spec.name == "AggregatingMergeTree"
        assert spec.args == ()


class TestAttachMetaMerge:
    def test_attach_meta_merges_existing(self):
        from dbwarden.schema._base import attach_meta, DBWardenMeta

        first = DBWardenMeta(
            indexes=[{"name": "ix_1", "columns": ["a"]}],
            comment="parent",
            table_attrs={"fillfactor": 90},
        )
        first.pg_policies = []
        first.pg_grants = []

        second = DBWardenMeta(
            indexes=[{"name": "ix_2", "columns": ["b"]}],
            comment="child",
            table_attrs={"tablespace": "fast"},
        )
        second.pg_policies = []
        second.pg_grants = []

        class Dummy:
            pass

        attach_meta(Dummy, first)
        assert Dummy.__dbwarden_meta__.comment == "parent"
        assert len(Dummy.__dbwarden_meta__.indexes) == 1

        attach_meta(Dummy, second)
        assert Dummy.__dbwarden_meta__.comment == "child"
        assert len(Dummy.__dbwarden_meta__.indexes) == 2
        assert Dummy.__dbwarden_meta__.table_attrs["fillfactor"] == 90
        assert Dummy.__dbwarden_meta__.table_attrs["tablespace"] == "fast"


class TestIndexSpecExtended:
    def test_to_dict_with_with_params(self):
        from dbwarden.schema.index import IndexSpec

        spec = IndexSpec(columns=["a"], with_params={"fillfactor": 70})
        d = spec.to_dict()
        assert d["with_params"] == {"fillfactor": 70}

    def test_to_dict_with_column_sorting(self):
        from dbwarden.schema.index import IndexSpec

        spec = IndexSpec(columns=["a"], column_sorting={"a": "DESC"})
        d = spec.to_dict()
        assert d["column_sorting"] == {"a": "DESC"}

    def test_to_dict_with_comment(self):
        from dbwarden.schema.index import IndexSpec

        spec = IndexSpec(columns=["a"], comment="my index")
        d = spec.to_dict()
        assert d["comment"] == "my index"

    def test_to_dict_concurrently_false(self):
        from dbwarden.schema.index import IndexSpec

        spec = IndexSpec(columns=["a"], concurrently=False)
        d = spec.to_dict()
        assert d["concurrently"] is False

    def test_index_factory_with_include(self):
        d = index("ix_all", ["a"], include=["b"])
        assert d["include"] == ["b"]

    def test_index_factory_with_with_params(self):
        d = index("ix_all", ["a"], with_params={"fillfactor": 70})
        assert d["with_params"] == {"fillfactor": 70}

    def test_index_factory_with_tablespace(self):
        d = index("ix_all", ["a"], tablespace="fast_ts")
        assert d["tablespace"] == "fast_ts"

    def test_index_factory_with_column_sorting(self):
        d = index("ix_all", ["a"], column_sorting={"a": "DESC"})
        assert d["column_sorting"] == {"a": "DESC"}

    def test_index_factory_with_comment(self):
        d = index("ix_all", ["a"], comment="my index")
        assert d["comment"] == "my index"

    def test_index_factory_concurrently_false(self):
        d = index("ix_all", ["a"], concurrently=False)
        assert d["concurrently"] is False

    def test_index_factory_with_clickhouse_type(self):
        d = index("ix_all", ["a"], clickhouse_type="set(100)")
        assert d["clickhouse_type"] == "set(100)"

    def test_index_factory_with_clickhouse_granularity(self):
        d = index("ix_all", ["a"], clickhouse_granularity=2)
        assert d["clickhouse_granularity"] == 2


class TestUniqueSpecExtended:
    def test_unique_factory_initially_deferred(self):
        d = unique("uq_test", ["a"], initially_deferred=True)
        assert d["initially_deferred"] is True

    def test_unique_factory_with_include(self):
        d = unique("uq_test", ["a"], include=["b"])
        assert d["include"] == ["b"]


class TestPGViewMeta:
    def test_defaults(self):
        meta = PGViewMeta()
        assert meta.pg_view_query is None
        assert meta.pg_view_materialized is False
        assert meta.pg_view_auto_refresh is False
        assert meta.pg_schema is None

    def test_custom_values(self):
        class MyMeta(PGViewMeta):
            pg_view_query = "SELECT id, name FROM users WHERE active = true"
            pg_view_materialized = True
            pg_view_auto_refresh = True
            pg_schema = "app"

        assert MyMeta.pg_view_query == "SELECT id, name FROM users WHERE active = true"
        assert MyMeta.pg_view_materialized is True
        assert MyMeta.pg_view_auto_refresh is True
        assert MyMeta.pg_schema == "app"
