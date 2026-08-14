from __future__ import annotations

from unittest.mock import patch

from dbwarden.engine.core.models import ModelTable
from dbwarden.engine.model_discovery.sql_generation import generate_drop_object_sql


def test_drop_object_quotes_postgresql_reserved_identifier():
    table = ModelTable(name="select", columns=[], object_type="table")
    with patch("dbwarden.engine.model_discovery.sql_generation._get_backend_name", return_value="postgresql"):
        assert generate_drop_object_sql(table) == 'DROP TABLE "select"'
