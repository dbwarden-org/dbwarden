from __future__ import annotations

from dataclasses import dataclass
from typing import Tuple

from dbwarden.databases.clickhouse.cluster import ClusterContext, ClusterMode
from dbwarden.engine.core.statement_order import MigrationStatement


@dataclass
class ClusterableStatement:
    """A DDL statement that may need an ON CLUSTER clause inserted.

    ClickHouse's ON CLUSTER position is syntactically fixed and differs by
    statement kind, so handlers declare the *prefix* (everything up to and
    including the object name) and the *suffix* (everything after the ON CLUSTER
    slot) separately.  ``render()`` joins them with or without the clause.

    Example for CREATE TABLE::

        prefix = "CREATE TABLE db.events"
        suffix = "(...) ENGINE = MergeTree() ORDER BY ..."
        ->  CREATE TABLE db.events ON CLUSTER 'c' (...) ENGINE = ...

    Attributes:
        prefix: SQL up to the ON CLUSTER insertion point.
        suffix: SQL after the insertion point.
        supports_cluster: some statements (e.g. some SYSTEM ops) never take
            ON CLUSTER; set False to pass through untouched.
    """
    prefix: str
    suffix: str
    supports_cluster: bool = True

    def render(self, ctx: ClusterContext) -> str:
        """Produce the final SQL string for the given cluster context.

        In REPLICATED and NONE modes, no clause is inserted.  In ON_CLUSTER mode,
        ``ON CLUSTER '<name>'`` is inserted between prefix and suffix.
        """
        out = self.prefix
        if (
            ctx.mode is ClusterMode.ON_CLUSTER
            and self.supports_cluster
        ):
            out += f" ON CLUSTER {_quote(ctx.cluster_name)}"
        if self.suffix and not self.suffix.startswith((" ", "\t", "\n", ";")):
            out += " "
        out += self.suffix
        return out.strip()

    @classmethod
    def from_sql(cls, sql: str) -> "ClusterableStatement":
        """Parse a raw DDL string and partition it at the ON CLUSTER insertion
        point.

        Uses a heuristic regex to find the first DDL keyword + object name and
        splits the statement there.  Coverage:

        ==========================  =============================
        Statement shape             Insertion point
        ==========================  =============================
        ``CREATE TABLE name ...``   After *name*
        ``CREATE MATERIALIZED VIEW name ...``  After *name*
        ``CREATE DICTIONARY name ...``  After *name*
        ``ALTER TABLE name ...``    After *name*
        ``RENAME TABLE name ...``   After *name*
        ``DETACH TABLE name ...``   After *name*
        ``ATTACH TABLE name ...``   After *name*
        ``DROP TABLE name ...``     After *name*
        ``DROP DICTIONARY name ...``  After *name*
        ==========================  =============================

        Accounts for optional ``IF NOT EXISTS`` / ``IF EXISTS``
        qualifiers between the verb and the object name.

        If the pattern is not matched the entire string is treated as
        *prefix* (no suffix), which means ``ON CLUSTER`` is appended
        at the end. Likely wrong, but safe for non-DDL pass-through.
        """
        import re
        identifier = r"(?:`(?:``|[^`])+`|[a-zA-Z_][a-zA-Z0-9_]*)"
        qualified_identifier = rf"{identifier}(?:\.{identifier})*"
        m = re.search(
            r'\b('
            r'CREATE\s+(TABLE|MATERIALIZED\s+VIEW|DICTIONARY)'
            r'(?:\s+IF\s+NOT\s+EXISTS)?'
            r'|'
            r'ALTER\s+TABLE'
            r'|'
            r'RENAME\s+TABLE'
            r'|'
            r'(?:DETACH|ATTACH)\s+TABLE'
            r'|'
            r'DROP\s+(TABLE|DICTIONARY)(?:\s+IF\s+EXISTS)?'
            rf')\s+({qualified_identifier})',
            sql, re.IGNORECASE
        )
        if m:
            verb_end = m.end()
            return cls(prefix=sql[:verb_end], suffix=sql[verb_end:])
        return cls(prefix=sql, suffix="")

    def to_migration(
        self,
        order: "StatementOrder",
        ctx: ClusterContext,
        rollback: "ClusterableStatement | str | None" = None,
    ) -> MigrationStatement:
        """Render both forward and (optional) rollback into a MigrationStatement."""
        from dbwarden.engine.core.statement_order import StatementOrder

        forward_sql = self.render(ctx)
        if isinstance(rollback, ClusterableStatement):
            rollback_sql = rollback.render(ctx)
        elif isinstance(rollback, str):
            rollback_sql = rollback
        else:
            rollback_sql = f"-- no-op (reverse of {forward_sql!r})"
        return MigrationStatement(order=order, upgrade_sql=forward_sql, rollback_sql=rollback_sql)


def emit_with_cluster(method):
    """Decorator for handler emit() that injects cluster_ctx.

    Extracts ``cluster_ctx`` from ``**kwargs`` (or falls back to NONE mode),
    stores it as ``handler._cluster_ctx``, then calls the wrapped emit.
    Handlers can reference ``self._cluster_ctx`` directly instead of
    extracting from kwargs.
    """
    from functools import wraps

    @wraps(method)
    def wrapper(self, op, db_name=None, **kwargs):
        from dbwarden.databases.clickhouse.cluster import ClusterContext
        cluster_ctx = kwargs.pop("cluster_ctx", None)
        if cluster_ctx is None:
            cluster_ctx = ClusterContext.from_config(type("_", (), {})())
        self._cluster_ctx = cluster_ctx
        return method(self, op, db_name, **kwargs)
    return wrapper


def _quote(ident: str) -> str:
    """Quote a cluster name as a ClickHouse string literal."""
    escaped = ident.replace("'", "\\'")
    return f"'{escaped}'"
