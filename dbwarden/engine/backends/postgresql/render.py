from __future__ import annotations

import re
from typing import Any

_POSTGRES_RESERVED_WORDS: set[str] = {
    "ALL", "ANALYSE", "ANALYZE", "AND", "ANY", "ARRAY", "AS", "ASC",
    "ASYMMETRIC", "AUTHORIZATION", "BETWEEN", "BINARY", "BOTH",
    "CASE", "CAST", "CHECK", "COLLATE", "COLLATION", "COLUMN",
    "CONCURRENTLY", "CONSTRAINT", "CREATE", "CROSS", "CURRENT_CATALOG",
    "CURRENT_DATE", "CURRENT_ROLE", "CURRENT_SCHEMA", "CURRENT_TIME",
    "CURRENT_TIMESTAMP", "CURRENT_USER", "DEFAULT", "DEFERRABLE",
    "DESC", "DISTINCT", "DO", "ELSE", "END", "EXCEPT", "EXISTS",
    "EXTRACT", "FALSE", "FETCH", "FILTER", "FOR", "FOREIGN", "FROM",
    "FULL", "FUNCTION", "GRANT", "GROUP", "GROUPING", "HAVING", "IF",
    "ILIKE", "IN", "INOUT", "INTERSECT", "INTERVAL", "INTO", "IS",
    "ISNULL", "JOIN", "LATERAL", "LEADING", "LEFT", "LIKE", "LIMIT",
    "LOCALTIME", "LOCALTIMESTAMP", "NATURAL", "NEW", "NOT", "NOTNULL",
    "NULL", "NULLIF", "OF", "OFFSET", "OLD", "ON", "ONLY", "OR",
    "ORDER", "OUT", "OUTER", "OVER", "OVERLAPS", "PARTITION", "PATH",
    "PLACING", "PRIMARY", "REFERENCES", "RETURNING", "RIGHT", "ROW",
    "ROWS", "RULES", "SCHEMA", "SELECT", "SESSION_USER", "SET",
    "SHOW", "SIMILAR", "SOME", "SYMMETRIC", "TABLE", "TABLESAMPLE",
    "THEN", "TO", "TRAILING", "TRUE", "UNION", "UNIQUE", "USER",
    "USING", "VARIADIC", "VERBOSE", "VIEW", "WHEN", "WHERE",
    "WINDOW", "WITH", "WITHIN", "WITHOUT", "XMLATTRIBUTES",
    "XMLCONCAT", "XMLELEMENT", "XMLEXISTS", "XMLFOREST",
    "XMLNAMESPACES", "XMLPARSE", "XMLPI", "XMLROOT", "XMLSERIALIZE",
    "XMLTABLE", "YEAR", "MONTH", "DAY", "HOUR", "MINUTE", "SECOND",
    "ZONE", "TYPE", "ADMIN", "AGGREGATE", "BACKWARD", "CACHE",
    "CLUSTER", "COMMENT", "COMMIT", "COPY", "DEALLOCATE", "DECLARE",
    "DISCARD", "DOMAIN", "EXTENSION", "FETCH", "FREEZE", "GLOBAL",
    "HANDLER", "IDENTITY", "IMPORT", "INDEX", "INHERIT", "LANGUAGE",
    "LISTEN", "LOAD", "LOCK", "LOGIN", "MAPPING", "MOVE", "NAME",
    "NAMESPACE", "NOTIFY", "OPTIONS", "OWNED", "OWNER", "PARALLEL",
    "PASSWORD", "PREPARE", "PREPARED", "PRIVILEGES", "PUBLIC",
    "PUBLICATION", "RECURSIVE", "REF", "REFRESH", "REINDEX",
    "RELEASE", "RENAME", "REPLACE", "RESET", "REVOKE", "ROLE",
    "ROLLBACK", "RULE", "SAVEPOINT", "SECURITY", "SEQUENCE",
    "SERVER", "SESSION", "SIGNAL", "SUBSCRIPTION", "TABLESPACE",
    "TEMP", "TEMPORARY", "TEXT", "TRANSACTION", "TRIGGER", "TRUNCATE",
    "TRUSTED", "UNLISTEN", "UNLOGGED", "VACUUM", "VALID", "VALIDATOR",
    "VALUE", "VOLATILE", "WHITESPACE", "WORK", "WRITE",
}


def _quote_pg(name: str) -> str:
    if not name:
        return name
    if name.upper() in _POSTGRES_RESERVED_WORDS:
        return f'"{name.replace(chr(34), chr(34) + chr(34))}"'
    if re.fullmatch(r"[a-z_][a-z0-9_]*", name):
        return name
    return f'"{name.replace(chr(34), chr(34) + chr(34))}"'


