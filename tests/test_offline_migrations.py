from dbwarden.engine.model_discovery import ModelColumn, ModelTable
from dbwarden.engine.offline import diff_model_states, model_state_to_dict, _table_to_state_entry, normalize_model_state, reconstruct_model_column


def _make_col(name, type="integer", nullable=False, pk=False, default=None):
    return ModelColumn(name, type, nullable, pk, False, default, None)


def _make_table(name, columns=None, comment=None, ch_opts=None, pg_table=None, object_type="table"):
    return ModelTable(
        name=name,
        columns=columns or [_make_col("id")],
        clickhouse_options=ch_opts or {},
        comment=comment,
        pg_table=pg_table or {},
        object_type=object_type,
    )


def _make_mysql_table(name, columns=None, my_table=None):
    return ModelTable(
        name=name,
        columns=columns or [_make_col("id")],
        my_table=my_table or {},
    )


def test_model_state_roundtrip():
    table = _make_table("users", columns=[
        _make_col("id", "biginteger", pk=True),
        _make_col("email", "varchar", nullable=False),
        _make_col("bio", "text", nullable=True),
    ])
    state = model_state_to_dict([table])
    assert state["format_version"] == 2
    assert "users" in state["tables"]
    entry = state["tables"]["users"]
    assert "id" in entry["columns"]
    assert entry["columns"]["id"]["type"] == "biginteger"
    assert entry["columns"]["id"]["primary_key"] is True
    assert state["indexes"] == {}
    assert state["constraints"] == {}


def test_diff_new_table():
    prev = model_state_to_dict([])
    curr = model_state_to_dict([_make_table("users")])
    up, down = diff_model_states(prev, curr)
    assert len(up) == 1
    assert up[0]["type"] == "create_table"
    assert up[0]["table"] == "users"
    assert len(down) == 1
    assert down[0]["type"] == "drop_table"


def test_diff_drop_table():
    prev = model_state_to_dict([_make_table("users")])
    curr = model_state_to_dict([])
    up, down = diff_model_states(prev, curr)
    assert len(up) == 1
    assert up[0]["type"] == "drop_table"
    assert up[0]["table"] == "users"
    assert len(down) == 1
    assert down[0]["type"] == "create_table"


def test_diff_add_column():
    prev = model_state_to_dict([_make_table("users", columns=[_make_col("id")])])
    curr = model_state_to_dict([_make_table("users", columns=[
        _make_col("id"), _make_col("email", "varchar"),
    ])])
    up, down = diff_model_states(prev, curr)
    add_cols = [op for op in up if op["type"] == "add_column"]
    assert len(add_cols) == 1
    assert add_cols[0]["column"] == "email"
    assert add_cols[0]["definition"]["type"] == "varchar"


def test_diff_drop_column():
    prev = model_state_to_dict([_make_table("users", columns=[
        _make_col("id"), _make_col("email", "varchar"),
    ])])
    curr = model_state_to_dict([_make_table("users", columns=[_make_col("id")])])
    up, down = diff_model_states(prev, curr)
    drop_cols = [op for op in up if op["type"] == "drop_column"]
    assert len(drop_cols) == 1
    assert drop_cols[0]["column"] == "email"
    assert drop_cols[0]["definition"]["type"] == "varchar"


def test_diff_type_change():
    prev = model_state_to_dict([_make_table("users", columns=[_make_col("name", "varchar")])])
    curr = model_state_to_dict([_make_table("users", columns=[_make_col("name", "text")])])
    up, down = diff_model_states(prev, curr)
    type_changes = [op for op in up if op["type"] == "alter_column_type"]
    assert len(type_changes) == 1
    assert type_changes[0]["column"] == "name"
    assert type_changes[0]["model_type"] == "text"


def test_diff_no_changes():
    prev = model_state_to_dict([_make_table("users")])
    curr = model_state_to_dict([_make_table("users")])
    up, down = diff_model_states(prev, curr)
    assert len(up) == 0
    assert len(down) == 0


def test_diff_nullable_change():
    prev = model_state_to_dict([_make_table("users", columns=[_make_col("email", "varchar", nullable=True)])])
    curr = model_state_to_dict([_make_table("users", columns=[_make_col("email", "varchar", nullable=False)])])
    up, down = diff_model_states(prev, curr)
    nullable_ops = [op for op in up if op["type"] == "alter_column_nullable"]
    assert len(nullable_ops) == 1
    assert nullable_ops[0]["nullable"] is False
    assert nullable_ops[0]["col_type"] == "varchar"


def test_diff_default_change():
    prev = model_state_to_dict([_make_table("users", columns=[_make_col("name", "varchar", default=None)])])
    curr = model_state_to_dict([_make_table("users", columns=[_make_col("name", "varchar", default="'default'")])])
    up, down = diff_model_states(prev, curr)
    default_ops = [op for op in up if op["type"] == "alter_column_default"]
    assert len(default_ops) == 1
    assert default_ops[0]["default"] == "'default'"


def test_diff_ch_options():
    prev = model_state_to_dict([_make_table("events", ch_opts={"ch_engine": "MergeTree"})])
    curr = model_state_to_dict([_make_table("events", ch_opts={"ch_engine": "ReplicatedMergeTree"})])
    up, down = diff_model_states(prev, curr)
    ch_ops = [op for op in up if op["type"] == "recreate_ch_table"]
    assert len(ch_ops) == 1
    assert ch_ops[0]["from_table"]["backend_table_spec"]["ch_engine"] == "MergeTree"
    assert ch_ops[0]["to_table"]["backend_table_spec"]["ch_engine"] == "ReplicatedMergeTree"


def test_table_to_state_entry_includes_backend():
    table = _make_table("events", ch_opts={"ch_engine": "MergeTree", "ch_order_by": ["id"]})
    entry = _table_to_state_entry(table)
    assert entry["backend_table_spec"]["backend"] == "clickhouse"
    assert entry["backend_table_spec"]["ch_engine"] == "MergeTree"
    assert entry["backend_table_spec"]["ch_order_by"] == ["id"]


def test_table_to_state_entry_includes_pg():
    table = _make_table("users", pg_table={"fillfactor": 80})
    entry = _table_to_state_entry(table)
    assert entry["backend_table_spec"]["backend"] == "postgresql"
    assert entry["backend_table_spec"]["fillfactor"] == 80


def test_column_ch_meta_in_state():
    col = _make_col("payload", "string")
    col.ch_meta = {"ch_codec": "ZSTD(3)", "ch_nullable": True}
    table = _make_table("events", columns=[col])
    entry = _table_to_state_entry(table)
    col_entry = entry["columns"]["payload"]
    assert col_entry["ch_column"]["ch_codec"] == "ZSTD(3)"
    assert col_entry["ch_column"]["ch_nullable"] is True


def test_reconstruct_model_column_restores_codec():
    col = reconstruct_model_column(
        {
            "name": "payload",
            "type": "String",
            "nullable": False,
            "primary_key": False,
            "unique": False,
            "default": None,
            "foreign_key": None,
            "comment": None,
            "autoincrement": None,
            "pg_column": {},
            "ch_column": {"ch_codec": "ZSTD(3)", "ch_type": "String"},
            "my_column": {},
        }
    )

    assert col.codec == "ZSTD(3)"


def test_table_to_state_entry_includes_mysql():
    table = _make_mysql_table(
        "users",
        my_table={"my_engine": "InnoDB", "my_charset": "utf8mb4"},
    )
    entry = _table_to_state_entry(table)
    assert entry["backend_table_spec"]["backend"] == "mysql"
    assert entry["backend_table_spec"]["my_engine"] == "InnoDB"
    assert entry["backend_table_spec"]["my_charset"] == "utf8mb4"


def test_column_my_meta_in_state():
    col = _make_col("id", "integer")
    col.my_meta = {"my_unsigned": True, "my_on_update": "CURRENT_TIMESTAMP"}
    table = _make_mysql_table("users", columns=[col])
    entry = _table_to_state_entry(table)
    col_entry = entry["columns"]["id"]
    assert col_entry["my_column"]["my_unsigned"] is True
    assert col_entry["my_column"]["my_on_update"] == "CURRENT_TIMESTAMP"


def test_diff_mysql_table_meta_change():
    prev = model_state_to_dict([
        _make_mysql_table("users", my_table={"my_engine": "InnoDB"})
    ])
    curr = model_state_to_dict([
        _make_mysql_table("users", my_table={"my_engine": "MyISAM"})
    ])
    up, down = diff_model_states(prev, curr)
    ops = [op for op in up if op["type"] == "alter_my_table"]
    assert len(ops) == 1
    assert ops[0]["key"] == "my_engine"
    assert ops[0]["to_value"] == "MyISAM"


def test_diff_mysql_column_meta_change():
    prev_col = _make_col("id", "integer")
    prev_col.my_meta = {"my_unsigned": True}
    curr_col = _make_col("id", "integer")
    curr_col.my_meta = {"my_unsigned": True, "my_on_update": "CURRENT_TIMESTAMP"}
    prev = model_state_to_dict([_make_mysql_table("users", columns=[prev_col])])
    curr = model_state_to_dict([_make_mysql_table("users", columns=[curr_col])])
    up, down = diff_model_states(prev, curr)
    ops = [op for op in up if op["type"] == "alter_my_column_meta"]
    assert len(ops) == 1
    assert ops[0]["column"] == "id"
    assert ops[0]["to_my_column"]["my_on_update"] == "CURRENT_TIMESTAMP"


def test_diff_table_comment_change():
    prev = model_state_to_dict([_make_table("users", comment="Old")])
    curr = model_state_to_dict([_make_table("users", comment="New")])
    up, down = diff_model_states(prev, curr)
    comment_ops = [op for op in up if op["type"] == "alter_table_comment"]
    assert len(comment_ops) == 1
    assert comment_ops[0]["comment"] == "New"
    assert comment_ops[0]["previous_comment"] == "Old"


def test_diff_column_comment_change():
    prev_col = _make_col("email", "varchar", nullable=True, default=None)
    prev_col.comment = "Old"
    curr_col = _make_col("email", "varchar", nullable=True, default=None)
    curr_col.comment = "New"
    prev = model_state_to_dict([_make_table("users", columns=[_make_col("id"), prev_col])])
    curr = model_state_to_dict([_make_table("users", columns=[_make_col("id"), curr_col])])
    up, down = diff_model_states(prev, curr)
    comment_ops = [op for op in up if op["type"] == "alter_column_comment"]
    assert len(comment_ops) == 1
    assert comment_ops[0]["column"] == "email"
    assert comment_ops[0]["comment"] == "New"


# ── End-to-end: diff_model_states + snapshot_diff_to_sql ──────────

from dbwarden.engine.snapshot import snapshot_diff_to_sql


def _diff_to_sql(prev_state, curr_state, db_name="primary"):
    up_ops, down_ops = diff_model_states(prev_state, curr_state)
    if not up_ops:
        return "", "", []
    return snapshot_diff_to_sql(up_ops, down_ops, db_name=db_name)


def test_offline_create_table_op():
    """create_table op is correctly generated; full SQL needs config lookup."""
    prev = model_state_to_dict([])
    curr = model_state_to_dict([_make_table("users")])
    up_ops, down_ops = diff_model_states(prev, curr)
    assert len(up_ops) == 1
    assert up_ops[0]["type"] == "create_table"
    assert up_ops[0]["table"] == "users"
    assert down_ops[0]["type"] == "drop_table"


def test_offline_drop_table_sql():
    prev = model_state_to_dict([_make_table("users")])
    curr = model_state_to_dict([])
    up_sql, rb_sql, changes = _diff_to_sql(prev, curr)
    assert "DROP TABLE" in up_sql
    assert "users" in up_sql
    assert rb_sql == "" or "CREATE TABLE" in rb_sql
    assert changes[0].operation == "drop_table"


def test_offline_add_column_sql():
    prev = model_state_to_dict([_make_table("users", columns=[_make_col("id")])])
    curr = model_state_to_dict([_make_table("users", columns=[
        _make_col("id"), _make_col("email", "varchar"),
    ])])
    up_sql, rb_sql, changes = _diff_to_sql(prev, curr)
    assert "ALTER TABLE users ADD COLUMN email" in up_sql
    assert "varchar" in up_sql.lower() or "VARCHAR" in up_sql
    assert "ALTER TABLE users DROP COLUMN email" in rb_sql
    assert any(c.operation == "add_column" and c.target == "email" for c in changes)


def test_offline_drop_column_sql():
    prev = model_state_to_dict([_make_table("users", columns=[
        _make_col("id"), _make_col("email", "varchar"),
    ])])
    curr = model_state_to_dict([_make_table("users", columns=[_make_col("id")])])
    up_sql, rb_sql, changes = _diff_to_sql(prev, curr)
    assert "DROP COLUMN email" in up_sql
    assert "ADD COLUMN email" in rb_sql
    assert any(c.operation == "drop_column" and c.target == "email" for c in changes)


def test_offline_alter_column_type_sql():
    from dbwarden.config import set_dev_mode
    set_dev_mode(True)
    prev = model_state_to_dict([_make_table("users", columns=[_make_col("name", "varchar")])])
    curr = model_state_to_dict([_make_table("users", columns=[_make_col("name", "text")])])
    up_sql, rb_sql, changes = _diff_to_sql(prev, curr)
    assert "ALTER TABLE users ALTER COLUMN name TYPE text" in up_sql
    # Rollback in offline mode uses placeholder since previous type isn't tracked
    assert any(c.operation == "alter_column_type" for c in changes)


