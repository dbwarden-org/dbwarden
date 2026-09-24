from decimal import Decimal

import pytest
from sqlalchemy import create_engine, text

from dbwarden.data.artifacts import build_manifest
from dbwarden.data.execution import (
    _prepare_identity_edges,
    _record_identity_edges,
    verify_identity_edges,
)
from dbwarden.data.frozen import render_frozen
from dbwarden.data.ir import canonical_bytes, digest, empty_spec, seal_spec
from tests.data.test_identity_collisions import NFC, _schema, _step


def test_canonical_hash_golden_vector_and_typed_identity_domains():
    value = {"z": [None, True, 1, "1"], "e\u0301": Decimal("1.00")}
    assert (
        canonical_bytes(value)
        == '{"z":[null,true,1,"1"],"é":{"$decimal":"1"}}'.encode()
    )
    assert (
        digest(value, "golden")
        == "8a4ce3e34b63a18f74bb35f7a74fa13431867e8d8935cd36c3cdc6599d6f4d0e"
    )
    assert (
        len(
            {
                digest([value], "identity")
                for value in (None, False, 0, "0", 0.0, Decimal(0))
            }
        )
        == 6
    )
    assert digest([1], "source") != digest([1], "target")


def test_integrity_hashes_exclude_their_own_stored_values():
    spec = empty_spec("primary", "sqlite")
    assert seal_spec({**spec, "canonical_checksum": "irrelevant"}) == spec
    plan = {"migration_id": "primary__0001", "data_spec": spec}
    frozen = render_frozen(spec, "primary__0001")
    manifest = build_manifest("SELECT 1;", plan, frozen)
    assert (
        build_manifest("SELECT 1;", {**plan, "data_bundle": manifest}, frozen)
        == manifest
    )
    assert [item["name"] for item in manifest["members"]] == [
        "primary__0001.sql",
        "primary__0001.plan.json",
        "primary__0001.data.py",
    ]
    assert manifest["members"][0]["byte_length"] == len(b"SELECT 1;")


def test_retained_hmac_keys_verify_old_edges_after_rotation():
    engine = create_engine("sqlite://")
    with engine.connect() as connection:
        _schema(connection)
        connection.execute(text("INSERT INTO values_table VALUES (1, 7)"))
        connection.execute(
            text("INSERT INTO transition_edges VALUES (:source, 1, 7, 1)"),
            {"source": NFC},
        )
        step = _step()
        _prepare_identity_edges(connection, [step], clickhouse=False)
        _record_identity_edges(
            connection,
            step,
            "primary__0001",
            1,
            "run-1",
            "copy_identity",
            clickhouse=False,
        )
        old_id = connection.execute(
            text("SELECT key_id FROM _dbwarden_data_keys")
        ).scalar_one()
        connection.execute(text("UPDATE _dbwarden_data_keys SET status='RETIRED'"))
        connection.execute(
            text(
                "INSERT INTO _dbwarden_data_keys VALUES ('new-key', :material, 'ACTIVE', '9999')"
            ),
            {"material": "ab" * 32},
        )
        _record_identity_edges(
            connection,
            step,
            "primary__0001",
            1,
            "run-2",
            "copy_identity",
            clickhouse=False,
        )
        assert verify_identity_edges(connection, step, "primary__0001", 1)["count"] == 1
        assert (
            connection.execute(
                text("SELECT key_id FROM _dbwarden_data_edges")
            ).scalar_one()
            == old_id
        )
        connection.execute(
            text("DELETE FROM _dbwarden_data_keys WHERE key_id=:id"), {"id": old_id}
        )
        with pytest.raises(
            ValueError, match="identity edge|identity-edge|Identity-edge"
        ):
            verify_identity_edges(connection, step, "primary__0001", 1)
