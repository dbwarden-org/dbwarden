from __future__ import annotations

from dbwarden.engine.core.models import IndexInfo
from dbwarden.engine.snapshot.index_utils import _index_op_from_info, _build_index_name


class TestIndexOpFromInfo:
    def test_minimal(self):
        info = IndexInfo(columns=["id"], unique=False)
        op = _index_op_from_info(info, "users")
        assert op["type"] == "add_index"
        assert op["table"] == "users"
        assert op["columns"] == ["id"]
        assert op["unique"] is False

    def test_with_name(self):
        info = IndexInfo(columns=["id"], unique=False, name="my_idx")
        op = _index_op_from_info(info, "users")
        assert op["index_name"] == "my_idx"

    def test_with_using(self):
        info = IndexInfo(columns=["id"], unique=False, using="hash")
        op = _index_op_from_info(info, "users")
        assert op["using"] == "hash"

    def test_with_where(self):
        info = IndexInfo(columns=["id"], unique=False, where="status > 0")
        op = _index_op_from_info(info, "users")
        assert op["where"] == "status > 0"

    def test_with_include(self):
        info = IndexInfo(columns=["id"], unique=False, include=["email", "name"])
        op = _index_op_from_info(info, "users")
        assert op["include"] == ["email", "name"]

    def test_with_with_params(self):
        info = IndexInfo(columns=["id"], unique=False, with_params={"fillfactor": 90})
        op = _index_op_from_info(info, "users")
        assert op["with_params"] == {"fillfactor": 90}

    def test_with_tablespace(self):
        info = IndexInfo(columns=["id"], unique=False, tablespace="pg_default")
        op = _index_op_from_info(info, "users")
        assert op["tablespace"] == "pg_default"

    def test_with_nulls_not_distinct(self):
        info = IndexInfo(columns=["id"], unique=False, nulls_not_distinct=True)
        op = _index_op_from_info(info, "users")
        assert op["nulls_not_distinct"] is True

    def test_with_column_sorting(self):
        info = IndexInfo(columns=["id"], unique=False, column_sorting={"id": "DESC"})
        op = _index_op_from_info(info, "users")
        assert op["column_sorting"] == {"id": "DESC"}

    def test_with_comment(self):
        info = IndexInfo(columns=["id"], unique=False, comment="my comment")
        op = _index_op_from_info(info, "users")
        assert op["comment"] == "my comment"

    def test_concurrently_false(self):
        info = IndexInfo(columns=["id"], unique=False, concurrently=False)
        op = _index_op_from_info(info, "users")
        assert op["concurrently"] is False

    def test_with_clickhouse_type(self):
        info = IndexInfo(columns=["id"], unique=False, clickhouse_type="MINMAX")
        op = _index_op_from_info(info, "users")
        assert op["clickhouse_type"] == "MINMAX"

    def test_with_clickhouse_granularity(self):
        info = IndexInfo(columns=["id"], unique=False, clickhouse_granularity=2)
        op = _index_op_from_info(info, "users")
        assert op["clickhouse_granularity"] == 2

    def test_with_expression(self):
        info = IndexInfo(columns=["id"], unique=False, expression="(lower(name))")
        op = _index_op_from_info(info, "users")
        assert op["expression"] == "(lower(name))"

    def test_all_fields(self):
        info = IndexInfo(
            columns=["email"],
            unique=True,
            name="uq_users_email",
            using="btree",
            where="deleted_at IS NULL",
            include=["name"],
            with_params={"fillfactor": 90},
            tablespace="pg_default",
            nulls_not_distinct=True,
            column_sorting={"email": "DESC"},
            concurrently=False,
            clickhouse_type=None,
            clickhouse_granularity=None,
            expression=None,
            comment="unique active email index",
        )
        op = _index_op_from_info(info, "users")
        assert op["index_name"] == "uq_users_email"
        assert op["where"] == "deleted_at IS NULL"
        assert op["include"] == ["name"]
        assert op["with_params"] == {"fillfactor": 90}
        assert op["tablespace"] == "pg_default"
        assert op["nulls_not_distinct"] is True
        assert op["column_sorting"] == {"email": "DESC"}
        assert op["concurrently"] is False
        assert op["comment"] == "unique active email index"
        assert "clickhouse_type" not in op
        assert "clickhouse_granularity" not in op
        assert "expression" not in op

    def test_concurrently_default(self):
        info = IndexInfo(columns=["id"], unique=False)
        op = _index_op_from_info(info, "users")
        assert "concurrently" not in op

    def test_nulls_not_distinct_default(self):
        info = IndexInfo(columns=["id"], unique=False)
        op = _index_op_from_info(info, "users")
        assert "nulls_not_distinct" not in op

    def test_with_postgresql_ops(self):
        info = IndexInfo(columns=["id"], unique=False, postgresql_ops={"id": "int4_ops"})
        op = _index_op_from_info(info, "users")
        assert op["postgresql_ops"] == {"id": "int4_ops"}