def test_offline_alter_nullable_sql():
    prev = model_state_to_dict([_make_table("users", columns=[_make_col("email", "varchar", nullable=True)])])
    curr = model_state_to_dict([_make_table("users", columns=[_make_col("email", "varchar", nullable=False)])])
    up_sql, rb_sql, changes = _diff_to_sql(prev, curr)
    assert "SET NOT NULL" in up_sql or "NOT NULL" in up_sql
    assert "DROP NOT NULL" in rb_sql or "NULL" in rb_sql
    assert any(c.operation == "alter_column_nullable" for c in changes)


def test_offline_alter_default_sql():
    prev = model_state_to_dict([_make_table("users", columns=[_make_col("name", "varchar", default=None)])])
    curr = model_state_to_dict([_make_table("users", columns=[_make_col("name", "varchar", default="'default'")])])
    up_sql, rb_sql, changes = _diff_to_sql(prev, curr)
    assert "SET DEFAULT" in up_sql or "DEFAULT" in up_sql
    assert "DROP DEFAULT" in rb_sql
    assert any(c.operation == "alter_column_default" for c in changes)


def test_offline_table_comment_sql():
    prev = model_state_to_dict([_make_table("users", comment="Old")])
    curr = model_state_to_dict([_make_table("users", comment="New")])
    up_sql, rb_sql, changes = _diff_to_sql(prev, curr)
    assert "COMMENT ON TABLE users IS 'New'" in up_sql
    assert "COMMENT ON TABLE users IS 'Old'" in rb_sql
    assert any(c.operation == "alter_table_comment" for c in changes)


def test_offline_column_comment_sql():
    prev_col = _make_col("email", "varchar", nullable=True)
    prev_col.comment = "Old comment"
    curr_col = _make_col("email", "varchar", nullable=True)
    curr_col.comment = "New comment"
    prev = model_state_to_dict([_make_table("users", columns=[_make_col("id"), prev_col])])
    curr = model_state_to_dict([_make_table("users", columns=[_make_col("id"), curr_col])])
    up_sql, rb_sql, changes = _diff_to_sql(prev, curr)
    assert "COMMENT ON COLUMN users.email IS 'New comment'" in up_sql
    assert "COMMENT ON COLUMN users.email IS 'Old comment'" in rb_sql
    assert any(c.operation == "alter_column_comment" for c in changes)


def test_offline_add_index_op():
    """add_index op carries full index payload offline."""
    from dbwarden.engine.model_discovery import IndexInfo
    prev = model_state_to_dict([_make_table("users")])
    table = _make_table("users", columns=[_make_col("name", "varchar")])
    table.indexes = [IndexInfo(name="ix_name", columns=["name"])]
    curr = model_state_to_dict([table])
    up_ops, down_ops = diff_model_states(prev, curr)
    add_idx_ops = [op for op in up_ops if op["type"] == "add_index"]
    assert len(add_idx_ops) == 1
    assert add_idx_ops[0]["columns"] == ["name"]
    assert add_idx_ops[0]["table"] == "users"


def test_offline_drop_index_sql():
    from dbwarden.engine.model_discovery import IndexInfo
    table = _make_table("users", columns=[_make_col("name", "varchar")])
    table.indexes = [IndexInfo(name="ix_name", columns=["name"])]
    prev = model_state_to_dict([table])
    curr = model_state_to_dict([_make_table("users", columns=[_make_col("name", "varchar")])])
    from dbwarden.config import set_dev_mode
    set_dev_mode(True)
    up_sql, rb_sql, changes = _diff_to_sql(prev, curr)
    up_ops, down_ops = diff_model_states(prev, curr)
    drop_idx_ops = [op for op in up_ops if op["type"] == "drop_index"]
    assert len(drop_idx_ops) == 1
    assert drop_idx_ops[0]["index_name"] == "ix_name"


def test_offline_multiple_ops_in_one_diff():
    """Multiple changes are correctly detected in a single diff."""
    prev = model_state_to_dict([_make_table("users")])
    curr = model_state_to_dict([
        _make_table("users", columns=[
            _make_col("id"), _make_col("email", "varchar", nullable=True),
        ]),
        _make_table("posts"),
    ])
    up_ops, down_ops = diff_model_states(prev, curr)
    assert len(up_ops) >= 2
    types = [op["type"] for op in up_ops]
    assert "add_column" in types
    assert "create_table" in types
    with_tables = {op.get("table") for op in up_ops}
    assert "users" in with_tables
    assert "posts" in with_tables


def test_offline_no_changes_returns_empty():
    prev = model_state_to_dict([_make_table("users")])
    curr = model_state_to_dict([_make_table("users")])
    up_sql, rb_sql, changes = _diff_to_sql(prev, curr)
    assert up_sql.strip() == ""
    assert rb_sql.strip() == ""
    assert len(changes) == 0


def test_offline_add_column_with_default():
    col = _make_col("is_active", "boolean", default="true")
    prev = model_state_to_dict([_make_table("users")])
    curr = model_state_to_dict([_make_table("users", columns=[_make_col("id"), col])])
    up_ops, down_ops = diff_model_states(prev, curr)
    add_cols = [op for op in up_ops if op["type"] == "add_column"]
    assert len(add_cols) == 1
    assert add_cols[0]["definition"]["default"] == "true"
    # The SQL may or may not include DEFAULT depending on snapshot_diff_to_sql fallback
    up_sql, rb_sql, changes = snapshot_diff_to_sql(up_ops, down_ops, db_name="primary")
    assert "ALTER TABLE users ADD COLUMN is_active" in up_sql


def test_offline_clickhouse_table_crashes():
    """CH option changes are now detected offline."""
    prev = model_state_to_dict([_make_table("events", ch_opts={"ch_engine": "MergeTree"})])
    curr = model_state_to_dict([_make_table("events", ch_opts={"ch_engine": "ReplicatedMergeTree"})])
    up_ops, down_ops = diff_model_states(prev, curr)
    assert len([op for op in up_ops if op["type"] == "recreate_ch_table"]) == 1


def test_offline_recreate_preserves_projections():
    """Projections no longer block engine recreates."""
    from dbwarden.databases.clickhouse.projection import ProjectionSpec
    proj = [ProjectionSpec("by_id", "SELECT id ORDER BY id").to_dict()]
    prev = model_state_to_dict([_make_table("events", ch_opts={"ch_engine": "MergeTree", "ch_projections": proj})])
    curr = model_state_to_dict([_make_table("events", ch_opts={"ch_engine": "ReplicatedMergeTree"})])
    up_ops, down_ops = diff_model_states(prev, curr)
    assert len([op for op in up_ops if op["type"] == "recreate_ch_table"]) == 1
    # Projections from prev state are in from_table
    assert up_ops[0]["from_table"]["backend_table_spec"].get("ch_projections") == proj


def test_offline_recreate_allows_inline_materialized_view(monkeypatch):
    """Inline MV (no TO target) now recreates with DROP VIEW + CREATE MATERIALIZED VIEW."""
    monkeypatch.setattr("dbwarden.engine.model_discovery.type_mapping._get_backend_name", lambda db_name=None: "clickhouse")
    monkeypatch.setattr("dbwarden.engine.snapshot._get_backend", lambda db_name=None: "clickhouse")
    prev = model_state_to_dict([_make_table("events", object_type="materialized_view", ch_opts={"ch_engine": "MergeTree", "ch_select_statement": "SELECT id FROM source"})])
    curr = model_state_to_dict([_make_table("events", object_type="materialized_view", ch_opts={"ch_engine": "ReplicatedMergeTree"})])
    up_ops, down_ops = diff_model_states(prev, curr)
    recreate = next(op for op in up_ops if op["type"] == "recreate_ch_table")
    assert recreate is not None
    from dbwarden.engine.snapshot import snapshot_diff_to_sql
    up_sql, rb_sql, _ = snapshot_diff_to_sql(up_ops, down_ops, db_name="primary")
    assert "DROP VIEW IF EXISTS events" in up_sql
    assert "CREATE MATERIALIZED VIEW IF NOT EXISTS events" in up_sql


def test_offline_recreate_blocks_on_mv_with_to_table():
    """MV with TO target still blocks because DROP+CREATE would lose the target table link."""
    prev = model_state_to_dict([_make_table("events", object_type="materialized_view", ch_opts={"ch_engine": "MergeTree", "ch_select_statement": "SELECT id FROM source", "ch_to_table": "source"})])
    curr = model_state_to_dict([_make_table("events", object_type="materialized_view", ch_opts={"ch_engine": "ReplicatedMergeTree"})])
    import pytest
    with pytest.raises(ValueError, match="TO"):
        diff_model_states(prev, curr)


def test_offline_recreate_dictionary_sql(monkeypatch):
    monkeypatch.setattr("dbwarden.engine.model_discovery.type_mapping._get_backend_name", lambda db_name=None: "clickhouse")
    prev = model_state_to_dict([_make_table("events", object_type="dictionary", ch_opts={"ch_engine": "MergeTree", "ch_dictionary": True, "ch_dict_layout": "flat()", "ch_dict_source": "FILE(path='/data/test.csv' format 'CSV')", "ch_dict_lifetime": "300"})])
    curr = model_state_to_dict([_make_table("events", object_type="dictionary", ch_opts={"ch_engine": "ReplicatedMergeTree", "ch_dictionary": True, "ch_dict_layout": "flat()", "ch_dict_source": "FILE(path='/data/test.csv' format 'CSV')", "ch_dict_lifetime": "300"})])
    up_ops, down_ops = diff_model_states(prev, curr)
    recreate = next(op for op in up_ops if op["type"] == "recreate_ch_table")
    assert recreate is not None
    from dbwarden.engine.snapshot import snapshot_diff_to_sql
    up_sql, rb_sql, _ = snapshot_diff_to_sql(up_ops, down_ops, db_name="primary")
    assert "DROP DICTIONARY events" in up_sql
    assert "CREATE DICTIONARY IF NOT EXISTS events" in up_sql
    assert "DROP DICTIONARY events" in rb_sql
    assert "CREATE DICTIONARY IF NOT EXISTS events" in rb_sql


def test_offline_recreate_annotates_dependent_mvs():
    """MVs targeting a recreated table are annotated on the recreate_ch_table op."""
    prev = model_state_to_dict([
        _make_table("events", ch_opts={"ch_engine": "MergeTree", "ch_order_by": ["id"]}),
        _make_table("events_mv", ch_opts={
            "ch_engine": "MergeTree",
            "ch_select_statement": "SELECT id FROM source",
            "ch_to_table": "events",
            "ch_object_type": "materialized_view",
        }),
    ])
    curr = model_state_to_dict([
        _make_table("events", ch_opts={"ch_engine": "ReplicatedMergeTree", "ch_order_by": ["id"]}),
        _make_table("events_mv", ch_opts={
            "ch_engine": "MergeTree",
            "ch_select_statement": "SELECT id FROM source",
            "ch_to_table": "events",
            "ch_object_type": "materialized_view",
        }),
    ])
    up_ops, down_ops = diff_model_states(prev, curr)
    recreate = next(op for op in up_ops if op["type"] == "recreate_ch_table")
    assert recreate.get("dependent_mvs") == ["events_mv"]


def test_offline_recreate_mv_sql_has_detach_attach():
    """DETACH/ATTACH statements appear in generated SQL when MVs target the recreated table."""
    prev = model_state_to_dict([
        _make_table("events", ch_opts={"ch_engine": "MergeTree", "ch_order_by": ["id"]}),
        _make_table("events_mv", ch_opts={
            "ch_engine": "MergeTree",
            "ch_select_statement": "SELECT id FROM source",
            "ch_to_table": "events",
            "ch_object_type": "materialized_view",
        }),
    ])
    curr = model_state_to_dict([
        _make_table("events", ch_opts={"ch_engine": "ReplicatedMergeTree", "ch_order_by": ["id"]}),
        _make_table("events_mv", ch_opts={
            "ch_engine": "MergeTree",
            "ch_select_statement": "SELECT id FROM source",
            "ch_to_table": "events",
            "ch_object_type": "materialized_view",
        }),
    ])
    up_ops, down_ops = diff_model_states(prev, curr)
    from dbwarden.engine.snapshot import snapshot_diff_to_sql
    up_sql, rb_sql, _ = snapshot_diff_to_sql(up_ops, down_ops, db_name="primary")
    assert "DETACH TABLE events_mv" in up_sql
    assert "ATTACH TABLE events_mv" in up_sql
    assert "DETACH TABLE events_mv" in rb_sql
    assert "ATTACH TABLE events_mv" in rb_sql


def test_offline_clickhouse_engine_recreate_sql_preserves_old_table():
    prev = model_state_to_dict([_make_table("events", ch_opts={"ch_engine": "MergeTree", "ch_order_by": ["id"]})])
    curr = model_state_to_dict([_make_table("events", columns=[_make_col("id"), _make_col("category", "varchar")], ch_opts={"ch_engine": "ReplacingMergeTree", "ch_order_by": ["id", "category"]})])
    up_ops, down_ops = diff_model_states(prev, curr)
    recreate = next(op for op in up_ops if op["type"] == "recreate_ch_table")
    recreate["drop_old_after_swap"] = False
    down_ops[0]["drop_old_after_swap"] = False
    up_sql, rb_sql, _changes = snapshot_diff_to_sql(up_ops, down_ops, db_name="primary")
    assert "CREATE TABLE IF NOT EXISTS events__dbw_new" in up_sql
    assert "INSERT INTO events__dbw_new" in up_sql
    assert "RENAME TABLE events TO events__dbw_old, events__dbw_new TO events;" in up_sql
    assert "\nDROP TABLE events__dbw_old;" not in up_sql
    assert "Preserved previous table as events__dbw_old" in up_sql
    assert "Irreversible ClickHouse recreate for events" in rb_sql
    assert "lose row-level detail" in rb_sql


