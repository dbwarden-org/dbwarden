from __future__ import annotations

import json
from typing import Any


_CH_COLUMN_KEYS: frozenset[str] = frozenset({
    "ch_codec",
    "ch_default_expression",
    "ch_materialized",
    "ch_alias",
    "ch_ephemeral",
    "ch_ttl",
    "ch_low_cardinality",
    "ch_nullable",
    "ch_type",
})


def _clean_clickhouse_expression(val: Any) -> str | None:
    if val is None:
        return None
    s = str(val).strip('\x00').strip()
    return s if s else None


def _serialize_clickhouse_engine(engine: Any) -> str | tuple | None:
    if engine is None:
        return None
    if isinstance(engine, dict):
        name = engine.get("name")
        if not name:
            return None
        args = list(engine.get("args", []) or [])
        if engine.get("zookeeper_path") is not None:
            args.insert(0, engine["zookeeper_path"])
        if engine.get("replica_name") is not None:
            args.insert(1 if engine.get("zookeeper_path") is not None else 0, engine["replica_name"])
        if not args:
            return name
        return tuple([name] + args)
    if hasattr(engine, "name"):
        args = [engine.name]
        if getattr(engine, "zookeeper_path", None) is not None:
            args.append(engine.zookeeper_path)
        if getattr(engine, "replica_name", None) is not None:
            args.append(engine.replica_name)
        args.extend(list(getattr(engine, "args", ()) or ()))
        return args[0] if len(args) == 1 else tuple(args)
    return engine


def _split_top_level(s: str, delimiter: str = ",") -> list[str]:
    """Split *s* on *delimiter* respecting balanced parentheses."""
    parts: list[str] = []
    depth = 0
    current: list[str] = []
    for ch in s:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        if ch == delimiter and depth == 0:
            parts.append("".join(current).strip())
            current = []
            continue
        current.append(ch)
    tail = "".join(current).strip()
    if tail:
        parts.append(tail)
    return parts


def _pick_clickhouse_codec(codec_expr: Any) -> str | None:
    if codec_expr is None:
        return None
    codec = str(codec_expr).strip()
    if not codec:
        return None
    # Strip the CODEC(...) wrapper if present.
    if codec.upper().startswith("CODEC(") and codec.endswith(")"):
        codec = codec[6:-1].strip()
    if not codec:
        return None
    parts = [part.strip() for part in _split_top_level(codec) if part.strip()]
    if not parts:
        return None
    non_default = [part for part in parts if part.upper() not in {"LZ4", "NONE"}]
    if not non_default:
        return None
    return ", ".join(non_default)


def _check_ch_engine_recreate_allowed(snap_spec: dict, model_spec: dict, table_name: str) -> None:
    reasons: list[str] = []
    for spec, label in [(snap_spec, "current"), (model_spec, "new")]:
        if spec.get("ch_object_type") == "materialized_view" and spec.get("ch_to_table"):
            reasons.append(f"is a materialized view with 'TO {spec['ch_to_table']}' ({label})")
        elif spec.get("ch_select_statement") and spec.get("ch_to_table"):
            reasons.append(f"has a SELECT statement and 'TO' target ({label})")
    if reasons:
        raise ValueError(
            f"ClickHouse table '{table_name}' cannot be automatically recreated: "
            f"{'; '.join(reasons)}. "
            "Handle manually or use --force to skip this check."
        )


_LOSSY_CH_ENGINE_FAMILIES: frozenset[str] = frozenset({
    "aggregatingmergetree",
    "collapsingmergetree",
    "replacingmergetree",
    "summingmergetree",
    "versionedcollapsingmergetree",
})

_ROW_PRESERVING_CH_ENGINE_FAMILIES: frozenset[str] = frozenset({
    "log",
    "memory",
    "mergetree",
    "replicatedmergetree",
    "stripelog",
    "tinylog",
})


def _clickhouse_engine_family(engine: Any) -> str | None:
    serialized = _serialize_clickhouse_engine(engine)
    if serialized is None:
        return None
    if isinstance(serialized, (tuple, list)):
        if not serialized:
            return None
        name = str(serialized[0])
    else:
        name = str(serialized)
    name = name.strip()
    if not name:
        return None
    return name.split("(", 1)[0].strip().lower()


def classify_clickhouse_recreate_rollback(from_engine: Any, to_engine: Any) -> tuple[str, str]:
    """Classify whether a ClickHouse recreate transition has safe rollback.

    Rollback is considered safe only when both engines are known row-preserving.
    Lossy or unknown families are irreversible by policy.
    """
    from_family = _clickhouse_engine_family(from_engine)
    to_family = _clickhouse_engine_family(to_engine)

    def _is_lossy(family: str | None) -> bool:
        if family in _LOSSY_CH_ENGINE_FAMILIES:
            return True
        if family and family.startswith("replicated"):
            unreplicated = family.removeprefix("replicated")
            return unreplicated in _LOSSY_CH_ENGINE_FAMILIES
        return False

    if _is_lossy(from_family) or _is_lossy(to_family):
        return (
            "irreversible",
            f"ClickHouse engine transition {from_family or 'unknown'} -> {to_family or 'unknown'} may collapse, deduplicate, aggregate, or otherwise lose row-level detail.",
        )
    if (
        from_family in _ROW_PRESERVING_CH_ENGINE_FAMILIES
        and to_family in _ROW_PRESERVING_CH_ENGINE_FAMILIES
    ):
        return (
            "conditional",
            f"ClickHouse engine transition {from_family} -> {to_family} is known row-preserving; rollback still depends on successful recreate round-trip.",
        )
    return (
        "irreversible",
        f"ClickHouse engine transition {from_family or 'unknown'} -> {to_family or 'unknown'} is not statically proven row-preserving.",
    )


def _diff_ch_column_extras(
    snap_ch_col: dict,
    model_ch_col: dict,
    table_name: str,
    col_name: str,
    upgrade_ops: list[dict],
    rollback_ops: list[dict],
) -> None:
    if json.dumps(snap_ch_col, sort_keys=True, default=str) != json.dumps(model_ch_col, sort_keys=True, default=str):
        upgrade_ops.append({
            "type": "alter_ch_column",
            "table": table_name,
            "column": col_name,
            "from_ch_column": snap_ch_col,
            "to_ch_column": model_ch_col,
        })
        rollback_ops.append({
            "type": "alter_ch_column",
            "table": table_name,
            "column": col_name,
            "from_ch_column": model_ch_col,
            "to_ch_column": snap_ch_col,
        })
