from __future__ import annotations

import pytest

from dbwarden.engine.backends.postgresql.handlers.statistics_handler import StatisticsHandler
from dbwarden.engine.core.protocol import Op


@pytest.mark.parametrize("value", ["0; DROP TABLE users", -2, True])
def test_statistics_target_rejects_unsafe_values(value):
    op = Op(
        object_type="alter_column_statistics",
        upgrade_attrs={"table": "users", "column": "name", "statistics": value},
        rollback_attrs={},
    )
    with pytest.raises(ValueError, match="statistics target"):
        StatisticsHandler().emit(op, db_name="primary")


def test_statistics_target_accepts_zero():
    op = Op(
        object_type="alter_column_statistics",
        upgrade_attrs={"table": "users", "column": "name", "statistics": 0},
        rollback_attrs={},
    )
    assert "SET STATISTICS 0" in StatisticsHandler().emit(op, db_name="primary")[0].upgrade_sql