class TestIndexSig:
    def test_index_info(self):
        from dbwarden.engine.snapshot.index_utils import _index_sig

        info = IndexInfo(
            columns=["id"],
            unique=True,
            using="btree",
            where="active = true",
            include=["name"],
            with_params={"fillfactor": 90},
            tablespace="pg_default",
            nulls_not_distinct=True,
            column_sorting={"id": "DESC"},
            postgresql_ops={"id": "int4_ops"},
            comment="test index",
            expression="(lower(name))",
        )
        sig = _index_sig(info)
        assert isinstance(sig, tuple)
        assert sig[0] == ("id",)
        assert sig[1] is True
        assert sig[2] == "btree"
        assert sig[3] == "active = true"
        assert sig[4] == ("name",)
        assert sig[5] == (("fillfactor", 90),)
        assert sig[6] == "pg_default"
        assert sig[7] is True
        assert sig[8] == (("id", "DESC"),)
        assert sig[9] is None
        assert sig[10] is None
        assert sig[11] == (("id", "int4_ops"),)
        assert sig[12] == "test index"
        assert sig[13] == "(lower(name))"

    def test_dict(self):
        from dbwarden.engine.snapshot.index_utils import _index_sig

        sig = _index_sig({
            "columns": ["id"],
            "unique": True,
            "using": "btree",
            "where": "active = true",
        })
        assert isinstance(sig, tuple)
        assert sig[0] == ("id",)
        assert sig[1] is True
        assert sig[2] == "btree"
        assert sig[3] == "active = true"


class TestRenameTableSql:
    def test_clickhouse_backend(self):
        from dbwarden.engine.snapshot.index_utils import _rename_table_sql
        from types import SimpleNamespace

        intent = SimpleNamespace(old_table="a", new_table="b")
        stmt = _rename_table_sql(intent, "clickhouse")
        assert stmt.upgrade_sql == "RENAME TABLE a TO b;"
        assert stmt.rollback_sql == "RENAME TABLE b TO a;"

    def test_generic_backend(self):
        from dbwarden.engine.snapshot.index_utils import _rename_table_sql
        from types import SimpleNamespace

        intent = SimpleNamespace(old_table="a", new_table="b")
        stmt = _rename_table_sql(intent, "postgresql")
        assert stmt.upgrade_sql == "ALTER TABLE a RENAME TO b;"
        assert stmt.rollback_sql == "ALTER TABLE b RENAME TO a;"


class TestBuildIndexName:
    def test_non_unique(self):
        result = _build_index_name("users", ["id"], unique=False)
        assert result == "idx_users_id"

    def test_unique(self):
        result = _build_index_name("users", ["email"], unique=True)
        assert result == "uq_users_email"

    def test_multiple_columns(self):
        result = _build_index_name("users", ["a", "b"], unique=False)
        assert result == "idx_users_a_b"

    def test_expression(self):
        result = _build_index_name("users", ["id"], unique=False, expression="(lower(email))")
        assert result == "idx_users_expr"

    def test_unique_expression(self):
        result = _build_index_name("users", ["id"], unique=True, expression="(lower(email))")
        assert result == "uq_users_expr"

    def test_using_non_btree(self):
        result = _build_index_name("users", ["id"], unique=False, using="hash")
        assert result == "idx_users_id_hash"

    def test_using_btree(self):
        result = _build_index_name("users", ["id"], unique=False, using="btree")
        assert result == "idx_users_id"

    def test_expression_with_using(self):
        result = _build_index_name("users", ["id"], unique=True, using="hash", expression="(lower(email))")
        assert result == "uq_users_expr_hash"
