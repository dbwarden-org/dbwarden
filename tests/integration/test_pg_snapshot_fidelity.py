"""The snapshot must faithfully describe a feature-rich live PostgreSQL schema.

Snapshot extraction guards nearly every catalog query with a broad `except`.
That is defensive by design, but it means a query that starts failing - against
a new server version, say - loses its slice of the schema without failing the
run. The diff then reports the missing piece as a difference, and the next
migration "fixes" a schema that was never wrong.

These tests read a schema exercising the metadata dbwarden claims to support
and assert each piece survives the round into the snapshot.

Usage::

    pytest tests/integration/test_pg_snapshot_fidelity.py --pg-integration -v
"""

from __future__ import annotations

import os

import pytest

pytest.importorskip("sqlalchemy")

import sqlalchemy as sa  # noqa: E402

from dbwarden.engine.snapshot import extract_full_schema_snapshot  # noqa: E402

SCHEMA_SQL = """
CREATE EXTENSION IF NOT EXISTS btree_gist;

DROP TABLE IF EXISTS dbw_fid_bookings CASCADE;
DROP TABLE IF EXISTS dbw_fid_orders CASCADE;
DROP TABLE IF EXISTS dbw_fid_customers CASCADE;
DROP TYPE IF EXISTS dbw_fid_status CASCADE;

CREATE TYPE dbw_fid_status AS ENUM ('queued', 'sent');

CREATE TABLE dbw_fid_customers (
    id SERIAL PRIMARY KEY,
    email VARCHAR(255) NOT NULL UNIQUE,
    name TEXT COLLATE "C",
    notes TEXT,
    tags TEXT[],
    payload JSONB DEFAULT '{}'::jsonb,
    CONSTRAINT ck_dbw_fid_email CHECK (email <> '')
);
ALTER TABLE dbw_fid_customers ALTER COLUMN notes SET STORAGE MAIN;
COMMENT ON TABLE dbw_fid_customers IS 'People who buy things';
COMMENT ON COLUMN dbw_fid_customers.email IS 'Login address';
CREATE INDEX ix_dbw_fid_lower_email ON dbw_fid_customers (lower(email));
CREATE INDEX ix_dbw_fid_partial ON dbw_fid_customers (email) WHERE notes IS NOT NULL;
ALTER TABLE dbw_fid_customers ENABLE ROW LEVEL SECURITY;
CREATE POLICY p_dbw_fid_self ON dbw_fid_customers USING (true);

CREATE TABLE dbw_fid_orders (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    customer_id INTEGER NOT NULL
        REFERENCES dbw_fid_customers (id) ON DELETE CASCADE ON UPDATE RESTRICT,
    status dbw_fid_status NOT NULL DEFAULT 'queued',
    total NUMERIC(12, 2),
    total_with_tax NUMERIC(12, 2) GENERATED ALWAYS AS (total * 1.22) STORED
);
ALTER TABLE dbw_fid_orders SET (fillfactor = 70);

CREATE TABLE dbw_fid_bookings (
    id SERIAL PRIMARY KEY,
    room TEXT,
    during tstzrange,
    EXCLUDE USING gist (room WITH =, during WITH &&)
);
"""

DROP_SQL = """
DROP TABLE IF EXISTS dbw_fid_bookings CASCADE;
DROP TABLE IF EXISTS dbw_fid_orders CASCADE;
DROP TABLE IF EXISTS dbw_fid_customers CASCADE;
DROP TYPE IF EXISTS dbw_fid_status CASCADE;
"""


def _pg_url() -> str:
    host = os.environ.get("PG_HOST")
    port = os.environ.get("PG_PORT")
    if host and port:
        user = os.environ.get("PG_USER", "postgres")
        password = os.environ.get("PG_PASSWORD", "postgres")
        database = os.environ.get("PG_DATABASE", "dbwarden_test")
        return f"postgresql://{user}:{password}@{host}:{port}/{database}"

    pytest.importorskip("testcontainers.postgres")
    from testcontainers.postgres import PostgresContainer

    container = PostgresContainer("postgres:16-alpine")
    container.__enter__()
    _pg_url._container = container  # type: ignore[attr-defined]
    return container.get_connection_url().replace("+psycopg2", "")


def _run(url: str, script: str) -> None:
    engine = sa.create_engine(url)
    try:
        with engine.begin() as conn:
            for statement in script.split(";"):
                body = "\n".join(
                    line for line in statement.splitlines()
                    if line.strip() and not line.strip().startswith("--")
                ).strip()
                if body:
                    conn.execute(sa.text(body))
    finally:
        engine.dispose()


@pytest.fixture(scope="module")
def pg_url(request):
    if not request.config.getoption("--pg-integration"):
        pytest.skip("needs --pg-integration")
    url = _pg_url()
    yield url
    container = getattr(_pg_url, "_container", None)
    if container is not None:
        container.__exit__(None, None, None)


