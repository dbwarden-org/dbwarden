from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass

from .declarations import HistoricalTable, column_names
from .expressions import Expr, lower_expression
from .ir import canonical_bytes, digest


@dataclass(frozen=True)
class MergeInput:
    source: HistoricalTable
    identity: tuple[str, ...]
    mappings: dict
    priority: int


@dataclass(frozen=True)
class MergeValue:
    policy: str
    expression: Expr


@dataclass(frozen=True)
class MergedSource:
    inputs: tuple[MergeInput, ...]
    key: tuple[str, ...]
    values: dict[str, MergeValue]
    allow_empty: bool = False


def from_source(source, *, identity, map, priority):
    if not isinstance(source, HistoricalTable):
        raise TypeError(
            "Merge inputs require explicitly pinned historical_table sources"
        )
    keys = column_names(identity)
    if not keys or not isinstance(map, dict) or not map:
        raise ValueError(
            "Merge inputs require stable identity columns and mapped values"
        )
    if type(priority) is not int:
        raise ValueError("Merge source priority must be an integer")
    mappings = dict(zip(column_names(list(map)), map.values()))
    if any(name.startswith("_dbw_") for name in mappings):
        raise ValueError("Merge projection names cannot use the reserved _dbw_ prefix")
    return MergeInput(source, keys, mappings, priority)


def aggregate(policy, expression):
    if policy not in {"sum", "min", "max", "count", "avg", "require_equal"}:
        raise ValueError(
            "Merge aggregate must be sum, min, max, count, avg, or require_equal"
        )
    if not isinstance(expression, Expr):
        raise TypeError("Merge aggregates require a declarative expression")
    return MergeValue(policy, expression)


def winner(expression):
    if not isinstance(expression, Expr):
        raise TypeError("Merge winners require a declarative expression")
    return MergeValue("winner", expression)


def merge_sources(*inputs, key, values, allow_empty=False):
    keys = column_names(key)
    if len(inputs) < 2 or any(not isinstance(item, MergeInput) for item in inputs):
        raise ValueError("merge_sources requires at least two from_source inputs")
    if not keys or not isinstance(values, dict) or not values:
        raise ValueError(
            "merge_sources requires grouping keys and explicit value rules"
        )
    names = column_names(list(values))
    if set(names) & set(keys) or any(
        not isinstance(item, MergeValue) for item in values.values()
    ):
        raise ValueError(
            "Every non-key merge value requires aggregate(...) or winner(...)"
        )
    if any(name.startswith("_dbw_") for name in (*keys, *names)):
        raise ValueError("Merge output names cannot use the reserved _dbw_ prefix")
    if len({item.priority for item in inputs}) != len(inputs):
        raise ValueError("Merge source priorities must be distinct")
    if type(allow_empty) is not bool:
        raise TypeError("Merge allow_empty must be boolean")
    return MergedSource(
        tuple(sorted(inputs, key=lambda item: item.priority)),
        keys,
        dict(values),
        allow_empty,
    )