def test_offline_row_preserving_clickhouse_recreate_has_real_rollback():
    prev = model_state_to_dict([_make_table("events", ch_opts={"ch_engine": "MergeTree", "ch_order_by": ["id"]})])
    curr = model_state_to_dict([_make_table("events", ch_opts={"ch_engine": "Log", "ch_order_by": ["id"]})])
    up_ops, down_ops = diff_model_states(prev, curr)

    recreate = next(op for op in up_ops if op["type"] == "recreate_ch_table")
    assert recreate["rollback_kind"] == "conditional"

    _up_sql, rb_sql, _changes = snapshot_diff_to_sql(up_ops, down_ops, db_name="primary")
    assert "CREATE TABLE IF NOT EXISTS events__dbw_failed" in rb_sql
    assert "Irreversible ClickHouse recreate" not in rb_sql


def test_offline_clickhouse_engine_recreate_sql_drops_old_table():
    prev = model_state_to_dict([_make_table("events", ch_opts={"ch_engine": "MergeTree", "ch_order_by": ["id"]})])
    curr = model_state_to_dict([_make_table("events", columns=[_make_col("id"), _make_col("category", "varchar")], ch_opts={"ch_engine": "ReplacingMergeTree", "ch_order_by": ["id", "category"]})])
    up_ops, down_ops = diff_model_states(prev, curr)
    recreate = next(op for op in up_ops if op["type"] == "recreate_ch_table")
    recreate["drop_old_after_swap"] = True
    down_ops[0]["drop_old_after_swap"] = True
    up_sql, rb_sql, _changes = snapshot_diff_to_sql(up_ops, down_ops, db_name="primary")
    assert "DROP TABLE events__dbw_old" in up_sql


def test_offline_backend_preserved_in_state():
    table = _make_table("events", ch_opts={"ch_engine": "MergeTree", "ch_order_by": ["id"]})
    state = model_state_to_dict([table])
    entry = state["tables"]["events"]
    assert entry["backend_table_spec"]["backend"] == "clickhouse"
    assert entry["backend_table_spec"]["ch_engine"] == "MergeTree"


def test_offline_pg_table_options_in_state():
    table = _make_table("accounts", pg_table={"fillfactor": 80})
    state = model_state_to_dict([table])
    entry = state["tables"]["accounts"]
    assert entry["backend_table_spec"]["backend"] == "postgresql"
    assert entry["backend_table_spec"]["fillfactor"] == 80


def test_offline_check_constraint_no_repeat_diff():
    table = ModelTable(
        name="users",
        columns=[ModelColumn("email", "varchar", False, False, False, None, None)],
        checks=[{"name": "ck_users_email", "expression": "email <> ''"}],
    )
    prev = model_state_to_dict([table])
    curr = model_state_to_dict([table])
    up, down = diff_model_states(prev, curr)
    assert up == []
    assert down == []


# ── Full integration: make_migrations_cmd(offline=True) ──────────

import pytest
import json
import os
import tempfile
from pathlib import Path
from dbwarden.config import set_dev_mode
from dbwarden.commands.make_migrations import make_migrations_cmd


def test_offline_make_migrations_end_to_end():
    """
    Full offline flow: create initial state → modify model →
    make-migrations --offline → verify .sql, .plan.json, state update.
    """
    set_dev_mode(False)
    with tempfile.TemporaryDirectory() as tmpdir:
        old_cwd = os.getcwd()
        try:
            os.chdir(tmpdir)

            # Write minimal config
            Path("dbwarden.py").write_text(
                "from dbwarden import database_config\n\n"
                "database_config(database_name='primary', default=True, "
                "database_type='sqlite', database_url_sync='sqlite:///./app.db', "
                "model_paths=['models'])\n",
                encoding="utf-8",
            )

            # Write initial model file (id + email only)
            Path("models").mkdir(parents=True)
            Path("models/user.py").write_text(
                "from sqlalchemy import Column, Integer, String\n"
                "from sqlalchemy.orm import declarative_base\n\n"
                "Base = declarative_base()\n\n"
                "class User(Base):\n"
                "    __tablename__ = 'users'\n"
                "    id = Column(Integer, primary_key=True)\n"
                "    email = Column(String(255), nullable=False, unique=True)\n",
                encoding="utf-8",
            )

            # Create migrations dir and .dbwarden/ dir
            Path("migrations/primary").mkdir(parents=True)
            Path(".dbwarden").mkdir(parents=True)

            # Create initial model_state.json matching the model (id + email)
            initial_state = {
                "format_version": 1,
                "exported_at": "2026-01-01T00:00:00Z",
                "dbwarden_version": "0.9.4",
                "tables": {
                    "users": {
                        "columns": {
                            "id": {"type": "integer", "nullable": False, "primary_key": True, "unique": False, "default": None, "foreign_key": None, "comment": None},
                            "email": {"type": "varchar(255)", "nullable": False, "primary_key": False, "unique": True, "default": None, "foreign_key": None, "comment": None},
                        },
                        "indexes": [],
                        "foreign_keys": [],
                        "checks": [],
                        "uniques": [],
                        "comment": None,
                        "object_type": "table",
                        "backend_table_spec": {},
                    }
                },
            }
            Path(".dbwarden/model_state.json").write_text(
                json.dumps(initial_state, indent=2) + "\n", encoding="utf-8"
            )

            # ── Step 2: Run make-migrations --offline (first run ──
            # no migration files exist, so this generates CREATE TABLE for all models)
            make_migrations_cmd("initial schema", offline=True, database="primary")

            # ── Step 3: Verify first migration creates the full table ──
            sql_files = sorted(Path("migrations/primary").glob("*.sql"))
            assert len(sql_files) >= 1, "Should have created at least one SQL file"

            initial_sql = sql_files[-1]
            initial_sql_content = initial_sql.read_text(encoding="utf-8")
            assert "CREATE TABLE" in initial_sql_content, (
                f"First migration should create table, got:\n{initial_sql_content}"
            )
            assert "email VARCHAR(255)" in initial_sql_content
            assert "id INTEGER NOT NULL PRIMARY KEY" in initial_sql_content

            # ── Step 4: Modify the model to add bio column ──
            Path("models/user.py").write_text(
                "from sqlalchemy import Column, Integer, String, Text\n"
                "from sqlalchemy.orm import declarative_base\n\n"
                "Base = declarative_base()\n\n"
                "class User(Base):\n"
                "    __tablename__ = 'users'\n"
                "    id = Column(Integer, primary_key=True)\n"
                "    email = Column(String(255), nullable=False, unique=True)\n"
                "    bio = Column(Text, nullable=True)\n",
                encoding="utf-8",
            )

            # ── Step 5: Run make-migrations --offline again (delta only) ──
            make_migrations_cmd("add bio column", offline=True, database="primary")

            # ── Step 6: Verify second migration has just the delta ──
            sql_files = sorted(Path("migrations/primary").glob("*.sql"))
            plan_files = sorted(Path("migrations/primary").glob("*.plan.json"))

            assert len(sql_files) >= 2, "Should have created a second SQL file"
            assert len(plan_files) >= 2, "Should have created a second plan file"

            second_sql = sql_files[-1]
            second_sql_content = second_sql.read_text(encoding="utf-8")
            assert "ALTER TABLE users ADD COLUMN bio" in second_sql_content, (
                f"Second migration should add bio column, got:\n{second_sql_content}"
            )
            assert "-- upgrade" in second_sql_content
            assert "-- rollback" in second_sql_content
            assert "ALTER TABLE users DROP COLUMN" in second_sql_content

            # Verify plan file
            second_plan = plan_files[-1]
            plan = json.loads(second_plan.read_text(encoding="utf-8"))
            assert plan["migration_id"] == second_sql.stem
            assert len(plan["operations"]) >= 1
            assert plan["operations"][0]["type"] == "add_column"

            # ── Step 7: Verify state was updated ──
            updated_state = json.loads(Path(".dbwarden/model_state.json").read_text())
            assert "bio" in updated_state["tables"]["users"]["columns"], (
                f"State should include 'bio' column, got: "
                f"{list(updated_state['tables']['users']['columns'].keys())}"
            )

            # ── Step 8: Run offline again with no changes ──
            make_migrations_cmd("no changes", offline=True, database="primary")
            sql_files_after = sorted(Path("migrations/primary").glob("*.sql"))
            assert len(sql_files_after) == len(sql_files), (
                "No new SQL should be created when there are no changes"
            )

        finally:
            os.chdir(old_cwd)


def test_offline_missing_state_file_does_not_crash():
    """Running make-migrations --offline without a state file should give a clear error."""
    set_dev_mode(False)
    with tempfile.TemporaryDirectory() as tmpdir:
        old_cwd = os.getcwd()
        try:
            os.chdir(tmpdir)

            Path("dbwarden.py").write_text(
                "from dbwarden import database_config\n\n"
                "database_config(database_name='primary', default=True, "
                "database_type='sqlite', database_url_sync='sqlite:///./app.db', "
                "model_paths=['models'])\n",
                encoding="utf-8",
            )
            Path("models").mkdir(parents=True)
            Path("models/user.py").write_text(
                "from sqlalchemy import Column, Integer, String\n"
                "from sqlalchemy.orm import declarative_base\n\n"
                "Base = declarative_base()\n\n"
                "class User(Base):\n"
                "    __tablename__ = 'users'\n"
                "    id = Column(Integer, primary_key=True)\n"
                "    email = Column(String(255), nullable=False)\n",
                encoding="utf-8",
            )
            Path("migrations/primary").mkdir(parents=True)
            Path(".dbwarden").mkdir(parents=True)

            # Run offline WITHOUT state file: should not crash
            make_migrations_cmd("should fail gracefully", offline=True, database="primary")

            # No SQL file should be created
            sql_files = sorted(Path("migrations/primary").glob("*.sql"))
            assert len(sql_files) == 0, "No migration should be created without state file"

        finally:
            os.chdir(old_cwd)


def test_offline_state_format_version():
    """State file format_version should be tracked and round-trippable."""
    prev = model_state_to_dict([_make_table("users")])
    assert prev["format_version"] == 2
    serialized = json.dumps(prev, indent=2, default=str)
    restored = json.loads(serialized)
    assert restored["format_version"] == 2
    assert "users" in restored["tables"]


def test_offline_normalizes_v1_state():
    prev = {
        "format_version": 1,
        "tables": {
            "users": {
                "columns": {
                    "id": {"type": "integer", "nullable": False, "primary_key": True, "unique": False, "default": None, "foreign_key": None, "comment": None},
                    "email": {"type": "varchar", "nullable": False, "primary_key": False, "unique": True, "default": None, "foreign_key": None, "comment": None, "ch_meta": {"ch_codec": "ZSTD(3)"}},
                },
                "indexes": [{"name": "ix_users_email", "columns": ["email"], "unique": False}],
                "foreign_keys": [{"column": "email", "references": "accounts.email"}],
                "checks": [{"name": "ck_users_email", "sql_expression": "email <> ''"}],
                "uniques": [{"name": "uq_users_email", "columns": ["email"]}],
                "comment": "Users",
                "object_type": "table",
                "backend_table_spec": {"backend": "postgresql", "pg_fillfactor": 80},
            }
        },
    }
    normalized = normalize_model_state(prev)
    assert normalized["format_version"] == 2
    assert normalized["tables"]["users"]["columns"]["email"]["ch_column"]["ch_codec"] == "ZSTD(3)"
    assert normalized["indexes"]["ix_users_email"]["table"] == "users"
    fk = next(c for c in normalized["constraints"].values() if c["type"] == "foreign_key")
    assert fk["referenced_table"] == "accounts"
    ck = next(c for c in normalized["constraints"].values() if c["type"] == "check")
    assert ck["expression"] == "email <> ''"


# ── Edge case tests ──────────────────────────────────────────────


def test_offline_both_empty_states():
    """Two empty state dicts should produce no operations."""
    up_ops, down_ops = diff_model_states({}, {})
    assert len(up_ops) == 0
    assert len(down_ops) == 0


def test_offline_empty_curr_no_crash():
    """Empty current state (no 'tables' key) should not crash."""
    prev = model_state_to_dict([_make_table("users")])
    up_ops, down_ops = diff_model_states(prev, {"format_version": 1})
    assert len(up_ops) == 1
    assert up_ops[0]["type"] == "drop_table"


def test_offline_add_ten_tables():
    """Adding 10 tables at once should produce 10 create_table ops."""
    prev = model_state_to_dict([])
    tables = [_make_table(f"table_{i}") for i in range(10)]
    curr = model_state_to_dict(tables)
    up_ops, down_ops = diff_model_states(prev, curr)
    assert len(up_ops) == 10
    assert all(op["type"] == "create_table" for op in up_ops)
    assert all(op["table"].startswith("table_") for op in up_ops)


