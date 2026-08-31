from __future__ import annotations

from typing import Any, List, Optional, Tuple

from dbwarden.engine.core.protocol import ObjectHandler, Op, RunPhase
from dbwarden.engine.core.ordering import apply_public_ordering, order_handlers
from dbwarden.engine.core.statement_order import MigrationStatement, _assemble_migration
from dbwarden.engine.migration_name import Change
from dbwarden.logging import get_component_logger
from dbwarden.plugin import ObjectHandlerRegistration, ObjectPluginRegistry

logger = get_component_logger("registry")

# Overrides are announced once per (object type, plugin) per process. Drivers are
# constructed many times per run, and a warning repeated ten times reads like ten
# problems.
_WARNED_OVERRIDES: set[tuple[str, str]] = set()


def reset_override_warnings() -> None:
    """Forget which overrides have been announced. For tests."""
    _WARNED_OVERRIDES.clear()


def _warn_override(object_type: str, plugin: str) -> None:
    key = (object_type, plugin)
    if key in _WARNED_OVERRIDES:
        return
    _WARNED_OVERRIDES.add(key)
    logger.warning(
        "Plugin '%s' overrides the built-in handler for object type '%s'. "
        "Migration SQL for '%s' now comes from the plugin, not dbwarden core.",
        plugin,
        object_type,
        object_type,
    )


class RegistryDriver:
    """Orchestrates all registered ObjectHandlers to produce ops and SQL."""

    def __init__(self, *, include_plugins: bool = True) -> None:
        self._handlers: dict[str, ObjectHandler] = {}
        # Types claimed by plugins are tracked even when this driver does not run
        # plugin handlers, so a core handler for a claimed type is still stepped
        # aside for. Otherwise core and the plugin would both emit for that type.
        self._plugin_claims: dict[str, ObjectHandlerRegistration] = {
            registration.handler.object_type: registration
            for registration in ObjectPluginRegistry.handlers().values()
        }
        if include_plugins:
            self._register_plugin_handlers()

    def register(self, handler: ObjectHandler) -> None:
        claim = self._plugin_claims.get(handler.object_type)
        if claim is not None and claim.handler is not handler:
            # A core handler for a type a plugin has claimed. The plugin wins;
            # say so, because silently swapping who generates DDL for an object
            # type is not something a user should have to read source to notice.
            _warn_override(handler.object_type, claim.plugin)
            return

        apply_public_ordering(handler)
        if handler.object_type in self._handlers and self._handlers[handler.object_type] is not handler:
            raise ValueError(f"Duplicate object handler for '{handler.object_type}'")
        self._handlers[handler.object_type] = handler

    def _register_plugin_handlers(self) -> None:
        for registration in ObjectPluginRegistry.handlers().values():
            self.register(registration.handler)

    def run(
        self,
        snapshot: dict[str, Any],
        model_tables: list[Any],
        config: Any,
    ) -> Tuple[List[Op], List[Op]]:
        all_upgrade: list[Op] = []
        all_rollback: list[Op] = []

        for handler in order_handlers(self._handlers):
            snap_spec = handler.extract(snapshot)
            snap_canonical = handler.canonicalize(snap_spec)

            if handler.run_phase == RunPhase.PREAMBLE:
                model_spec = handler.model_spec_from_config(config)
            else:
                model_spec = handler.model_spec_from_tables(model_tables)

            model_canonical = handler.canonicalize(model_spec)

            up, rb = handler.diff(snap_canonical, model_canonical)
            all_upgrade.extend(up)
            all_rollback.extend(rb)

        return all_upgrade, all_rollback

    def emit_all(
        self,
        ops: List[Op],
        db_name: Optional[str] = None,
        cluster_ctx: Any = None,
    ) -> List[MigrationStatement]:
        stmts: list[MigrationStatement] = []
        ops_by_type: dict[str, list[Op]] = {}
        for op in ops:
            ops_by_type.setdefault(op.object_type, []).append(op)
        for handler in order_handlers(self._handlers):
            handler_op_types = getattr(handler, "op_types", (handler.object_type,))
            for ot in handler_op_types:
                for op in ops_by_type.get(ot, []):
                    stmts.extend(handler.emit(op, db_name=db_name, cluster_ctx=cluster_ctx))
        return stmts

    def emit_op_to_sql(
        self,
        upgrade_ops: List[Op],
        rollback_ops: List[Op],
        db_name: Optional[str] = None,
        cluster_ctx: Any = None,
    ) -> Tuple[str, str, List[Change]]:
        all_stmts = (
            self.emit_all(upgrade_ops, db_name=db_name, cluster_ctx=cluster_ctx)
            + self.emit_all(rollback_ops, db_name=db_name, cluster_ctx=cluster_ctx)
        )
        up_sql, rb_sql = _assemble_migration(all_stmts)
        changes: list[Change] = []
        return up_sql, rb_sql, changes