@pytest.fixture(scope="module")
def snapshot(pg_url):
    _run(pg_url, SCHEMA_SQL)
    try:
        yield extract_full_schema_snapshot(
            sqlalchemy_url=pg_url, database_type="postgresql",
        )
    finally:
        _run(pg_url, DROP_SQL)


def _columns(snapshot, table):
    return snapshot["tables"][table]["columns"]


def _pg_column(snapshot, table, column):
    return _columns(snapshot, table)[column].get("pg_column") or {}


@pytest.mark.integration
class TestRolesSurviveExtraction:
    """A table with an RLS policy used to wipe the snapshot's roles.

    The policy loop bound its list of policy roles to the same name as the
    function's role accumulator, so every later role lookup raised and the
    handler recorded nothing.
    """

    def test_roles_is_a_mapping(self, snapshot):
        assert isinstance(snapshot.get("roles"), dict)

    def test_the_connecting_role_is_recorded(self, snapshot):
        expected = os.environ.get("PG_USER", "postgres")
        assert expected in snapshot["roles"]

    def test_the_policy_is_still_captured(self, snapshot):
        policies = snapshot["tables"]["dbw_fid_customers"].get("pg_policies")
        assert policies and policies[0]["name"] == "p_dbw_fid_self"


@pytest.mark.integration
class TestTableMetadataFidelity:
    def test_table_and_column_comments(self, snapshot):
        assert snapshot["tables"]["dbw_fid_customers"]["comment"] == "People who buy things"
        assert _columns(snapshot, "dbw_fid_customers")["email"]["comment"] == "Login address"

    def test_row_level_security(self, snapshot):
        pg_table = snapshot["tables"]["dbw_fid_customers"].get("pg_table") or {}
        assert pg_table.get("pg_rls")

    def test_storage_parameters(self, snapshot):
        pg_table = snapshot["tables"]["dbw_fid_orders"].get("pg_table") or {}
        assert pg_table.get("pg_storage_params")

    def test_exclude_constraint(self, snapshot):
        pg_table = snapshot["tables"]["dbw_fid_bookings"].get("pg_table") or {}
        assert pg_table.get("pg_excludes")


@pytest.mark.integration
class TestColumnMetadataFidelity:
    def test_non_default_storage(self, snapshot):
        assert _pg_column(snapshot, "dbw_fid_customers", "notes").get("storage") == "MAIN"

    def test_collation(self, snapshot):
        assert _pg_column(snapshot, "dbw_fid_customers", "name").get("collation")

    def test_identity_column(self, snapshot):
        assert _pg_column(snapshot, "dbw_fid_orders", "id").get("identity")

    def test_generated_column(self, snapshot):
        assert _pg_column(snapshot, "dbw_fid_orders", "total_with_tax").get("generated")

    def test_numeric_precision_and_scale(self, snapshot):
        total = _columns(snapshot, "dbw_fid_orders")["total"]
        assert total.get("precision") == 12
        assert total.get("scale") == 2

    def test_array_and_json_types(self, snapshot):
        columns = _columns(snapshot, "dbw_fid_customers")
        assert "array" in str(columns["tags"].get("type", ""))
        assert "json" in str(columns["payload"].get("type", ""))

    def test_enum_column_and_type(self, snapshot):
        assert "enum" in str(_columns(snapshot, "dbw_fid_orders")["status"].get("type", ""))
        assert snapshot.get("enums")


@pytest.mark.integration
class TestConstraintAndIndexFidelity:
    @staticmethod
    def _constraints(snapshot, kind):
        return [c for c in snapshot["constraints"].values() if c.get("type") == kind]

    def test_unique_constraint(self, snapshot):
        assert any(
            "dbw_fid_customers" in str(c.get("table"))
            for c in self._constraints(snapshot, "unique")
        )

    def test_check_constraint(self, snapshot):
        assert any(
            c.get("name") == "ck_dbw_fid_email"
            for c in self._constraints(snapshot, "check")
        )

    def test_foreign_key_actions(self, snapshot):
        foreign_keys = self._constraints(snapshot, "foreign_key")
        assert any(
            c.get("on_delete") == "CASCADE" and c.get("on_update") == "RESTRICT"
            for c in foreign_keys
        )

    def test_expression_index(self, snapshot):
        assert any(
            index.get("name") == "ix_dbw_fid_lower_email"
            for index in snapshot["indexes"].values()
        )

    def test_partial_index_keeps_its_predicate(self, snapshot):
        partial = [
            index for index in snapshot["indexes"].values()
            if index.get("name") == "ix_dbw_fid_partial"
        ]
        assert partial, "partial index missing from snapshot"
        assert partial[0].get("where"), "index predicate was dropped"