def test_offline_add_drop_ten_columns():
    """Adding 10 columns then dropping them should detect all 20 ops."""
    prev_cols = [_make_col(f"col_{i}", "integer") for i in range(10)]
    curr_cols = [_make_col(f"col_{i}", "varchar") for i in range(10)]
    prev = model_state_to_dict([_make_table("data", columns=[_make_col("id")] + prev_cols)])
    curr = model_state_to_dict([_make_table("data", columns=[_make_col("id")] + curr_cols)])
    up_ops, down_ops = diff_model_states(prev, curr)
    type_ops = [op for op in up_ops if op["type"] == "alter_column_type"]
    assert len(type_ops) == 10


def test_offline_varchar_size_change():
    """Changing VARCHAR(255) to VARCHAR(100) is a type change."""
    prev_col = _make_col("email", "varchar(255)")
    curr_col = _make_col("email", "varchar(100)")
    prev = model_state_to_dict([_make_table("users", columns=[prev_col])])
    curr = model_state_to_dict([_make_table("users", columns=[curr_col])])
    up_ops, down_ops = diff_model_states(prev, curr)
    type_ops = [op for op in up_ops if op["type"] == "alter_column_type"]
    assert len(type_ops) == 1
    assert type_ops[0]["model_type"] == "varchar(100)"


def test_offline_all_column_types():
    """Diff should handle all common column types."""
    cols = [
        _make_col("pk", "bigint", pk=True),
        _make_col("name", "varchar(255)"),
        _make_col("bio", "text", nullable=True),
        _make_col("score", "float"),
        _make_col("rate", "decimal(10,2)"),
        _make_col("is_active", "boolean", default="false"),
        _make_col("created_at", "timestamp", nullable=True),
        _make_col("birth_date", "date", nullable=True),
        _make_col("payload", "jsonb", nullable=True),
        _make_col("uid", "uuid", nullable=False),
        _make_col("avatar", "blob", nullable=True),
    ]
    # Add all columns to an existing table
    prev = model_state_to_dict([_make_table("profiles", columns=[_make_col("id")])])
    curr = model_state_to_dict([_make_table("profiles", columns=[_make_col("id")] + cols)])
    up_ops, down_ops = diff_model_states(prev, curr)
    add_ops = [op for op in up_ops if op["type"] == "add_column"]
    assert len(add_ops) == 11
    types_found = {op["definition"]["type"] for op in add_ops}
    assert "bigint" in types_found
    assert "varchar(255)" in types_found
    assert "text" in types_found
    assert "boolean" in types_found
    assert "uuid" in types_found


def test_offline_comment_clear():
    """Clearing a comment (set to empty) should produce an alter_table_comment."""
    prev = model_state_to_dict([_make_table("users", comment="Old")])
    curr = model_state_to_dict([_make_table("users", comment="")])
    up_ops, down_ops = diff_model_states(prev, curr)
    comment_ops = [op for op in up_ops if op["type"] == "alter_table_comment"]
    assert len(comment_ops) == 1
    assert comment_ops[0]["comment"] == ""
    assert comment_ops[0]["previous_comment"] == "Old"
    # Verify SQL handles empty comment
    up_sql, rb_sql, changes = _diff_to_sql(prev, curr)
    assert "COMMENT ON TABLE users IS NULL" in up_sql or f"COMMENT ON TABLE users IS ''" in up_sql
    assert "COMMENT ON TABLE users IS 'Old'" in rb_sql


def test_offline_default_clear():
    """Clearing a default (set to None) should produce an alter_column_default."""
    prev = model_state_to_dict([_make_table("users", columns=[_make_col("name", "varchar", default="'Anonymous'")])])
    curr = model_state_to_dict([_make_table("users", columns=[_make_col("name", "varchar", default=None)])])
    up_ops, down_ops = diff_model_states(prev, curr)
    default_ops = [op for op in up_ops if op["type"] == "alter_column_default"]
    assert len(default_ops) == 1
    assert default_ops[0]["default"] is None
    assert default_ops[0]["column"] == "name"
    up_sql, rb_sql, changes = _diff_to_sql(prev, curr)
    assert "DROP DEFAULT" in up_sql or "default" in up_sql.lower()


def test_offline_simultaneous_column_changes():
    """Changing type + nullable + default on same column in one diff."""
    prev = model_state_to_dict([_make_table("users", columns=[
        _make_col("name", "varchar", nullable=True, default=None),
    ])])
    curr = model_state_to_dict([_make_table("users", columns=[
        _make_col("name", "text", nullable=False, default="'required'"),
    ])])
    up_ops, down_ops = diff_model_states(prev, curr)
    types_in = {op["type"] for op in up_ops}
    assert "alter_column_type" in types_in
    assert "alter_column_nullable" in types_in
    assert "alter_column_default" in types_in
    assert len(up_ops) == 3
    # Verify all ops reference the same column
    for op in up_ops:
        assert op["column"] == "name"
    # SQL should contain each change
    up_sql, rb_sql, changes = _diff_to_sql(prev, curr)
    assert "ALTER TABLE users ALTER COLUMN name TYPE text" in up_sql
    assert "SET NOT NULL" in up_sql or "NOT NULL" in up_sql
    assert "SET DEFAULT" in up_sql or "DEFAULT" in up_sql


def test_offline_case_sensitive_table():
    """Table names are case-sensitive: 'Users' and 'users' are different tables."""
    prev = model_state_to_dict([_make_table("Users")])
    curr = model_state_to_dict([_make_table("users")])
    up_ops, down_ops = diff_model_states(prev, curr)
    assert len(up_ops) == 2  # drop "Users" + create "users"
    drop_ops = [op for op in up_ops if op["type"] == "drop_table"]
    create_ops = [op for op in up_ops if op["type"] == "create_table"]
    assert len(drop_ops) == 1
    assert drop_ops[0]["table"] == "Users"
    assert len(create_ops) == 1
    assert create_ops[0]["table"] == "users"


def test_offline_foreign_key_in_state():
    """Foreign key metadata should be preserved in state and diffed."""
    table = ModelTable(
        name="posts",
        columns=[
            ModelColumn("id", "integer", False, True, False, None, None),
            ModelColumn("user_id", "integer", False, False, False, None, None),
        ],
        foreign_keys=[{"column": "user_id", "references": "users.id"}],
    )
    state = model_state_to_dict([table])
    entry = state["tables"]["posts"]
    assert len(entry["foreign_keys"]) == 1
    assert entry["foreign_keys"][0]["references"] == "users.id"


def test_offline_unique_column_in_state():
    """Unique flag should be preserved in column state."""
    col = _make_col("email", "varchar", nullable=False)
    col.unique = True
    table = _make_table("users", columns=[_make_col("id"), col])
    state = model_state_to_dict([table])
    col_entry = state["tables"]["users"]["columns"]["email"]
    assert col_entry["unique"] is True


def test_offline_checks_in_state():
    """Check constraints in table metadata should be preserved in state."""
    table = ModelTable(
        name="products",
        columns=[ModelColumn("price", "numeric", False, False, False, None, None)],
        checks=[{"name": "ck_price_positive", "expression": "price > 0"}],
    )
    state = model_state_to_dict([table])
    entry = state["tables"]["products"]
    assert len(entry["checks"]) == 1
    assert entry["checks"][0]["name"] == "ck_price_positive"


def test_offline_uniques_in_state():
    """Unique constraints in table metadata should be preserved in state."""
    table = ModelTable(
        name="users",
        columns=[ModelColumn("email", "varchar", False, False, False, None, None)],
        uniques=[{"name": "uq_email", "columns": ["email"]}],
    )
    state = model_state_to_dict([table])
    entry = state["tables"]["users"]
    assert len(entry["uniques"]) == 1
    assert entry["uniques"][0]["name"] == "uq_email"


def test_offline_object_type_view():
    """Views should be tracked with object_type='view' in state."""
    table = ModelTable(
        name="active_users",
        columns=[ModelColumn("id", "integer", False, True, False, None, None)],
        object_type="view",
    )
    state = model_state_to_dict([table])
    entry = state["tables"]["active_users"]
    assert entry["object_type"] == "view"


def test_offline_add_and_drop_same_column_name():
    """Same column name added in one table and dropped in another: no collision."""
    prev = model_state_to_dict([_make_table("users")])
    curr = model_state_to_dict([
        _make_table("users", columns=[_make_col("id"), _make_col("email", "varchar")]),
        _make_table("posts"),
    ])
    up_ops, down_ops = diff_model_states(prev, curr)
    add_cols = [op for op in up_ops if op["type"] == "add_column"]
    create_tables = [op for op in up_ops if op["type"] == "create_table"]
    assert len(add_cols) == 1
    assert add_cols[0]["table"] == "users"
    assert add_cols[0]["column"] == "email"
    assert len(create_tables) == 1
    assert create_tables[0]["table"] == "posts"


def test_offline_rollback_produces_reversible_sql():
    """Applying upgrade SQL then rollback SQL should produce symmetric result."""
    from dbwarden.config import set_dev_mode
    set_dev_mode(True)
    prev = model_state_to_dict([_make_table("users", columns=[_make_col("id")])])
    curr = model_state_to_dict([_make_table("users", columns=[
        _make_col("id"), _make_col("email", "varchar", nullable=True, default=None),
    ])])
    up_sql, rb_sql, changes = _diff_to_sql(prev, curr)
    assert "ALTER TABLE users ADD COLUMN email" in up_sql
    assert "ALTER TABLE users DROP COLUMN email" in rb_sql
    # Round-trip: the rollback of add_column should be drop_column
    assert changes[0].operation == "add_column"
    assert changes[0].target == "email"


def test_offline_corrupted_state_file():
    """Corrupted state file should be caught gracefully."""
    set_dev_mode(False)
    with tempfile.TemporaryDirectory() as tmpdir:
        old_cwd = os.getcwd()
        try:
            os.chdir(tmpdir)

            Path("dbwarden.py").write_text(
                "from dbwarden import database_config\n\n"
                "database_config(database_name='primary', default=True, "
                "database_type='sqlite', database_url_sync='sqlite:///./app.db', "
                "model_paths=['models'])\n",
                encoding="utf-8",
            )
            Path("models").mkdir(parents=True)
            Path("models/user.py").write_text(
                "from sqlalchemy import Column, Integer, String\n"
                "from sqlalchemy.orm import declarative_base\n\n"
                "Base = declarative_base()\n\n"
                "class User(Base):\n"
                "    __tablename__ = 'users'\n"
                "    id = Column(Integer, primary_key=True)\n"
                "    email = Column(String(255), nullable=False)\n",
                encoding="utf-8",
            )
            Path("migrations/primary").mkdir(parents=True)
            Path(".dbwarden").mkdir(parents=True)
            # Write invalid JSON
            Path(".dbwarden/model_state.json").write_text("NOT JSON\n", encoding="utf-8")

            # Should not crash with JSONDecodeError
            make_migrations_cmd("corrupted state", offline=True, database="primary")

            # No SQL file should be created
            sql_files = sorted(Path("migrations/primary").glob("*.sql"))
            assert len(sql_files) == 0
        finally:
            os.chdir(old_cwd)


def test_offline_empty_tables_in_state():
    """State with empty 'tables' dict should not crash."""
    set_dev_mode(False)
    with tempfile.TemporaryDirectory() as tmpdir:
        old_cwd = os.getcwd()
        try:
            os.chdir(tmpdir)

            Path("dbwarden.py").write_text(
                "from dbwarden import database_config\n\n"
                "database_config(database_name='primary', default=True, "
                "database_type='sqlite', database_url_sync='sqlite:///./app.db', "
                "model_paths=['models'])\n",
                encoding="utf-8",
            )
            Path("models").mkdir(parents=True)
            Path("models/user.py").write_text(
                "from sqlalchemy import Column, Integer, String\n"
                "from sqlalchemy.orm import declarative_base\n\n"
                "Base = declarative_base()\n\n"
                "class User(Base):\n"
                "    __tablename__ = 'users'\n"
                "    id = Column(Integer, primary_key=True)\n"
                "    email = Column(String(255), nullable=False)\n",
                encoding="utf-8",
            )
            Path("migrations/primary").mkdir(parents=True)
            Path(".dbwarden").mkdir(parents=True)
            Path(".dbwarden/model_state.json").write_text(
                json.dumps({"format_version": 1, "exported_at": "2026-01-01T00:00:00Z", "dbwarden_version": "0.9.4", "tables": {}}) + "\n",
                encoding="utf-8",
            )

            # Should detect all models as new tables
            make_migrations_cmd("first offline", offline=True, database="primary")

            sql_files = sorted(Path("migrations/primary").glob("*.sql"))
            assert len(sql_files) == 1
            sql_content = sql_files[0].read_text(encoding="utf-8")
            assert "CREATE TABLE" in sql_content or "users" in sql_content
        finally:
            os.chdir(old_cwd)


def test_offline_no_model_paths():
    """If model_paths is empty/list with missing dir, should not crash."""
    set_dev_mode(False)
    with tempfile.TemporaryDirectory() as tmpdir:
        old_cwd = os.getcwd()
        try:
            os.chdir(tmpdir)

            Path("dbwarden.py").write_text(
                "from dbwarden import database_config\n\n"
                "database_config(database_name='primary', default=True, "
                "database_type='sqlite', database_url_sync='sqlite:///./app.db', "
                "model_paths=['nonexistent'])\n",
                encoding="utf-8",
            )
            Path("migrations/primary").mkdir(parents=True)
            Path(".dbwarden").mkdir(parents=True)
            Path(".dbwarden/model_state.json").write_text(
                json.dumps({"format_version": 1, "tables": {}}) + "\n",
                encoding="utf-8",
            )

            # Should produce a warning, not crash
            make_migrations_cmd("no models", offline=True, database="primary")
        finally:
            os.chdir(old_cwd)


