"""Plugin-related exceptions."""
from __future__ import annotations

from .core import DBWardenError


class PluginApiMismatchError(DBWardenError):
    """Raised when a plugin targets a plugin API version this dbwarden does not provide."""

    def __init__(self, dist_name: str, declared: object) -> None:
        from dbwarden.plugin import PLUGIN_API_VERSION
        super().__init__(
            f"Plugin '{dist_name}' targets dbwarden plugin API version {declared}, "
            f"but this dbwarden provides version {PLUGIN_API_VERSION}. "
            f"Upgrade the plugin, or pin dbwarden to a version that provides "
            f"API {declared}."
        )
        self.dist_name = dist_name
        self.declared = declared


class HookNotRegisteredError(DBWardenError):
    """Raised when a hook is called but no plugin has registered it."""

    def __init__(self, hook_name: str) -> None:
        super().__init__(f"No plugin registered hook '{hook_name}'")
        self.hook_name = hook_name


class HookConflictError(DBWardenError):
    """Raised when multiple plugins register the same single-value hook."""

    def __init__(self, hook_name: str, plugins: list[str]) -> None:
        super().__init__(
            f"Hook '{hook_name}' registered by {len(plugins)} plugins "
            f"({', '.join(plugins)}), expected exactly 1"
        )
        self.hook_name = hook_name
        self.plugins = plugins


class ObjectHandlerConflictError(DBWardenError):
    """Raised when multiple plugins claim the same object_type."""

    def __init__(self, object_type: str, plugins: list[str]) -> None:
        super().__init__(
            f"Object handler '{object_type}' registered by multiple plugins: "
            f"{', '.join(plugins)}"
        )
        self.object_type = object_type
        self.plugins = plugins


class PluginInstallError(DBWardenError):
    """Raised when a plugin installation fails."""

    pass