def compile_merge_source(
    merged, *, database, backend, parameters, registry_path, schema_dir
):
    from .compiler import (
        _expr,
        _expression_info,
        _snapshot_column_info,
        _snapshot_columns,
    )
    from .snapshots import resolve_snapshot

    if backend == "clickhouse":
        raise ValueError("ClickHouse merges require a source write barrier")
    inputs, projections, seen = [], {}, set()
    for source in merged.inputs:
        ref = source.source
        name = f"{ref.schema}.{ref.table}" if ref.schema else ref.table
        if name in seen:
            raise ValueError("A merge cannot read the same historical table twice")
        seen.add(name)
        snapshot = resolve_snapshot(
            ref.snapshot,
            database=database,
            backend=backend,
            registry_path=registry_path,
            schema_dir=schema_dir,
        )
        tables = snapshot["schema_state"]["tables"]
        state = tables.get(name)
        bare = tables.get(ref.table)
        if ref.schema and isinstance(bare, dict) and bare.get("schema") == ref.schema:
            if state is not None and canonical_bytes(state) != canonical_bytes(bare):
                raise ValueError(f"Merge source {name} is ambiguous in pinned snapshot")
            state = bare
        if not isinstance(state, dict) or state.get("schema") not in (None, ref.schema):
            raise ValueError(
                f"Merge source is absent or has a different schema: {name}"
            )
        columns = _snapshot_columns(state.get("columns"), table=name)
        info = _snapshot_column_info(columns, table=name)
        if any(
            key not in columns or columns[key].get("nullable", True)
            for key in source.identity
        ):
            raise ValueError(
                "Merge source identities must name non-null snapshot columns"
            )
        if any(info[key][0] not in {"integer", "string"} for key in source.identity):
            raise ValueError(
                "Merge identities require integer or string columns for stable byte ordering"
            )
        resolved = {}
        for field, expression in source.mappings.items():
            value = _expr(
                expression,
                columns=[
                    value
                    for column in columns
                    for value in (column, f"{ref.table}.{column}")
                ],
                parameters=parameters,
                backend=backend,
            )
            family, nullable = _expression_info(value, info)
            resolved[field] = value
            if field in projections and projections[field][0] != family:
                raise ValueError(
                    f"Merge projection {field} needs an explicit common type cast"
                )
            projections[field] = (
                family,
                nullable or projections.get(field, (None, False))[1],
            )
        if inputs and set(resolved) != set(inputs[0]["mappings"]):
            raise ValueError("All merge inputs must project the same named fields")
        if not set(merged.key) <= resolved.keys():
            raise ValueError("Merge grouping keys must be present in every input")
        inputs.append(
            {
                "source": {
                    "database": database,
                    "schema": ref.schema,
                    "table": ref.table,
                },
                "identity": list(source.identity),
                "mappings": resolved,
                "priority": source.priority,
                "columns": sorted(columns),
                "snapshot": {
                    key: value
                    for key, value in snapshot.items()
                    if key != "schema_state"
                },
            }
        )
    rules, output = {}, {}
    type_names = {
        "integer": "INTEGER",
        "numeric": "NUMERIC",
        "float": "FLOAT",
        "string": "TEXT",
        "boolean": "BOOLEAN",
        "date": "DATE",
        "datetime": "TIMESTAMP",
    }
    for key in merged.key:
        if projections[key][0] not in {"integer", "string"}:
            raise ValueError(
                "Merge grouping keys require integer or string expressions; use an explicit cast"
            )
        output[key] = {
            "type": type_names.get(projections[key][0], "TEXT"),
            "nullable": False,
        }
    for field, rule in merged.values.items():
        expression = _expr(
            rule.expression,
            columns=list(projections),
            backend=backend,
            parameters=parameters,
        )
        family, nullable = _expression_info(expression, projections)
        if rule.policy in {"sum", "avg"} and family not in {
            "integer",
            "numeric",
            "float",
        }:
            raise ValueError(f"Merge {rule.policy} requires a numeric expression")
        if rule.policy == "count":
            family, nullable = "integer", False
        elif rule.policy == "avg":
            family = "numeric"
        output[field] = {"type": type_names.get(family, "TEXT"), "nullable": nullable}
        rules[field] = {
            "policy": rule.policy,
            "expression": expression,
            "family": family,
        }
    metadata = {
        "inputs": inputs,
        "key": list(merged.key),
        "values": rules,
        "key_families": {key: projections[key][0] for key in merged.key},
        "allow_empty": merged.allow_empty,
        "ordering": "priority_then_identity_utf8_hex_v1",
    }
    identity = digest(metadata, "merge-source")
    table = "_dbwarden_merge_" + identity[:20]
    snapshot = {
        "format_version": 1,
        "snapshot_id": "merge_" + identity,
        "database": database,
        "backend": backend,
        "schema_checksum": digest(output, "merge-schema"),
        "data_checksum": None,
        "created_by_migration": None,
        "parent_snapshot_ids": sorted(
            {item["snapshot"]["snapshot_id"] for item in inputs}
        ),
        "schema_state": {"tables": {table: {"columns": output}}},
    }
    return HistoricalTable(table, snapshot=snapshot["snapshot_id"]), snapshot, metadata