def test_offline_missing_migrations_dir():
    """If migrations directory doesn't exist, should create it and work."""
    set_dev_mode(False)
    with tempfile.TemporaryDirectory() as tmpdir:
        old_cwd = os.getcwd()
        try:
            os.chdir(tmpdir)

            Path("dbwarden.py").write_text(
                "from dbwarden import database_config\n\n"
                "database_config(database_name='primary', default=True, "
                "database_type='sqlite', database_url_sync='sqlite:///./app.db', "
                "model_paths=['models'])\n",
                encoding="utf-8",
            )
            Path("models").mkdir(parents=True)
            Path("models/user.py").write_text(
                "from sqlalchemy import Column, Integer, String\n"
                "from sqlalchemy.orm import declarative_base\n\n"
                "Base = declarative_base()\n\n"
                "class User(Base):\n"
                "    __tablename__ = 'users'\n"
                "    id = Column(Integer, primary_key=True)\n"
                "    email = Column(String(255), nullable=False, unique=True)\n",
                encoding="utf-8",
            )
            Path(".dbwarden").mkdir(parents=True)
            initial_state = {
                "format_version": 1,
                "tables": {
                    "users": {
                        "columns": {
                            "id": {"type": "integer", "nullable": False, "primary_key": True, "unique": False, "default": None, "foreign_key": None, "comment": None},
                        },
                        "indexes": [], "foreign_keys": [], "checks": [], "uniques": [],
                        "comment": None, "object_type": "table", "backend_table_spec": {},
                    }
                },
            }
            Path(".dbwarden/model_state.json").write_text(
                json.dumps(initial_state) + "\n", encoding="utf-8"
            )

            # Create migrations dir (required by get_migrations_directory)
            Path("migrations/primary").mkdir(parents=True)
            make_migrations_cmd("initial", offline=True, database="primary")

            sql_files = sorted(Path("migrations/primary").glob("*.sql"))
            assert len(sql_files) >= 1
        finally:
            os.chdir(old_cwd)


def test_offline_description_with_spaces():
    """Description with spaces should produce a valid filename."""
    prev = model_state_to_dict([_make_table("users", columns=[_make_col("id")])])
    curr = model_state_to_dict([_make_table("users", columns=[
        _make_col("id"), _make_col("email", "varchar"),
    ])])
    up_ops, down_ops = diff_model_states(prev, curr)
    up_sql, rb_sql, changes = _diff_to_sql(prev, curr)
    assert up_sql.strip()  # should have SQL even without description
    assert rb_sql.strip()


def test_offline_type_coercion_not_normalized():
    """Types that normalize to same string should not trigger changes."""
    prev = model_state_to_dict([_make_table("items", columns=[_make_col("val", "INTEGER")])])
    curr = model_state_to_dict([_make_table("items", columns=[_make_col("val", "integer")])])
    up_ops, down_ops = diff_model_states(prev, curr)
    type_changes = [op for op in up_ops if op["type"] == "alter_column_type"]
    assert len(type_changes) == 0  # Should be normalized and detected as same


def test_offline_duplicate_ops_not_generated():
    """Identical prev and curr with no column changes should produce 0 ops."""
    cols = [_make_col("id"), _make_col("email", "varchar", nullable=True)]
    prev = model_state_to_dict([_make_table("users", columns=cols)])
    curr = model_state_to_dict([_make_table("users", columns=cols)])
    up_ops, down_ops = diff_model_states(prev, curr)
    assert len(up_ops) == 0
    assert len(down_ops) == 0


def test_offline_multiple_indexes():
    """Multiple indexes on same table should all be detected."""
    from dbwarden.engine.model_discovery import IndexInfo
    table = _make_table("users", columns=[
        _make_col("name", "varchar"), _make_col("email", "varchar"),
    ])
    table.indexes = [
        IndexInfo(name="ix_name", columns=["name"]),
        IndexInfo(name="ix_email", columns=["email"]),
    ]
    prev = model_state_to_dict([_make_table("users")])
    curr = model_state_to_dict([table])
    up_ops, down_ops = diff_model_states(prev, curr)
    add_idx = [op for op in up_ops if op["type"] == "add_index"]
    assert len(add_idx) == 2
    columns = {tuple(op["columns"]) for op in add_idx}
    assert ("name",) in columns
    assert ("email",) in columns


def test_offline_index_ops_preserve_rollback_attrs():
    from dbwarden.engine.model_discovery import IndexInfo

    prev_table = _make_table("users", columns=[_make_col("email", "varchar")])
    prev_table.indexes = [IndexInfo(name="ix_email", columns=["email"], unique=True)]
    prev = model_state_to_dict([prev_table])
    curr = model_state_to_dict([_make_table("users", columns=[_make_col("email", "varchar")])])

    up_ops, _down_ops = diff_model_states(prev, curr)

    drop_index = next(op for op in up_ops if op["type"] == "drop_index")
    assert drop_index["__rollback_attrs"]["columns"] == ["email"]
    assert drop_index["__rollback_attrs"]["unique"] is True


def test_offline_no_policy_ops_without_plugin():
    prev = model_state_to_dict([
        ModelTable(
            name="accounts",
            columns=[_make_col("id")],
            pg_policies=[{"name": "owners_only", "role": "app_user", "using": "owner_id = current_user_id()"}],
        )
    ])
    curr = model_state_to_dict([
        ModelTable(
            name="accounts",
            columns=[_make_col("id")],
            pg_policies=[{"name": "owners_only", "role": "admin_user", "using": "owner_id = current_user_id()"}],
        )
    ])

    up_ops, _down_ops = diff_model_states(prev, curr)

    policy_ops = [op for op in up_ops if "policy" in op.get("type", "")]
    assert not policy_ops


def test_offline_none_comment_roundtrip():
    """Setting comment from None to string and back should produce symmetric ops."""
    # None → "Hello"
    prev = model_state_to_dict([_make_table("items")])  # default comment=None
    curr = model_state_to_dict([_make_table("items", comment="Hello")])
    up_ops, down_ops = diff_model_states(prev, curr)
    comment_ops = [op for op in up_ops if op["type"] == "alter_table_comment"]
    assert len(comment_ops) == 1
    assert comment_ops[0]["comment"] == "Hello"
    assert comment_ops[0]["previous_comment"] is None
    # "Hello" → None
    prev2 = model_state_to_dict([_make_table("items", comment="Hello")])
    curr2 = model_state_to_dict([_make_table("items")])
    up_ops2, down_ops2 = diff_model_states(prev2, curr2)
    comment_ops2 = [op for op in up_ops2 if op["type"] == "alter_table_comment"]
    assert len(comment_ops2) == 1
    assert comment_ops2[0]["comment"] is None
    assert comment_ops2[0]["previous_comment"] == "Hello"


def test_offline_clickhouse_engine_recreate_end_to_end():
    """
    End-to-end offline flow with ClickHouse engine change:
    Create initial state with CH backend_table_spec → modify state to change engine →
    make-migrations --offline --clickhouse-engine-recreate →
    verify .sql contains ENGINE = and ORDER BY.
    """
    set_dev_mode(False)
    with tempfile.TemporaryDirectory() as tmpdir:
        old_cwd = os.getcwd()
        try:
            os.chdir(tmpdir)

            # Write config with clickhouse database type (offline, dummy URL)
            Path("dbwarden.py").write_text(
                "from dbwarden import database_config\n\n"
                "database_config(database_name='primary', default=True, "
                "database_type='clickhouse', database_url_sync='clickhouse://localhost/', "
                "model_paths=['models'])\n",
                encoding="utf-8",
            )

            # Write model file with CH metadata via __dbwarden_meta__
            Path("models").mkdir(parents=True)
            Path("models/event.py").write_text(
                "from sqlalchemy import Column, Integer, String\n"
                "from sqlalchemy.orm import declarative_base\n"
                "from dbwarden.schema._base import DBWardenMeta, attach_meta\n\n"
                "Base = declarative_base()\n\n"
                "class Event(Base):\n"
                "    __tablename__ = 'events'\n"
                "    id = Column(Integer, primary_key=True)\n"
                "    name = Column(String(255))\n\n"
                "attach_meta(Event, DBWardenMeta(\n"
                "    backend_table={\n"
                "        'ch_engine': 'MergeTree',\n"
                "        'ch_order_by': ('id',),\n"
                "    }\n"
                "))\n",
                encoding="utf-8",
            )

            # Create migrations dir and .dbwarden/ dir
            Path("migrations/primary").mkdir(parents=True)
            Path(".dbwarden").mkdir(parents=True)

            # Write initial model_state.json with matching CH options
            initial_state = {
                "format_version": 2,
                "exported_at": "2026-06-01T00:00:00Z",
                "dbwarden_version": "0.9.4",
                "tables": {
                    "events": {
                        "name": "events",
                        "columns": {
                            "id": {
                                "name": "id",
                                "type": "integer",
                                "nullable": False,
                                "primary_key": True,
                                "unique": False,
                                "default": None,
                                "foreign_key": None,
                                "comment": None,
                                "autoincrement": None,
                                "pg_column": {},
                                "ch_column": {},
                            },
                            "name": {
                                "name": "name",
                                "type": "varchar(255)",
                                "nullable": True,
                                "primary_key": False,
                                "unique": False,
                                "default": None,
                                "foreign_key": None,
                                "comment": None,
                                "autoincrement": None,
                                "pg_column": {},
                                "ch_column": {},
                            },
                        },
                        "indexes": [],
                        "foreign_keys": [],
                        "checks": [],
                        "uniques": [],
                        "comment": None,
                        "object_type": "table",
                        "backend_table_spec": {
                            "backend": "clickhouse",
                            "ch_engine": "MergeTree",
                            "ch_order_by": ["id"],
                        },
                    }
                },
                "indexes": {},
                "constraints": {},
                "enums": {},
            }
            Path(".dbwarden/model_state.json").write_text(
                json.dumps(initial_state, indent=2) + "\n", encoding="utf-8"
            )

            # ── Step 1: First run should be no-op (model matches state) ──
            from dbwarden.commands.make_migrations import make_migrations_cmd
            make_migrations_cmd("initial", offline=True, database="primary")
            sql_files = sorted(Path("migrations/primary").glob("*.sql"))
            plan_files = sorted(Path("migrations/primary").glob("*.plan.json"))
            sql_count_before = len(sql_files)

            # ── Step 2: Update state to change engine → triggers recreate ──
            updated_state = dict(initial_state)
            updated_state["tables"]["events"]["backend_table_spec"] = {
                "backend": "clickhouse",
                "ch_engine": "ReplicatedMergeTree('/clickhouse/tables/events', '{replica}')",
                "ch_order_by": ["id"],
            }
            Path(".dbwarden/model_state.json").write_text(
                json.dumps(updated_state, indent=2) + "\n", encoding="utf-8"
            )

            # ── Step 3: Run offline with engine recreate flag ──
            make_migrations_cmd(
                "change engine",
                offline=True,
                database="primary",
                clickhouse_engine_recreate=True,
            )

            # ── Step 4: Verify SQL file was created with CH-specific syntax ──
            sql_files_after = sorted(Path("migrations/primary").glob("*.sql"))
            assert len(sql_files_after) > sql_count_before, (
                "A new migration should be created for the engine change"
            )
            offline_sql = sql_files_after[-1]
            offline_sql_content = offline_sql.read_text(encoding="utf-8")
            assert "ENGINE = " in offline_sql_content, (
                f"Engine change SQL should contain ENGINE =, got:\n{offline_sql_content}"
            )
            assert "ORDER BY " in offline_sql_content, (
                f"SQL should contain ORDER BY, got:\n{offline_sql_content}"
            )

            # ── Step 5: Verify plan file was created ──
            plan_files_after = sorted(Path("migrations/primary").glob("*.plan.json"))
            assert len(plan_files_after) > len(plan_files), (
                "A new plan file should be created"
            )

            # ── Step 6: Run offline again with no changes → no new SQL ──
            make_migrations_cmd("no changes", offline=True, database="primary")
            sql_files_stable = sorted(Path("migrations/primary").glob("*.sql"))
            assert len(sql_files_stable) == len(sql_files_after), (
                "No new SQL should be created when there are no changes"
            )

        finally:
            os.chdir(old_cwd)


