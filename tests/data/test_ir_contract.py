import copy
from decimal import Decimal

import pytest

from dbwarden.data.ir import (
    canonical_bytes,
    digest,
    empty_spec,
    seal_spec,
    validate_spec,
)


def test_version_one_canonical_encoding_and_digest_golden_vector():
    value = {
        "nothing": None,
        "label": "cafe\u0301",
        "id": 7,
        "amount": Decimal("12.500"),
    }
    assert (
        canonical_bytes(value)
        == b'{"amount":{"$decimal":"12.5"},"id":7,"label":"caf\xc3\xa9","nothing":null}'
    )
    assert (
        digest(value, "golden")
        == "644f1fb17905cc3da674e7d68e2ed0569d0047dc187772896136e3e39f9b5db6"
    )
    assert digest(value, "other-domain") != digest(value, "golden")
    assert digest({**value, "id": "7"}, "golden") != digest(value, "golden")
    spec = empty_spec("primary", "sqlite")
    assert seal_spec({**spec, "canonical_checksum": "not-a-preimage"}) == spec


def _managed():
    return {
        "declaration_id": "app.models:Country.Data.managed_rows",
        "target_table": {"database": "primary", "schema": None, "table": "countries"},
        "key_columns": ["code"],
        "row_source": {
            "kind": "inline",
            "rows": [],
            "checksum": digest([], "row-source"),
        },
        "owned_columns": ["name"],
        "on_missing": "keep",
        "scope": None,
        "acknowledgement": False,
        "rollback": {"policy": "irreversible"},
        "category": None,
    }


def _with_declarations(*records):
    spec = empty_spec("primary", "sqlite")
    spec["managed_rows"] = list(records)
    ordered = sorted(records, key=lambda item: item["declaration_id"])
    spec["declaration_set"] = {
        "declaration_ids": [item["declaration_id"] for item in ordered],
        "declaration_checksum": digest(ordered, "declarations"),
    }
    return seal_spec(spec)


def _reseal(spec, mutate):
    value = copy.deepcopy(spec)
    mutate(value)
    return seal_spec(value)


def test_empty_spec_satisfies_structural_contract():
    spec = empty_spec("primary", "sqlite")
    assert validate_spec(spec) is spec


@pytest.mark.parametrize(
    "field",
    ["spec_version", "expression_language_version", "backend_semantics_version"],
)
def test_versions_reject_booleans(field):
    spec = _reseal(
        empty_spec("primary", "sqlite"), lambda value: value.__setitem__(field, True)
    )
    with pytest.raises(ValueError, match="Unsupported data"):
        validate_spec(spec)


def test_required_top_level_records_cannot_be_omitted():
    spec = _reseal(
        empty_spec("primary", "sqlite"), lambda value: value.pop("transitions")
    )
    with pytest.raises(ValueError, match="missing required fields: transitions"):
        validate_spec(spec)


def test_declaration_ids_and_content_checksum_must_match_records():
    record = _managed()
    spec = _with_declarations(record)
    assert validate_spec(spec) is spec

    ids_only = _reseal(
        spec,
        lambda value: value["declaration_set"].__setitem__(
            "declaration_checksum", digest([record["declaration_id"]], "declarations")
        ),
    )
    with pytest.raises(ValueError, match="declaration checksum mismatch"):
        validate_spec(ids_only)

    missing_id = _reseal(
        spec,
        lambda value: value["declaration_set"].__setitem__("declaration_ids", []),
    )
    with pytest.raises(ValueError, match="declaration_ids do not match"):
        validate_spec(missing_id)


def test_table_references_and_policies_are_validated():
    malformed_table = _reseal(
        _with_declarations(_managed()),
        lambda value: value["managed_rows"][0]["target_table"].__setitem__(
            "database", "other"
        ),
    )
    with pytest.raises(ValueError, match="table reference database"):
        validate_spec(malformed_table)

    malformed_policy = _reseal(
        _with_declarations(_managed()),
        lambda value: value["managed_rows"][0].__setitem__("on_missing", "ignore"),
    )
    with pytest.raises(ValueError, match="on_missing policy"):
        validate_spec(malformed_policy)


def test_extension_metadata_remains_canonical_and_non_breaking():
    spec = empty_spec("primary", "sqlite")
    spec["merges"] = []
    spec["execution"] = {
        "batch": {"key": ["id"], "size": 100},
        "extension_version": 1,
    }
    spec = seal_spec(spec)
    assert validate_spec(spec)["execution"]["batch"]["size"] == 100


def test_snapshot_lineage_matches_database_and_has_typed_version():
    spec = empty_spec("primary", "sqlite")
    spec["source_snapshots"] = [
        {
            "format_version": True,
            "snapshot_id": "primary__0001",
            "database": "primary",
            "backend": "sqlite",
            "schema_checksum": "abc",
            "data_checksum": None,
            "created_by_migration": "primary__0001",
            "parent_snapshot_ids": [],
        }
    ]
    with pytest.raises(ValueError, match="format_version"):
        validate_spec(seal_spec(spec))