def _is_expression(s: str) -> bool:
    if not s:
        return False
    if s.startswith('"') and s.endswith('"'):
        return False
    for ch in s:
        if ch in ("(", ")", ":", ",", "+", "-", "*", "/", "%", "=", "<", ">", "!", "|", "&", "#", "~"):
            return True
    return False


def _render_postgres_column_type(col: Any) -> str:
    pg_type = col.pg_meta.get("pg_type", {}) if col.pg_meta else {}
    if pg_type.get("kind") == "enum" and pg_type.get("type_name"):
        return str(pg_type["type_name"])
    if pg_type.get("kind") == "array" and pg_type.get("inner"):
        dimensions = pg_type.get("dimensions", 1) or 1
        return f"{pg_type['inner']}" + "[]" * dimensions
    return col.type


def _build_create_policy_sql(policy: dict, qname: str) -> str:
    parts = [f"CREATE POLICY {_quote_pg(policy['name'])} ON {qname}"]
    permissive = policy.get("permissive", "PERMISSIVE")
    if permissive.upper() == "RESTRICTIVE":
        parts.append("AS RESTRICTIVE")
    command = policy.get("command", "ALL")
    parts.append(f"FOR {command}")
    role = policy.get("role", "PUBLIC")
    if isinstance(role, str):
        role = [role]
    roles = ", ".join(r if r.upper() == "PUBLIC" else _quote_pg(r) for r in role)
    parts.append(f"TO {roles}")
    using = policy.get("using")
    if using:
        parts.append(f"USING ({using})")
    with_check = policy.get("with_check")
    if with_check:
        parts.append(f"WITH CHECK ({with_check})")
    return " ".join(parts) + ";"


def _build_alter_policy_sql(policy: dict, qname: str) -> str:
    parts = [f"ALTER POLICY {_quote_pg(policy['name'])} ON {qname}"]
    role = policy.get("role", "PUBLIC")
    if isinstance(role, str):
        role = [role]
    roles = ", ".join(r if r.upper() == "PUBLIC" else _quote_pg(r) for r in role)
    parts.append(f"TO {roles}")
    using = policy.get("using")
    if using:
        parts.append(f"USING ({using})")
    with_check = policy.get("with_check")
    if with_check:
        parts.append(f"WITH CHECK ({with_check})")
    return " ".join(parts) + ";"


def _build_drop_policy_sql(policy_name: str, qname: str) -> str:
    return f"DROP POLICY IF EXISTS {_quote_pg(policy_name)} ON {qname};"


def _build_grant_sql(grant_entry: dict, qname: str, object_type: str = "TABLE") -> str:
    privileges = grant_entry.get("privileges", "ALL")
    if isinstance(privileges, list):
        privileges = ", ".join(privileges)
    role = grant_entry.get("role", "PUBLIC")
    if isinstance(role, str):
        role = [role]
    roles = ", ".join(r if r.upper() == "PUBLIC" else _quote_pg(r) for r in role)
    sql = f"GRANT {privileges} ON {object_type} {qname} TO {roles}"
    if grant_entry.get("grantable"):
        sql += " WITH GRANT OPTION"
    return sql + ";"


def _build_revoke_sql(grant_entry: dict, qname: str, object_type: str = "TABLE") -> str:
    privileges = grant_entry.get("privileges", "ALL")
    if isinstance(privileges, list):
        privileges = ", ".join(privileges)
    role = grant_entry.get("role", "PUBLIC")
    if isinstance(role, str):
        role = [role]
    roles = ", ".join(r if r.upper() == "PUBLIC" else _quote_pg(r) for r in role)
    sql = f"REVOKE {privileges} ON {object_type} {qname} FROM {roles}"
    if grant_entry.get("grantable"):
        sql += " CASCADE"
    return sql + ";"


def generate_create_view_sql(
    table: Any,
    qualified_name: str | None = None,
) -> str:
    qname = qualified_name or table.name
    if table.pg_view_materialized:
        sql = f"CREATE MATERIALIZED VIEW IF NOT EXISTS {qname} AS {table.pg_view_definition or ''}"
    else:
        sql = f"CREATE OR REPLACE VIEW {qname} AS {table.pg_view_definition or ''}"
    if table.comment:
        sql += f";\nCOMMENT ON VIEW {qname} IS '{table.comment.replace(chr(39), chr(39) + chr(39))}'"
    return sql
