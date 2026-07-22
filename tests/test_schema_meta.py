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