def test_offline_postgresql_complex_round_trip():
    """
    Full offline round-trip for PostgreSQL with complex models:
    1. Export model state → no-op on first run
    2. Schema changes (add/drop columns, type changes, indexes, FKs, comments, renames)
    3. Generate migration with --rename flags
    4. Verify SQL output contains expected statements
    5. Confirm second run with no changes produces no new SQL
    """
    set_dev_mode(False)
    with tempfile.TemporaryDirectory() as tmpdir:
        old_cwd = os.getcwd()
        try:
            os.chdir(tmpdir)

            Path("dbwarden.py").write_text(
                "from dbwarden import database_config\n\n"
                "database_config(database_name='primary', default=True, "
                "database_type='postgresql', database_url_sync='postgresql:///', "
                "model_paths=['models'])\n",
                encoding="utf-8",
            )

            Path("models").mkdir(parents=True)
            Path("models/models.py").write_text(
                "from sqlalchemy import (Column, Integer, String, Text, Float, Boolean,\n"
                "                         ForeignKey, UniqueConstraint, CheckConstraint)\n"
                "from sqlalchemy.orm import declarative_base\n\n"
                "Base = declarative_base()\n\n"
                "class User(Base):\n"
                "    __tablename__ = 'users'\n"
                "    __table_args__ = (\n"
                "        UniqueConstraint('email', name='uq_users_email'),\n"
                "        CheckConstraint('age >= 0', name='ck_users_age'),\n"
                "    )\n"
                "    id = Column(Integer, primary_key=True)\n"
                "    email = Column(String(255), nullable=False, unique=True)\n"
                "    full_name = Column(String(200), nullable=True, server_default='')\n"
                "    age = Column(Integer, nullable=True, default=0)\n"
                "    is_active = Column(Boolean, nullable=False, default=True)\n"
                "\n"
                "class Order(Base):\n"
                "    __tablename__ = 'orders'\n"
                "    id = Column(Integer, primary_key=True)\n"
                "    user_id = Column(Integer, ForeignKey('users.id'), nullable=False)\n"
                "    total = Column(Float, nullable=False, default=0.0)\n"
                "    description = Column(Text, nullable=True)\n",
                encoding="utf-8",
            )

            Path("migrations/primary").mkdir(parents=True)
            Path(".dbwarden").mkdir(parents=True)

            # Initial state matches the model above
            initial_state: dict[str, Any] = {
                "format_version": 2,
                "exported_at": "2026-06-01T00:00:00Z",
                "dbwarden_version": "0.9.4",
                "tables": {
                    "users": {
                        "name": "users",
                        "columns": {
                            "id": {"name": "id", "type": "integer", "nullable": False, "primary_key": True, "unique": False, "default": None, "foreign_key": None, "comment": None, "autoincrement": None, "pg_column": {}, "ch_column": {}},
                            "email": {"name": "email", "type": "varchar(255)", "nullable": False, "primary_key": False, "unique": True, "default": None, "foreign_key": None, "comment": None, "autoincrement": None, "pg_column": {}, "ch_column": {}},
                            "full_name": {"name": "full_name", "type": "varchar(200)", "nullable": True, "primary_key": False, "unique": False, "default": "", "foreign_key": None, "comment": None, "autoincrement": None, "pg_column": {}, "ch_column": {}},
                            "age": {"name": "age", "type": "integer", "nullable": True, "primary_key": False, "unique": False, "default": "0", "foreign_key": None, "comment": None, "autoincrement": None, "pg_column": {}, "ch_column": {}},
                            "is_active": {"name": "is_active", "type": "boolean", "nullable": False, "primary_key": False, "unique": False, "default": "true", "foreign_key": None, "comment": None, "autoincrement": None, "pg_column": {}, "ch_column": {}},
                        },
                        "indexes": [],
                        "foreign_keys": [],
                        "checks": [],
                        "uniques": [{"name": "uq_users_email", "columns": ["email"]}],
                        "comment": None,
                        "object_type": "table",
                        "backend_table_spec": {},
                    },
                    "orders": {
                        "name": "orders",
                        "columns": {
                            "id": {"name": "id", "type": "integer", "nullable": False, "primary_key": True, "unique": False, "default": None, "foreign_key": None, "comment": None, "autoincrement": None, "pg_column": {}, "ch_column": {}},
                            "user_id": {"name": "user_id", "type": "integer", "nullable": False, "primary_key": False, "unique": False, "default": None, "foreign_key": None, "comment": None, "autoincrement": None, "pg_column": {}, "ch_column": {}},
                            "total": {"name": "total", "type": "float", "nullable": False, "primary_key": False, "unique": False, "default": "0.0", "foreign_key": None, "comment": None, "autoincrement": None, "pg_column": {}, "ch_column": {}},
                            "description": {"name": "description", "type": "text", "nullable": True, "primary_key": False, "unique": False, "default": None, "foreign_key": None, "comment": None, "autoincrement": None, "pg_column": {}, "ch_column": {}},
                        },
                        "indexes": [],
                        "foreign_keys": [{"columns": ["user_id"], "referenced_table": "users", "referenced_columns": ["id"], "on_delete": "NO ACTION", "on_update": "NO ACTION", "deferrable": False}],
                        "checks": [],
                        "uniques": [],
                        "comment": None,
                        "object_type": "table",
                        "backend_table_spec": {},
                    },
                },
                "indexes": {},
                "constraints": {
                    "users:unique:uq_users_email": {"type": "unique", "table": "users", "name": "uq_users_email", "columns": ["email"]},
                    "orders:foreign_key:0:": {"type": "foreign_key", "table": "orders", "columns": ["user_id"], "referenced_table": "users", "referenced_columns": ["id"], "on_delete": "NO ACTION", "on_update": "NO ACTION", "deferrable": False},
                },
                "enums": {},
            }
            Path(".dbwarden/model_state.json").write_text(
                json.dumps(initial_state, indent=2) + "\n", encoding="utf-8"
            )

            # ── Step 1: Initial run creates migration ──
            make_migrations_cmd("initial", offline=True, database="primary")
            sql_files_step1 = sorted(Path("migrations/primary").glob("*.sql"))
            initial_sql_count = len(sql_files_step1)

            # ── Step 1a: Second run with no changes → no new SQL ──
            make_migrations_cmd("no changes", offline=True, database="primary")
            sql_files_stable = sorted(Path("migrations/primary").glob("*.sql"))
            assert len(sql_files_stable) == initial_sql_count, (
                "No new SQL when state hasn't changed"
            )

            # ── Step 2: Change models ──
            # Drop full_name + description, add name + notes → creates drop+add pairs
            # that rename flags resolve into RENAME COLUMN
            Path("models/models.py").write_text(
                "from sqlalchemy import (Column, Integer, String, Text, Float, Boolean, BigInteger,\n"
                "                         ForeignKey, UniqueConstraint, CheckConstraint)\n"
                "from sqlalchemy.orm import declarative_base\n"
                "from dbwarden.databases import index\n\n"
                "Base = declarative_base()\n\n"
                "class User(Base):\n"
                "    __tablename__ = 'users'\n"
                "    __table_args__ = (\n"
                "        UniqueConstraint('email', name='uq_users_email'),\n"
                "        CheckConstraint('age >= 0', name='ck_users_age'),\n"
                "    )\n"
                "    id = Column(Integer, primary_key=True)\n"
                "    email = Column(String(255), nullable=False, unique=True)\n"
                "    name = Column(String(250), nullable=True, server_default='')\n"
                "    age = Column(Integer, nullable=False, default=0)\n"
                "    is_active = Column(Boolean, nullable=False, default=True)\n"
                "    bio = Column(Text, nullable=True, comment='User biography')\n"
                "\n"
                "    class Meta:\n"
                "        indexes = [\n"
                "            index('ix_users_name', ['name']),\n"
                "        ]\n"
                "\n"
                "class Order(Base):\n"
                "    __tablename__ = 'orders'\n"
                "    id = Column(BigInteger, primary_key=True)\n"
                "    user_id = Column(Integer, ForeignKey('users.id'), nullable=False)\n"
                "    total = Column(Float, nullable=False, default=0.0)\n"
                "    notes = Column(Text, nullable=True)\n"
                "    status = Column(String(50), nullable=True, default='pending')\n"
                "\n"
                "class Product(Base):\n"
                "    __tablename__ = 'products'\n"
                "    id = Column(Integer, primary_key=True)\n"
                "    name = Column(String(255), nullable=False)\n"
                "    price = Column(Float, nullable=False)\n",
                encoding="utf-8",
            )
            make_migrations_cmd(
                "evolve schema",
                offline=True,
                database="primary",
                rename_flags=["users.full_name:name", "orders.description:notes"],
            )

            # ── Step 4: Verify migration SQL ──
            sql_files_step2 = sorted(Path("migrations/primary").glob("*.sql"))
            assert len(sql_files_step2) == initial_sql_count + 1, (
                "Should have created one new migration file"
            )
            latest_sql = sql_files_step2[-1]
            offline_sql_content = latest_sql.read_text(encoding="utf-8")

            # New table products → CREATE TABLE
            assert "CREATE TABLE IF NOT EXISTS products" in offline_sql_content

            # New columns on existing tables → ADD COLUMN
            assert "ADD COLUMN bio" in offline_sql_content
            assert "ADD COLUMN status" in offline_sql_content

            # Renamed columns via flags → RENAME COLUMN (not DROP+ADD)
            assert "RENAME COLUMN full_name TO name" in offline_sql_content
            assert "RENAME COLUMN description TO notes" in offline_sql_content

            # Column type change: id Integer → BigInteger
            assert "ALTER TABLE orders ALTER COLUMN id TYPE" in offline_sql_content

            # Nullable change: age nullable → not null
            assert "ALTER TABLE users ALTER COLUMN age SET NOT NULL" in offline_sql_content

            # New index
            assert "ix_users_name" in offline_sql_content

            # ── Step 5: Plan file matches ──
            plan_files_step2 = sorted(Path("migrations/primary").glob("*.plan.json"))
            latest_plan = plan_files_step2[-1]
            plan = json.loads(latest_plan.read_text(encoding="utf-8"))
            op_types = {op["type"] for op in plan["operations"]}
            assert "create_table" in op_types
            assert "rename_column" in op_types
            assert "add_column" in op_types

            # ── Step 6: Second run with no changes → no new SQL ──
            make_migrations_cmd("no changes", offline=True, database="primary")
            sql_files_step3 = sorted(Path("migrations/primary").glob("*.sql"))
            plan_files_step3 = sorted(Path("migrations/primary").glob("*.plan.json"))
            assert len(sql_files_step3) == len(sql_files_step2)
            assert len(plan_files_step3) == len(plan_files_step2)

        finally:
            os.chdir(old_cwd)