def plan_merge(item, previous, backend):
    from .planning import (
        _guard,
        _mysql_rename_step,
        _new_step,
        _same,
        _transition,
        quote,
        table_sql,
    )

    if previous is not None:
        raise ValueError(
            "Merge revisions require a new declaration and explicitly pinned retained sources"
        )
    if len(item["targets"]) != 1:
        raise ValueError("A many-source merge requires exactly one target")
    if item["completion"]["policy"] not in {"preserve", "drop"}:
        raise ValueError("Merged sources use preserve or acknowledged drop completion")
    if item["coverage"]["mode"] != "all" or item["coverage"]["on_unmatched"] != "error":
        raise ValueError("Merged groups require complete coverage")
    merged = item["merge"]
    q = lambda value: quote(value, backend)
    stage = table_sql(item["source"], backend)
    prefix = item["transition_id"][:16]
    inputs_table = q("_dbwarden_merge_inputs_" + prefix)
    inputs = merged["inputs"]
    keys = merged["key"]
    union, before, prefix_steps, retire, restore = [], [], [], [], []
    freeze_steps = []
    identity_width = max(len(source["identity"]) for source in inputs)
    for index, source in enumerate(inputs):
        ref = source["source"]
        active = ref
        if backend in {"mysql", "mariadb"}:
            active = {
                **ref,
                "table": "_dbwarden_merge_frozen_" + prefix + "_" + str(index),
            }
            freeze_steps.append(_mysql_rename_step(ref, active, backend))
        table = table_sql(active, backend)
        aliases = {ref["table"]: "s", "": "s"}
        projected = {
            key: lower_expression(value, backend, aliases=aliases)
            for key, value in source["mappings"].items()
        }
        ids = [f"s.{q(key)}" for key in source["identity"]]
        before.extend(
            [
                _guard(
                    f"SELECT COUNT(*) FROM {table} s WHERE "
                    + " OR ".join(f"{value} IS NULL" for value in ids),
                    "Merge source identity contains NULL",
                ),
                _guard(
                    f"SELECT COUNT(*) FROM (SELECT {', '.join(ids)} FROM {table} s GROUP BY {', '.join(ids)} HAVING COUNT(*) > 1) d",
                    "Merge source identity is not unique",
                ),
                _guard(
                    f"SELECT COUNT(*) FROM {table} s WHERE "
                    + " OR ".join(f"({projected[key]}) IS NULL" for key in keys),
                    "Merge grouping key contains NULL",
                ),
            ]
        )
        sort_type = "CHAR" if backend in {"mysql", "mariadb"} else "TEXT"
        encoded_ids = [
            f"encode(convert_to(CAST({value} AS TEXT), 'UTF8'), 'hex')"
            if backend == "postgresql"
            else f"HEX(CAST({value} AS {sort_type}))"
            for value in ids
        ]
        union.append(
            "SELECT "
            + ", ".join(
                [f"{value} AS {q(key)}" for key, value in projected.items()]
                + [f"{source['priority']} AS {q('_dbw_priority')}"]
                + [
                    f"{encoded_ids[i]} AS {q('_dbw_id' + str(i))}"
                    if i < len(ids)
                    else f"NULL AS {q('_dbw_id' + str(i))}"
                    for i in range(identity_width)
                ]
            )
            + f" FROM {table} s"
        )
        if item["completion"]["policy"] == "preserve":
            preserved = {
                **ref,
                "table": "_dbwarden_merge_preserved_" + prefix + "_" + str(index),
            }
            if backend in {"mysql", "mariadb"}:
                retire.append(_mysql_rename_step(active, preserved, backend))
                restore.append(_mysql_rename_step(preserved, ref, backend))
            else:
                retire.append(
                    _new_step(
                        [f"ALTER TABLE {table} RENAME TO {q(preserved['table'])};"]
                    )
                )
                restore.append(
                    _new_step(
                        [
                            f"ALTER TABLE {table_sql(preserved, backend)} RENAME TO {q(ref['table'])};"
                        ]
                    )
                )
        else:
            retire.append(_new_step([f"DROP TABLE {table};"]))
    union_sql = " UNION ALL ".join(union)
    if not merged.get("allow_empty", False):
        before.append(
            _guard(
                f"SELECT COUNT(*) FROM ({union_sql}) u",
                "Merge inputs are empty; set allow_empty=True to permit this",
                minimum=1,
                maximum=None,
            )
        )
    if item["execution"]["max_rows"] is not None:
        before.append(
            _guard(
                f"SELECT COUNT(*) FROM ({union_sql}) u",
                "Merge input row limit exceeded",
                maximum=item["execution"]["max_rows"],
            )
        )
    if freeze_steps:
        prefix_steps.append(
            _new_step(
                [
                    "RENAME TABLE "
                    + ", ".join(
                        step["sql"][0].removeprefix("RENAME TABLE ").rstrip(";")
                        for step in freeze_steps
                    )
                    + ";"
                ],
                [guard for step in freeze_steps for guard in step["guards"]],
            )
        )
    prefix_steps.append(_new_step(guards=before))
    prefix_steps.append(
        _new_step(
            [
                f"CREATE TABLE {inputs_table} AS SELECT * FROM ({union_sql}) u WHERE FALSE;",
                f"INSERT INTO {inputs_table} {union_sql};",
            ]
        )
    )

    def binary_value(value):
        return (
            f"encode(convert_to(CAST({value} AS TEXT), 'UTF8'), 'hex')"
            if backend == "postgresql"
            else f"HEX(CAST({value} AS CHAR))"
            if backend in {"mysql", "mariadb"}
            else f"HEX(CAST({value} AS TEXT))"
        )

    partition = ", ".join(
        binary_value(q(key)) if merged["key_families"][key] == "string" else q(key)
        for key in keys
    )
    ordering = ", ".join(
        [q("_dbw_priority"), *[q("_dbw_id" + str(i)) for i in range(identity_width)]]
    )
    ranked = f"(SELECT *, ROW_NUMBER() OVER (PARTITION BY {partition} ORDER BY {ordering}) AS {q('_dbw_rank')} FROM {inputs_table}) u"
    fields = [f"MIN({q(key)}) AS {q(key)}" for key in keys]
    rules_guards = []
    rules_guards.append(
        _guard(
            f"SELECT COUNT(*) FROM (SELECT {partition}, {ordering} FROM {inputs_table} GROUP BY {partition}, {ordering} HAVING COUNT(*) > 1) d",
            "Merge source identity encoding is not unique",
        )
    )
    for field, rule in merged["values"].items():
        value = lower_expression(rule["expression"], backend)
        policy = rule["policy"]
        if policy == "winner":
            expression = f"MAX(CASE WHEN {q('_dbw_rank')} = 1 THEN {value} END)"
        else:
            expression = (
                f"{'MIN' if policy == 'require_equal' else policy.upper()}({value})"
            )
        if policy == "require_equal":
            compared = binary_value(value) if rule["family"] == "string" else value
            rules_guards.append(
                _guard(
                    f"SELECT COUNT(*) FROM (SELECT {partition} FROM {inputs_table} GROUP BY {partition} "
                    f"HAVING COUNT(DISTINCT {compared}) > 1 OR (COUNT({value}) > 0 AND COUNT({value}) < COUNT(*))) d",
                    "Merge require_equal contributions differ",
                )
            )
        fields.append(f"{expression} AS {q(field)}")
    grouped = f"SELECT {', '.join(fields)} FROM {ranked} GROUP BY {partition}"
    prefix_steps.append(
        _new_step(
            [
                f"CREATE TABLE {stage} AS SELECT * FROM ({grouped}) g WHERE FALSE;",
                f"INSERT INTO {stage} {grouped};",
            ],
            rules_guards,
        )
    )
    inner = deepcopy(item)
    inner.pop("merge")
    up, down, severity = _transition(inner, None, backend)
    provenance, provenance_down = [], []
    target = item["targets"][0]
    mappings = {
        name: lower_expression(
            value, backend, aliases={item["source"]["table"]: "m", "": "m"}
        )
        for name, value in target["mappings"].items()
    }
    runtime_stage = stage
    if backend in {"mysql", "mariadb"}:
        runtime_stage = q("_dbwarden_frozen_" + item["transition_id"][:16])
    for index, source in enumerate(inputs):
        ledger_name = "_dbwarden_merge_edges_" + prefix + "_" + str(index)
        ledger = q(ledger_name)
        ref = source["source"]
        active = (
            {**ref, "table": "_dbwarden_merge_frozen_" + prefix + "_" + str(index)}
            if backend in {"mysql", "mariadb"}
            else ref
        )
        projected = {
            name: lower_expression(value, backend, aliases={ref["table"]: "s", "": "s"})
            for name, value in source["mappings"].items()
        }
        join = " AND ".join(
            _same(binary_value(projected[key]), binary_value("m." + q(key)), backend)
            if merged["key_families"][key] == "string"
            else _same(projected[key], "m." + q(key), backend)
            for key in keys
        )
        columns = [
            f"s.{q(key)} AS {q('s_' + str(i))}"
            for i, key in enumerate(source["identity"])
        ]
        columns += [
            f"s.{q(key)} AS {q('p_' + str(i))}"
            for i, key in enumerate(source["columns"])
        ]
        columns += [
            f"{mappings[key]} AS {q('t_' + str(i))}"
            for i, key in enumerate(target["identity"])
        ]
        columns += [
            f"{value} AS {q('v_' + str(i))}"
            for i, value in enumerate(mappings.values())
        ]
        columns += [f"0 AS {q('owned_write')}"]
        query = f"SELECT {', '.join(columns)} FROM {table_sql(active, backend)} s JOIN {runtime_stage} m ON {join}"
        step = _new_step(
            [
                f"CREATE TABLE {ledger} AS SELECT * FROM ({query}) e WHERE FALSE;",
                f"INSERT INTO {ledger} {query};",
            ]
        )
        step["identity_edges"] = {
            "definition_id": digest(
                [item["transition_id"], index], "merge-contribution"
            ),
            "transition_id": item["transition_id"],
            "ledger_table": ledger_name,
            "source_columns": ["s_" + str(i) for i in range(len(source["identity"]))]
            + ["p_" + str(i) for i in range(len(source["columns"]))],
            "source_key_count": len(source["identity"]),
            "target_identity_columns": [
                "t_" + str(i) for i in range(len(target["identity"]))
            ],
            "value_columns": ["v_" + str(i) for i in range(len(mappings))],
            "target_table": target["target_table"],
            "target_columns": list(mappings),
            "target_key_columns": target["identity"],
            "owned_column": "owned_write",
        }
        if item["completion"]["policy"] == "preserve":
            step["identity_edges"]["retained_source"] = {
                "table": {
                    **ref,
                    "table": "_dbwarden_merge_preserved_" + prefix + "_" + str(index),
                },
                "key_columns": source["identity"],
                "columns": source["columns"],
                "key_indexes": list(range(len(source["identity"]))),
                "value_indexes": list(
                    range(
                        len(source["identity"]),
                        len(source["identity"]) + len(source["columns"]),
                    )
                ),
            }
            restore[index]["verify_edges"] = [
                {
                    "definition_id": step["identity_edges"]["definition_id"],
                    "source_only": True,
                }
            ]
        provenance.append(step)
        provenance_down.append(_new_step([f"DROP TABLE {ledger};"]))
    up = prefix_steps + up[:-1] + provenance + up[-1:] + retire
    if down:
        down = (
            down
            + [_new_step([f"DROP TABLE {stage};", f"DROP TABLE {inputs_table};"])]
            + restore
            + provenance_down
        )
    for step in up:
        step.setdefault("lock_tables", []).extend(source["source"] for source in inputs)
    return up, down, severity