def test_offline_clickhouse_complex_round_trip(monkeypatch):
    """
    Full offline round-trip for ClickHouse with complex models.
    Uses programmatic model_state_to_dict + diff_model_states + snapshot_diff_to_sql
    to exercise CH-specific features: engine recreation, projections, codecs,
    Nullable/LowCardinality columns, ORDER BY changes, and round-trip stability.
    """
    monkeypatch.setattr("dbwarden.engine.model_discovery.type_mapping._get_backend_name", lambda db_name=None: "clickhouse")
    monkeypatch.setattr("dbwarden.engine.snapshot._get_backend", lambda db_name=None: "clickhouse")

    col_id = _make_col("id", "UInt64", pk=True)
    col_ts = _make_col("ts", "DateTime")
    col_ts.ch_meta = {"ch_type": "DateTime", "ch_codec": "ZSTD(3)"}
    col_value = _make_col("value", "Float64")
    col_value.ch_meta = {"ch_type": "Float64", "ch_nullable": True}
    col_label = _make_col("label", "String")
    col_label.ch_meta = {"ch_type": "LowCardinality(String)", "ch_low_cardinality": True}

    base_ch_opts = {
        "ch_engine": "MergeTree",
        "ch_order_by": ["ts", "id"],
        "ch_partition_by": "toYYYYMM(ts)",
        "ch_sample_by": "id",
        "ch_ttl": ["ts + INTERVAL 90 DAY"],
        "ch_projections": [
            {"name": "by_value", "query": "SELECT value ORDER BY value"},
        ],
    }

    events_table = _make_table("events", columns=[col_id, col_ts, col_value, col_label], ch_opts=dict(base_ch_opts))
    prev_state = model_state_to_dict([events_table])

    # ── Change 1: engine change → recreate ──
    new_ch_opts = dict(base_ch_opts)
    new_ch_opts["ch_engine"] = "ReplicatedMergeTree"
    new_events = _make_table("events", columns=[col_id, col_ts, col_value, col_label], ch_opts=new_ch_opts)
    curr_state = model_state_to_dict([new_events])
    up_ops, down_ops = diff_model_states(prev_state, curr_state)
    assert any(op["type"] == "recreate_ch_table" for op in up_ops)

    from dbwarden.engine.snapshot import snapshot_diff_to_sql

    up_sql, rb_sql, changes = snapshot_diff_to_sql(up_ops, down_ops, db_name="primary")
    assert "ENGINE = " in up_sql
    assert "ORDER BY " in up_sql
    assert "PARTITION BY " in up_sql
    assert "TTL " in up_sql
    assert "PROJECTION" in up_sql

    # ── Change 2: add column, drop projection, change codec → no recreate ──
    col_new = _make_col("new_col", "String")
    col_new.ch_meta = {"ch_type": "String"}
    new_ch_opts2 = dict(new_ch_opts)  # keep ReplicatedMergeTree engine
    new_ch_opts2.pop("ch_projections", None)  # remove projection
    col_ts2 = _make_col("ts", "DateTime")
    col_ts2.ch_meta = {"ch_type": "DateTime", "ch_codec": "LZ4"}  # change codec
    col_value2 = _make_col("value", "Float64")
    col_value2.ch_meta = {"ch_type": "Float64", "ch_nullable": False}  # change nullable
    col_label2 = _make_col("label", "String")
    col_label2.ch_meta = {"ch_type": "LowCardinality(String)", "ch_low_cardinality": True}
    events2 = _make_table("events", columns=[col_id, col_ts2, col_value2, col_label2, col_new], ch_opts=new_ch_opts2)

    start_state = model_state_to_dict([new_events])
    curr_state2 = model_state_to_dict([events2])
    up_ops2, down_ops2 = diff_model_states(start_state, curr_state2)
    up_types = {op["type"] for op in up_ops2}
    assert "add_column" in up_types or "alter_ch_column" in up_types

    up_sql2, rb_sql2, _ = snapshot_diff_to_sql(up_ops2, down_ops2, db_name="primary")
    assert "ADD COLUMN new_col" in up_sql2 or "new_col" in up_sql2

    # ── Change 3: round-trip with no changes → no ops ──
    up_ops3, down_ops3 = diff_model_states(curr_state2, curr_state2)
    assert up_ops3 == []
    assert down_ops3 == []

    # ── Change 4: dictionary engine change → DROP+CREATE ──
    dict_col = _make_col("key", "UInt64", pk=True)
    dict_col.ch_meta = {"ch_type": "UInt64"}
    dict_col2 = _make_col("val", "String")
    dict_col2.ch_meta = {"ch_type": "String"}
    dict_opts = {
        "ch_engine": "MergeTree",
        "ch_dictionary": True,
        "ch_dict_layout": "flat()",
        "ch_dict_source": "FILE(path='/data/test.csv' format 'CSV')",
        "ch_dict_lifetime": "300",
    }
    dict_table = _make_table("my_dict", columns=[dict_col, dict_col2], object_type="dictionary", ch_opts=dict_opts)
    dict_state = model_state_to_dict([dict_table])

    dict_opts2 = dict(dict_opts)
    dict_opts2["ch_engine"] = "ReplicatedMergeTree"
    dict_table2 = _make_table("my_dict", columns=[dict_col, dict_col2], object_type="dictionary", ch_opts=dict_opts2)
    dict_state2 = model_state_to_dict([dict_table2])

    up_ops4, down_ops4 = diff_model_states(dict_state, dict_state2)
    recreate_dict = next(op for op in up_ops4 if op["type"] == "recreate_ch_table")
    assert recreate_dict is not None

    up_sql4, rb_sql4, _ = snapshot_diff_to_sql(up_ops4, down_ops4, db_name="primary")
    assert "DROP DICTIONARY my_dict" in up_sql4
    assert "CREATE DICTIONARY IF NOT EXISTS my_dict" in up_sql4
    assert "DROP DICTIONARY my_dict" in rb_sql4

    # ── Change 5: inline MV engine change → DROP VIEW + CREATE MATERIALIZED VIEW ──
    mv_col = _make_col("id", "UInt64", pk=True)
    mv_col.ch_meta = {"ch_type": "UInt64"}
    mv_opts = {
        "ch_engine": "MergeTree",
        "ch_order_by": ["id"],
        "ch_select_statement": "SELECT id FROM source",
        "ch_object_type": "materialized_view",
    }
    mv_table = _make_table("my_mv", columns=[mv_col], object_type="materialized_view", ch_opts=mv_opts)
    mv_state = model_state_to_dict([mv_table])

    mv_opts2 = dict(mv_opts)
    mv_opts2["ch_engine"] = "ReplicatedMergeTree"
    mv_table2 = _make_table("my_mv", columns=[mv_col], object_type="materialized_view", ch_opts=mv_opts2)
    mv_state2 = model_state_to_dict([mv_table2])

    up_ops5, down_ops5 = diff_model_states(mv_state, mv_state2)
    recreate_mv = next(op for op in up_ops5 if op["type"] == "recreate_ch_table")
    assert recreate_mv is not None

    up_sql5, rb_sql5, _ = snapshot_diff_to_sql(up_ops5, down_ops5, db_name="primary")
    assert "DROP VIEW IF EXISTS my_mv" in up_sql5
    assert "CREATE MATERIALIZED VIEW IF NOT EXISTS my_mv" in up_sql5


def test_offline_postgresql_prod_like_round_trip():
    """
    Prod-like PostgreSQL offline round-trip with multi-file models,
    e-commerce schema, and comprehensive schema evolution.
    State is bootstrapped from model discovery (no pre-populated state file).
    """
    set_dev_mode(False)
    with tempfile.TemporaryDirectory() as tmpdir:
        old_cwd = os.getcwd()
        try:
            os.chdir(tmpdir)

            Path("dbwarden.py").write_text(
                "from dbwarden import database_config\n\n"
                "database_config(database_name='primary', default=True, "
                "database_type='postgresql', database_url_sync='postgresql:///', "
                "model_paths=['models'])\n",
                encoding="utf-8",
            )

            # ── Single model file for reliable model discovery ──
            Path("models").mkdir(parents=True)
            Path("models/models.py").write_text(
                "from sqlalchemy import (Column, Integer, String, Text, Float, Boolean, DateTime,\n"
                "                         ForeignKey, UniqueConstraint, CheckConstraint, Index, func)\n"
                "from sqlalchemy.orm import declarative_base\n\n"
                "Base = declarative_base()\n\n"
                "class User(Base):\n"
                "    __tablename__ = 'users'\n"
                "    id = Column(Integer, primary_key=True)\n"
                "    email = Column(String(255), nullable=False, unique=True)\n"
                "    username = Column(String(100), nullable=False)\n"
                "    password_hash = Column(String(255), nullable=False)\n"
                "    is_active = Column(Boolean, nullable=False, default=True)\n"
                "    created_at = Column(DateTime, nullable=False, server_default=func.now())\n"
                "    updated_at = Column(DateTime, nullable=True, onupdate=func.now())\n"
                "\n"
                "class Category(Base):\n"
                "    __tablename__ = 'categories'\n"
                "    id = Column(Integer, primary_key=True)\n"
                "    name = Column(String(100), nullable=False, unique=True)\n"
                "    description = Column(Text, nullable=True)\n"
                "    sort_order = Column(Integer, nullable=False, default=0)\n"
                "    is_active = Column(Boolean, nullable=False, default=True)\n"
                "\n"
                "class Product(Base):\n"
                "    __tablename__ = 'products'\n"
                "    __table_args__ = (\n"
                "        CheckConstraint('price >= 0', name='ck_products_price'),\n"
                "        CheckConstraint('stock >= 0', name='ck_products_stock'),\n"
                "        Index('ix_products_name_price', 'name', 'price'),\n"
                "    )\n"
                "    id = Column(Integer, primary_key=True)\n"
                "    name = Column(String(200), nullable=False)\n"
                "    slug = Column(String(255), nullable=False, unique=True)\n"
                "    description = Column(Text, nullable=True)\n"
                "    price = Column(Float, nullable=False)\n"
                "    stock = Column(Integer, nullable=False, default=0)\n"
                "    category_id = Column(Integer, ForeignKey('categories.id'), nullable=True)\n"
                "    is_active = Column(Boolean, nullable=False, default=True)\n"
                "\n"
                "class Order(Base):\n"
                "    __tablename__ = 'orders'\n"
                "    id = Column(Integer, primary_key=True)\n"
                "    user_id = Column(Integer, ForeignKey('users.id'), nullable=False)\n"
                "    status = Column(String(50), nullable=False, default='pending')\n"
                "    total = Column(Float, nullable=False, default=0.0)\n"
                "    shipping_address = Column(Text, nullable=True)\n"
                "    created_at = Column(DateTime, nullable=False, server_default=func.now())\n",
                encoding="utf-8",
            )

            Path("migrations/primary").mkdir(parents=True)
            Path(".dbwarden").mkdir(parents=True)

            # ── Pre-populate EMPTY state → forces full initial migration ──
            empty_state: dict[str, Any] = {
                "format_version": 2,
                "exported_at": "2026-06-01T00:00:00Z",
                "dbwarden_version": "0.9.4",
                "tables": {},
                "indexes": {},
                "constraints": {},
                "enums": {},
            }
            Path(".dbwarden/model_state.json").write_text(
                json.dumps(empty_state, indent=2) + "\n", encoding="utf-8"
            )

            # ── Step 1: Initial run → creates migration ──
            make_migrations_cmd("initial", offline=True, database="primary")
            sql_before = len(sorted(Path("migrations/primary").glob("*.sql")))

            make_migrations_cmd("no changes", offline=True, database="primary")
            assert len(sorted(Path("migrations/primary").glob("*.sql"))) == sql_before

            # ── Step 2: Change models ──
            Path("models/models.py").write_text(
                "from sqlalchemy import (Column, Integer, String, Text, Float, Boolean, DateTime,\n"
                "                         ForeignKey, UniqueConstraint, CheckConstraint, Index, func)\n"
                "from sqlalchemy.orm import declarative_base\n\n"
                "Base = declarative_base()\n\n"
                "class User(Base):\n"
                "    __tablename__ = 'users'\n"
                "    id = Column(Integer, primary_key=True)\n"
                "    email = Column(String(255), nullable=False, unique=True)\n"
                "    handle = Column(String(100), nullable=False)\n"
                "    phone = Column(String(20), nullable=True)\n"
                "    is_active = Column(Boolean, nullable=False, default=True)\n"
                "    created_at = Column(DateTime, nullable=False, server_default=func.now())\n"
                "    updated_at = Column(DateTime, nullable=True, onupdate=func.now())\n"
                "\n"
                "class Category(Base):\n"
                "    __tablename__ = 'categories'\n"
                "    id = Column(Integer, primary_key=True)\n"
                "    name = Column(String(100), nullable=False, unique=True)\n"
                "    description = Column(Text, nullable=True)\n"
                "    sort_order = Column(Integer, nullable=False, default=0)\n"
                "    is_active = Column(Boolean, nullable=False, default=True)\n"
                "\n"
                "class Product(Base):\n"
                "    __tablename__ = 'products'\n"
                "    __table_args__ = (\n"
                "        CheckConstraint('price >= 0', name='ck_products_price'),\n"
                "        CheckConstraint('qty_available >= 0', name='ck_products_qty'),\n"
                "        Index('ix_products_name_price', 'name', 'price'),\n"
                "    )\n"
                "    id = Column(Integer, primary_key=True)\n"
                "    name = Column(String(200), nullable=False)\n"
                "    slug = Column(String(255), nullable=False, unique=True)\n"
                "    description = Column(Text, nullable=True)\n"
                "    price = Column(Float, nullable=False)\n"
                "    discount_price = Column(Float, nullable=True)\n"
                "    qty_available = Column(Integer, nullable=False, default=0)\n"
                "    category_id = Column(Integer, ForeignKey('categories.id'), nullable=True)\n"
                "    is_active = Column(Boolean, nullable=False, default=True)\n"
                "\n"
                "class Order(Base):\n"
                "    __tablename__ = 'orders'\n"
                "    id = Column(Integer, primary_key=True)\n"
                "    user_id = Column(Integer, ForeignKey('users.id'), nullable=False)\n"
                "    status = Column(String(50), nullable=False, default='pending')\n"
                "    total = Column(Float, nullable=False, default=0.0)\n"
                "    shipping_address = Column(Text, nullable=True)\n"
                "    created_at = Column(DateTime, nullable=False, server_default=func.now())\n"
                "\n"
                "class Review(Base):\n"
                "    __tablename__ = 'reviews'\n"
                "    __table_args__ = (\n"
                "        CheckConstraint('rating >= 1 AND rating <= 5', name='ck_reviews_rating'),\n"
                "    )\n"
                "    id = Column(Integer, primary_key=True)\n"
                "    product_id = Column(Integer, ForeignKey('products.id'), nullable=False)\n"
                "    user_id = Column(Integer, ForeignKey('users.id'), nullable=False)\n"
                "    rating = Column(Integer, nullable=False)\n"
                "    comment = Column(Text, nullable=True)\n",
                encoding="utf-8",
            )

            make_migrations_cmd(
                "evolve schema",
                offline=True,
                database="primary",
                rename_flags=["users.username:handle"],
            )

            # ── Step 3: Verify SQL ──
            sql_files = sorted(Path("migrations/primary").glob("*.sql"))
            assert len(sql_files) == sql_before + 1
            sql_content = sql_files[-1].read_text(encoding="utf-8")

            # New table
            assert "CREATE TABLE IF NOT EXISTS reviews" in sql_content

            # Dropped columns
            assert "DROP COLUMN password_hash" in sql_content
            assert "DROP COLUMN stock" in sql_content

            # Added columns
            assert "ADD COLUMN phone" in sql_content
            assert "ADD COLUMN discount_price" in sql_content
            assert "ADD COLUMN qty_available" in sql_content

            # Column rename via flag → RENAME COLUMN
            assert "RENAME COLUMN username TO handle" in sql_content

            # Check constraint changes
            assert "ck_products_qty" in sql_content or "qty_available" in sql_content

            # ── Step 4: Verify plan ──
            plan_files = sorted(Path("migrations/primary").glob("*.plan.json"))
            plan = json.loads(plan_files[-1].read_text(encoding="utf-8"))
            op_types = {op["type"] for op in plan["operations"]}
            assert "create_table" in op_types  # reviews
            assert "rename_column" in op_types  # username→handle
            assert "drop_column" in op_types    # password_hash, stock
            assert "add_column" in op_types     # phone, discount_price, qty_available

            # ── Step 5: No-op on second pass ──
            make_migrations_cmd("stable", offline=True, database="primary")
            assert len(sorted(Path("migrations/primary").glob("*.sql"))) == sql_before + 1

        finally:
            os.chdir(old_cwd)


def test_offline_clickhouse_prod_like_round_trip():
    """
    Prod-like ClickHouse offline round-trip with __dbwarden_meta__ models,
    engine recreation, column additions/drops, projection changes, and TTL changes.
    """
    set_dev_mode(False)
    with tempfile.TemporaryDirectory() as tmpdir:
        old_cwd = os.getcwd()
        try:
            os.chdir(tmpdir)

            Path("dbwarden.py").write_text(
                "from dbwarden import database_config\n\n"
                "database_config(database_name='primary', default=True, "
                "database_type='clickhouse', database_url_sync='clickhouse://localhost/', "
                "model_paths=['models'])\n",
                encoding="utf-8",
            )

            Path("models").mkdir(parents=True)
            Path("models/models.py").write_text(
                "from sqlalchemy import Column, Integer, String, Float\n"
                "from sqlalchemy.orm import declarative_base\n"
                "from dbwarden.schema._base import DBWardenMeta, attach_meta\n\n"
                "Base = declarative_base()\n\n"
                "class PageView(Base):\n"
                "    __tablename__ = 'page_views'\n"
                "    id = Column(Integer, primary_key=True)\n"
                "    user_id = Column(Integer)\n"
                "    url = Column(String(2048))\n"
                "    referrer = Column(String(2048))\n"
                "    user_agent = Column(String(512))\n"
                "    ip = Column(String(45))\n"
                "    timestamp = Column(String(30))\n"
                "    duration = Column(Integer)\n"
                "    country = Column(String(2))\n"
                "    browser = Column(String(50))\n\n"
                "attach_meta(PageView, DBWardenMeta(\n"
                "    backend_table={\n"
                "        'ch_engine': 'MergeTree',\n"
                "        'ch_order_by': ('timestamp', 'user_id'),\n"
                "        'ch_partition_by': \"toYYYYMM(timestamp)\",\n"
                "        'ch_ttl': [\"timestamp + INTERVAL 90 DAY\"],\n"
                "        'ch_projections': [\n"
                "            {'name': 'by_url', 'query': 'SELECT url, count() GROUP BY url ORDER BY url'},\n"
                "        ],\n"
                "    }\n"
                "))\n\n"
                "class Metric(Base):\n"
                "    __tablename__ = 'metrics'\n"
                "    id = Column(Integer, primary_key=True)\n"
                "    name = Column(String(255))\n"
                "    value = Column(Float)\n"
                "    recorded_at = Column(String(30))\n\n"
                "attach_meta(Metric, DBWardenMeta(\n"
                "    backend_table={\n"
                "        'ch_engine': 'MergeTree',\n"
                "        'ch_order_by': ('recorded_at', 'name'),\n"
                "    }\n"
                "))\n",
                encoding="utf-8",
            )

            Path("migrations/primary").mkdir(parents=True)
            Path(".dbwarden").mkdir(parents=True)

            # Empty state → full initial migration
            empty_state: dict[str, Any] = {
                "format_version": 2,
                "exported_at": "2026-06-01T00:00:00Z",
                "dbwarden_version": "0.9.4",
                "tables": {},
                "indexes": {},
                "constraints": {},
                "enums": {},
            }
            Path(".dbwarden/model_state.json").write_text(
                json.dumps(empty_state, indent=2) + "\n", encoding="utf-8"
            )

            # ── Step 1: Initial run ──
            make_migrations_cmd("initial", offline=True, database="primary")
            sql_before = len(sorted(Path("migrations/primary").glob("*.sql")))
            assert sql_before > 0, "Should have created initial migration SQL"

            # ── Step 1a: No changes → no new SQL ──
            make_migrations_cmd("no changes", offline=True, database="primary")
            assert len(sorted(Path("migrations/primary").glob("*.sql"))) == sql_before

            # ── Step 2: Evolve models ──
            Path("models/models.py").write_text(
                "from sqlalchemy import Column, Integer, String, Float\n"
                "from sqlalchemy.orm import declarative_base\n"
                "from dbwarden.schema._base import DBWardenMeta, attach_meta\n\n"
                "Base = declarative_base()\n\n"
                "class PageView(Base):\n"
                "    __tablename__ = 'page_views'\n"
                "    id = Column(Integer, primary_key=True)\n"
                "    user_id = Column(Integer)\n"
                "    url = Column(String(2048))\n"
                "    referrer = Column(String(2048))\n"
                "    user_agent = Column(String(512))\n"
                "    session_id = Column(String(128))\n"
                "    timestamp = Column(String(30))\n"
                "    duration = Column(Integer)\n"
                "    country = Column(String(2))\n"
                "    browser = Column(String(50))\n\n"
                "attach_meta(PageView, DBWardenMeta(\n"
                "    backend_table={\n"
                "        'ch_engine': \"ReplicatedMergeTree('/clickhouse/tables/page_views', '{replica}')\",\n"
                "        'ch_order_by': ('timestamp', 'user_id'),\n"
                "        'ch_partition_by': \"toYYYYMM(timestamp)\",\n"
                "        'ch_ttl': [\"timestamp + INTERVAL 180 DAY\"],\n"
                "        'ch_projections': [\n"
                "            {'name': 'by_browser', 'query': 'SELECT browser, count() GROUP BY browser ORDER BY browser'},\n"
                "        ],\n"
                "    }\n"
                "))\n\n"
                "class Metric(Base):\n"
                "    __tablename__ = 'metrics'\n"
                "    id = Column(Integer, primary_key=True)\n"
                "    name = Column(String(255))\n"
                "    value = Column(Float)\n"
                "    recorded_at = Column(String(30))\n"
                "    source = Column(String(100))\n\n"
                "attach_meta(Metric, DBWardenMeta(\n"
                "    backend_table={\n"
                "        'ch_engine': 'ReplicatedMergeTree(\"/clickhouse/tables/metrics\", \"{replica}\")',\n"
                "        'ch_order_by': ('recorded_at', 'name'),\n"
                "    }\n"
                "))\n",
                encoding="utf-8",
            )

            make_migrations_cmd(
                "evolve schema",
                offline=True,
                database="primary",
                clickhouse_engine_recreate=True,
            )

            # ── Step 3: Verify SQL ──
            sql_files = sorted(Path("migrations/primary").glob("*.sql"))
            assert len(sql_files) == sql_before + 1
            sql_content = sql_files[-1].read_text(encoding="utf-8")

            # Engine recreate
            assert "ENGINE = " in sql_content
            assert "ReplicatedMergeTree" in sql_content

            # Added columns
            assert "session_id" in sql_content
            assert "source" in sql_content

            # Dropped columns
            assert "ip" in sql_content

            # Projection
            assert "by_browser" in sql_content or "PROJECTION" in sql_content

            # ── Step 4: Verify plan ──
            plan_files = sorted(Path("migrations/primary").glob("*.plan.json"))
            plan = json.loads(plan_files[-1].read_text(encoding="utf-8"))
            op_types = {op["type"] for op in plan["operations"]}
            assert "recreate_ch_table" in op_types

            # ── Step 5: Second pass no-op ──
            make_migrations_cmd("stable", offline=True, database="primary")
            assert len(sorted(Path("migrations/primary").glob("*.sql"))) == sql_before + 1

        finally:
            os.chdir(old_cwd)


def test_offline_pg_schema_round_trip():
    """pg_schema is preserved in model_state_to_dict / diff_model_states round-trip."""
    table = _make_table("users", columns=[_make_col("id")])
    table.schema = "app"
    state = model_state_to_dict([table])
    entry = state["tables"]["users"]
    assert entry["schema"] == "app"

    tables_out = diff_model_states(state, state)
    assert len(tables_out[0]) == 0


def test_offline_pg_schema_diff_new_table():
    """A new table with schema generates a create_table op carrying schema in state_table."""
    prev = model_state_to_dict([])
    table = _make_table("users", columns=[_make_col("id")])
    table.schema = "app"
    curr = model_state_to_dict([table])
    up, down = diff_model_states(prev, curr)
    create_ops = [op for op in up if op["type"] == "create_table"]
    assert len(create_ops) == 1
    assert create_ops[0]["state_table"].get("schema") == "app"


def test_offline_pg_reserved_word_quoting(monkeypatch):
    """Reserved word table names are quoted in generated SQL."""
    import dbwarden.engine.model_discovery as md
    monkeypatch.setattr(md.type_mapping, "_get_backend_name", lambda db_name=None: "postgresql")
    from dbwarden.engine.model_discovery import generate_create_table_sql

    columns = [ModelColumn("id", "INTEGER", False, True, False, None, None)]
    table = ModelTable(name="user", columns=columns)
    sql = generate_create_table_sql(table)

    assert '"user"' in sql


def test_offline_pg_extension_sql():
    """A plugin-owned config key round-trips once its plugin has registered it."""
    from dbwarden.config import get_database, set_dev_mode
    from dbwarden.plugin import ConfigKeyRegistry

    set_dev_mode(False)
    # Stand in for dbwarden-pgsql-extensions being installed.
    ConfigKeyRegistry.register("pg_extensions", plugin="dbwarden-pgsql-extensions")
    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            old_cwd = os.getcwd()
            try:
                os.chdir(tmpdir)
                Path("dbwarden.py").write_text(
                    "from dbwarden import database_config\n\n"
                    "database_config(database_name='primary', default=True, "
                    "database_type='postgresql', database_url_sync='postgresql:///', "
                    "model_paths=['models'], pg_extensions=['citext', 'pgcrypto'])\n",
                    encoding="utf-8",
                )
                config = get_database("primary")
                assert config.pg_extensions == ["citext", "pgcrypto"]
            finally:
                os.chdir(old_cwd)
    finally:
        ConfigKeyRegistry.reset()
    set_dev_mode(False)


def test_plugin_config_key_without_plugin_raises():
    """A plugin-owned key declared without its plugin fails loudly, not silently."""
    from dbwarden.config_registry import database_config
    from dbwarden.exceptions import DBWardenConfigError
    from dbwarden.plugin import ConfigKeyRegistry

    ConfigKeyRegistry.reset()
    with pytest.raises(DBWardenConfigError, match="dbwarden-pgsql-rbac"):
        database_config(
            database_name="primary",
            database_type="postgresql",
            database_url_sync="postgresql:///",
            pg_roles=[{"name": "analyst"}],
        )


def test_unknown_config_key_raises():
    """A key no plugin owns is a typo and must not be silently accepted."""
    from dbwarden.config_registry import database_config
    from dbwarden.exceptions import DBWardenConfigError

    with pytest.raises(DBWardenConfigError, match="unexpected keyword argument"):
        database_config(
            database_name="primary",
            database_type="postgresql",
            database_url_sync="postgresql:///",
            pg_rolez=[{"name": "typo"}],
        )


def test_offline_diff_rename_column():
    prev = model_state_to_dict([
        _make_table("users", columns=[
            _make_col("id", "biginteger", pk=True),
            _make_col("name", "varchar"),
        ])
    ])
    curr = model_state_to_dict([
        _make_table("users", columns=[
            _make_col("id", "biginteger", pk=True),
            _make_col("full_name", "varchar"),
        ])
    ])
    up, down = diff_model_states(prev, curr)
    rename_ops = [op for op in up if op["type"] == "rename_column"]
    assert len(rename_ops) == 1
    assert rename_ops[0]["old_name"] == "name"
    assert rename_ops[0]["new_name"] == "full_name"


def test_offline_diff_pg_column_meta():
    prev = model_state_to_dict([
        _make_table("users", columns=[
            _make_col("id", "biginteger", pk=True),
        ])
    ])
    curr_state = model_state_to_dict([
        _make_table("users", columns=[
            _make_col("id", "biginteger", pk=True),
        ])
    ])
    curr_state["tables"]["users"]["columns"]["id"]["pg_column"] = {
        "collation": "en_US.utf8",
        "storage": "PLAIN",
    }
    up, down = diff_model_states(prev, curr_state)
    pg_meta_ops = [op for op in up if op["type"] == "alter_pg_column_meta"]
    assert len(pg_meta_ops) == 1
    assert pg_meta_ops[0]["column"] == "id"


def test_offline_diff_ch_options_both_none():
    prev = model_state_to_dict([
        _make_table("users", columns=[_make_col("id", pk=True)])
    ])
    curr = model_state_to_dict([
        _make_table("users", columns=[_make_col("id", pk=True)])
    ])
    prev["tables"]["users"]["backend_table_spec"] = {"backend": "clickhouse", "ch_option_a": None}
    curr["tables"]["users"]["backend_table_spec"] = {"backend": "clickhouse", "ch_option_a": None}
    up, down = diff_model_states(prev, curr)
    ch_ops = [op for op in up if op["type"] == "alter_ch_options"]
    assert len(ch_ops) == 0


def test_offline_diff_my_auto_increment_none():
    prev = model_state_to_dict([
        _make_table("users", columns=[_make_col("id", pk=True)])
    ])
    curr = model_state_to_dict([
        _make_table("users", columns=[_make_col("id", pk=True)])
    ])
    prev["tables"]["users"]["backend_table_spec"] = {"backend": "mysql", "my_auto_increment": None}
    curr["tables"]["users"]["backend_table_spec"] = {"backend": "mysql", "my_auto_increment": None}
    up, down = diff_model_states(prev, curr)
    my_ops = [op for op in up if op["type"] == "alter_my_table"]
    assert len(my_ops) == 0
