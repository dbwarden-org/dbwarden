from __future__ import annotations

import base64
import json
import subprocess
import sys
import tomllib
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from importlib.metadata import EntryPoint, entry_points
from importlib.metadata import PackageNotFoundError, version as package_version
from pathlib import Path
from typing import Any, Callable

from packaging.version import parse as parse_version
from packaging.requirements import InvalidRequirement, Requirement

from dbwarden._verified import VERIFIED_PLUGINS
from dbwarden._official import OFFICIAL_PLUGINS, OfficialSpec, classify
from dbwarden.files import atomic_write_text

from dbwarden.logging import get_component_logger

logger = get_component_logger("plugin")

PYPI_BASE_URL = "https://pypi.org"


def _toml_string(value: object) -> str:
    """Render a TOML basic string without allowing structural injection."""
    text = str(value)
    escaped = (
        text.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\r", "\\r")
        .replace("\n", "\\n")
        .replace("\t", "\\t")
    )
    return f'"{escaped}"'
# The OIDC issuer PyPI records for GitHub Actions Trusted Publishing.
GITHUB_OIDC_ISSUER = "https://token.actions.githubusercontent.com"

PLUGIN_GROUP = "dbwarden.plugins"
CONSENT_PATH = Path(".dbwarden") / "consent.toml"
LOCK_PATH = Path(".dbwarden") / "plugins.lock"

# The plugin contract's own version, independent of dbwarden's release version.
# Bump it when a change would make an existing plugin behave incorrectly rather
# than fail loudly: a hook signature change, different semantics for a registered
# handler, a rename on the stable surface.
#
# A plugin declares the contract it was built against by setting
# `DBWARDEN_PLUGIN_API` on its package. Declaring nothing means "version 1", so
# plugins written before this existed keep working.
PLUGIN_API_VERSION = 1
PLUGIN_API_ATTR = "DBWARDEN_PLUGIN_API"

from dbwarden.exceptions.plugin import (  # noqa: E402
    HookConflictError,
    HookNotRegisteredError,
    ObjectHandlerConflictError,
    PluginApiMismatchError,
    PluginInstallError,
)


KNOWN_VALUE_HOOKS: frozenset[str] = frozenset({
    "session_factory",
    "sync_session_factory",
    "clickhouse_session_factory",
    "clickhouse_sync_session_factory",
    "load_model_module",
    "load_config_module",
    "lifespan",
    "health_routes",
    "migration_routes",
    "seed_apply",
    "seed_create",
    "seed_export",
    "seed_list",
    "seed_rollback",
    "sandbox_provider_start",
    "sandbox_provider_stop",
})

MULTI_VALUE_HOOKS: frozenset[str] = frozenset({
    "health_routes",
    "migration_routes",
})

# Representative (args, kwargs) core uses to invoke each value hook. A registered
# hook is signature-compliant if `inspect.signature(fn).bind(*args, **kwargs)`
# succeeds. `_ANY` is a placeholder where only arity matters, not the value.
_ANY: object = object()

HOOK_CALL_SPECS: dict[str, tuple[tuple[Any, ...], dict[str, Any]]] = {
    "session_factory": (("primary",), {"dev": False}),
    "sync_session_factory": (("primary",), {"dev": False}),
    "clickhouse_session_factory": (("primary",), {"dev": False}),
    "clickhouse_sync_session_factory": (("primary",), {"dev": False}),
    "load_model_module": ((_ANY, _ANY), {}),
    "load_config_module": ((_ANY, _ANY), {}),
    "lifespan": ((), {"mode": "check"}),
    "health_routes": ((), {"auth_mode": "open", "api_key": None}),
    "migration_routes": ((), {"auth_mode": "open", "api_key": None}),
    "seed_create": (("seed",), {"seed_type": "sql", "database": None, "verbose": False}),
    "seed_apply": ((), {"version": None, "dry_run": False, "database": None, "all_databases": False, "verbose": False}),
    "seed_list": ((), {"database": None, "all_databases": False, "verbose": False, "prune": False}),
    "seed_rollback": ((), {"count": None, "to_version": None, "database": None, "all_databases": False, "verbose": False}),
    "seed_export": ((), {"database": None, "all_databases": False, "output_dir": "seeds"}),
    "sandbox_provider_start": (("sqlite",), {}),
    "sandbox_provider_stop": ((), {}),
}


@dataclass(frozen=True)
class ProvenanceResult:
    verified: bool
    version: str | None = None
    identity: str | None = None
    filename: str | None = None
    sha256: str | None = None
    reason: str | None = None


@dataclass(frozen=True)
class PluginLockEntry:
    distribution: str
    version: str
    filename: str
    sha256: str
    tier: str
    verified: str
    identity: str
    installed_at: str


@dataclass(frozen=True)
class PluginLoadReport:
    distribution: str
    version: str | None
    entry_point: str
    value: str
    tier: str
    trusted: bool
    state: str
    hooks: tuple[str, ...] = ()
    object_handlers: tuple[str, ...] = ()
    error: str | None = None
    lock: PluginLockEntry | None = None


@dataclass(frozen=True)
class ObjectHandlerRegistration:
    plugin: str
    handler: Any


# Config keys owned by official plugins, mapped to the distribution that provides
# them. Core keeps this table (rather than only the live registry) so that a key
# declared without its plugin installed fails with an actionable message instead
# of being silently ignored, which is what used to happen.
PLUGIN_CONFIG_KEY_OWNERS: dict[str, str] = {
    "pg_roles": "dbwarden-pgsql-rbac",
    "pg_default_privileges": "dbwarden-pgsql-rbac",
    "pg_domains": "dbwarden-pgsql-types",
    "pg_sequences": "dbwarden-pgsql-types",
    "pg_composite_types": "dbwarden-pgsql-types",
    "pg_extensions": "dbwarden-pgsql-extensions",
    "pg_functions": "dbwarden-pgsql-extensions",
    "pg_triggers": "dbwarden-pgsql-extensions",
    "pg_event_triggers": "dbwarden-pgsql-extensions",
    "pg_extended_statistics": "dbwarden-pgsql-extensions",
    "ch_named_collections": "dbwarden-ch-rbac",
    "ch_roles": "dbwarden-ch-rbac",
    "ch_users": "dbwarden-ch-rbac",
    "ch_row_policies": "dbwarden-ch-rbac",
    "ch_quotas": "dbwarden-ch-rbac",
    "ch_settings_profiles": "dbwarden-ch-rbac",
    "ch_grants": "dbwarden-ch-rbac",
}


class ConfigKeyRegistry:
    """Config keys contributed by plugins.

    A plugin registers the ``database_config(...)`` keyword arguments it consumes.
    Core validates against this registry, so an unknown key raises instead of
    being silently dropped, and a known key whose plugin is missing raises with
    an install hint.
    """

    _keys: dict[str, str] = {}

    @classmethod
    def register(cls, key: str, *, plugin: str) -> None:
        existing = cls._keys.get(key)
        if existing is not None and existing != plugin:
            raise ValueError(
                f"Config key '{key}' registered by multiple plugins: {existing!r} and {plugin!r}"
            )
        cls._keys[key] = plugin

    @classmethod
    def is_registered(cls, key: str) -> bool:
        return key in cls._keys

    @classmethod
    def owner(cls, key: str) -> str | None:
        return cls._keys.get(key)

    @classmethod
    def keys(cls) -> tuple[str, ...]:
        return tuple(sorted(cls._keys))

    @classmethod
    def reset(cls) -> None:
        cls._keys = {}


class HookRegistry:
    _hooks: dict[str, list[tuple[str, Callable[..., Any]]]] = {}

    @classmethod
    def register(cls, hook_name: str, fn: Callable[..., Any], *, plugin: str) -> None:
        if hook_name not in KNOWN_VALUE_HOOKS:
            raise ValueError(
                f"Unknown hook '{hook_name}'. Known: {', '.join(sorted(KNOWN_VALUE_HOOKS))}"
            )
        cls._hooks.setdefault(hook_name, []).append((plugin, fn))

    @classmethod
    def execute_single(cls, hook_name: str, *args: Any, **kwargs: Any) -> Any:
        fns = cls._hooks.get(hook_name, [])
        if not fns:
            raise HookNotRegisteredError(hook_name)
        if len(fns) > 1:
            raise HookConflictError(hook_name, [plugin for plugin, _ in fns])
        return fns[0][1](*args, **kwargs)

    @classmethod
    def execute_all(cls, hook_name: str, *args: Any, **kwargs: Any) -> list[Any]:
        return [fn(*args, **kwargs) for _, fn in cls._hooks.get(hook_name, [])]

    @classmethod
    def is_registered(cls, hook_name: str) -> bool:
        return bool(cls._hooks.get(hook_name))

    @classmethod
    def providers(cls, hook_name: str) -> list[str]:
        return [plugin for plugin, _ in cls._hooks.get(hook_name, [])]

    @classmethod
    def hooks(cls) -> dict[str, list[tuple[str, Callable[..., Any]]]]:
        return {name: list(entries) for name, entries in cls._hooks.items()}

    @classmethod
    def clear(cls) -> None:
        cls._hooks.clear()


class ObjectPluginRegistry:
    _handlers: dict[str, ObjectHandlerRegistration] = {}

    @classmethod
    def register(cls, handler: Any, *, plugin: str) -> None:
        from dbwarden.engine.core.ordering import apply_public_ordering

        object_type = getattr(handler, "object_type", None)
        if not isinstance(object_type, str) or not object_type:
            raise ValueError("Object handlers must define a non-empty object_type")
        apply_public_ordering(handler)
        existing = cls._handlers.get(object_type)
        if existing is not None and existing.plugin != plugin:
            raise ObjectHandlerConflictError(object_type, [existing.plugin, plugin])
        cls._handlers[object_type] = ObjectHandlerRegistration(plugin=plugin, handler=handler)

    @classmethod
    def handlers(cls) -> dict[str, ObjectHandlerRegistration]:
        return dict(cls._handlers)

    @classmethod
    def has_handler(cls, object_type: str) -> bool:
        return object_type in cls._handlers

    @classmethod
    def clear(cls) -> None:
        cls._handlers.clear()
        # Whatever was overriding core is gone too, so let the next override be
        # announced again rather than suppressed by a stale dedup entry.
        from dbwarden.engine.core.registry import reset_override_warnings

        reset_override_warnings()


class PluginRegistrar:
    """Entry point for plugin registration.

    Passed to a plugin's ``setup(registrar)`` function. The plugin calls
    ``register()`` for value hooks and ``register_object_handler()`` for
    schema diff handlers.

    Attributes:
        _plugin_name: The distribution name of the registering plugin.
    """

    def __init__(self, plugin_name: str) -> None:
        self._plugin_name = plugin_name

    def register(self, hook_name: str, fn: Callable[..., Any]) -> None:
        HookRegistry.register(hook_name, fn, plugin=self._plugin_name)

    def register_object_handler(self, handler: Any) -> None:
        ObjectPluginRegistry.register(handler, plugin=self._plugin_name)

    def register_config_key(self, *keys: str) -> None:
        """Declare ``database_config(...)`` keyword arguments this plugin consumes."""
        for key in keys:
            ConfigKeyRegistry.register(key, plugin=self._plugin_name)


def _dist_name(ep: EntryPoint) -> str:
    dist = getattr(ep, "dist", None)
    return dist.name if dist is not None else "<unknown>"


def _dist_version(ep: EntryPoint) -> str | None:
    dist = getattr(ep, "dist", None)
    return dist.version if dist is not None else None


_LOAD_STATES: dict[str, str] = {}
_LOAD_ERRORS: dict[str, str] = {}
_DISCOVERED: dict[str, tuple[str | None, str, str]] = {}


def load_consent(path: Path | None = None) -> dict[str, dict[str, str]]:
    path = path or CONSENT_PATH
    if not path.exists():
        return {}
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    consent = data.get("consent", {})
    return {str(name): dict(value) for name, value in consent.items() if isinstance(value, dict)}


def consent_allows(dist_name: str, version: str | None, *, path: Path | None = None) -> bool:
    consent = load_consent(path)
    entry = consent.get(dist_name)
    if not entry:
        return False
    return version is None or entry.get("version") == version


def verified_allows(dist_name: str, version: str | None = None) -> bool:
    min_version = VERIFIED_PLUGINS.get(dist_name)
    if min_version is None:
        return False
    try:
        installed = version or package_version(dist_name)
        return parse_version(installed) >= parse_version(min_version)
    except Exception:
        return False


def record_consent(dist_name: str, version: str | None, *, path: Path | None = None) -> None:
    path = path or CONSENT_PATH
    consent = load_consent(path)
    consent[dist_name] = {
        "version": version or "",
        "consented_at": datetime.now(timezone.utc).isoformat(),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = []
    for name in sorted(consent):
        entry = consent[name]
        lines.append(f'[consent.{_toml_string(name)}]')
        lines.append(f"version = {_toml_string(entry.get('version', ''))}")
        lines.append(f"consented_at = {_toml_string(entry.get('consented_at', ''))}")
        lines.append("")
    atomic_write_text(path, "\n".join(lines))


def revoke_consent(dist_name: str, *, path: Path | None = None) -> bool:
    path = path or CONSENT_PATH
    consent = load_consent(path)
    if dist_name not in consent:
        return False
    del consent[dist_name]
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = []
    for name in sorted(consent):
        entry = consent[name]
        lines.append(f'[consent.{_toml_string(name)}]')
        lines.append(f"version = {_toml_string(entry.get('version', ''))}")
        lines.append(f"consented_at = {_toml_string(entry.get('consented_at', ''))}")
        lines.append("")
    atomic_write_text(path, "\n".join(lines))
    return True


def load_plugin_lock(path: Path | None = None) -> dict[str, PluginLockEntry]:
    path = path or LOCK_PATH
    if not path.exists():
        return {}
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    lock: dict[str, PluginLockEntry] = {}
    for name, value in data.items():
        if not isinstance(value, dict):
            continue
        lock[str(name)] = PluginLockEntry(
            distribution=str(value.get("distribution", name)),
            version=str(value.get("version", "")),
            filename=str(value.get("filename", "")),
            sha256=str(value.get("sha256", "")),
            tier=str(value.get("tier", "")),
            verified=str(value.get("verified", "")),
            identity=str(value.get("identity", "")),
            installed_at=str(value.get("installed_at", "")),
        )
    return lock


def plugin_reports() -> list[PluginLoadReport]:
    reports: list[PluginLoadReport] = []
    lock_by_dist = {entry.distribution: entry for entry in load_plugin_lock().values()}
    discovered = dict(_DISCOVERED)
    for ep in iter_plugin_entry_points():
        dist_name = _dist_name(ep)
        discovered[dist_name] = (_dist_version(ep), ep.name, ep.value)

    for dist_name in sorted(discovered):
        version, ep_name, ep_value = discovered[dist_name]
        tier = classify(dist_name)
        trusted = tier == "official" or (
            tier == "verified" and verified_allows(dist_name, version)
        ) or consent_allows(dist_name, version)
        reports.append(
            PluginLoadReport(
                distribution=dist_name,
                version=version,
                entry_point=ep_name,
                value=ep_value,
                tier=tier,
                trusted=trusted,
                state=_LOAD_STATES.get(dist_name, "discovered"),
                hooks=tuple(_hooks_for_plugin(dist_name)),
                object_handlers=tuple(_object_handlers_for_plugin(dist_name)),
                error=_LOAD_ERRORS.get(dist_name),
                lock=lock_by_dist.get(dist_name),
            )
        )
    return reports


def _hooks_for_plugin(dist_name: str) -> list[str]:
    hooks: list[str] = []
    for hook_name, entries in HookRegistry.hooks().items():
        if any(plugin == dist_name for plugin, _ in entries):
            hooks.append(hook_name)
    return sorted(hooks)


def _object_handlers_for_plugin(dist_name: str) -> list[str]:
    return sorted(
        object_type
        for object_type, registration in ObjectPluginRegistry.handlers().items()
        if registration.plugin == dist_name
    )


def write_plugin_lock_entry(
    key: str,
    entry: PluginLockEntry,
    *,
    path: Path | None = None,
) -> None:
    path = path or LOCK_PATH
    lock = load_plugin_lock(path)
    lock[key] = entry
    _write_plugin_lock(lock, path)


def remove_plugin_lock_entry(key: str, *, path: Path | None = None) -> bool:
    path = path or LOCK_PATH
    lock = load_plugin_lock(path)
    if key not in lock:
        return False
    del lock[key]
    _write_plugin_lock(lock, path)
    return True


def _write_plugin_lock(lock: dict[str, PluginLockEntry], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = []
    for key in sorted(lock):
        entry = lock[key]
        if not key or any(char not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-" for char in key):
            raise ValueError(f"Invalid plugin lock key: {key!r}")
        lines.append(f"[{key}]")
        lines.append(f"distribution = {_toml_string(entry.distribution)}")
        lines.append(f"version = {_toml_string(entry.version)}")
        lines.append(f"filename = {_toml_string(entry.filename)}")
        lines.append(f"sha256 = {_toml_string(entry.sha256)}")
        lines.append(f"tier = {_toml_string(entry.tier)}")
        lines.append(f"verified = {_toml_string(entry.verified)}")
        lines.append(f"identity = {_toml_string(entry.identity)}")
        lines.append(f"installed_at = {_toml_string(entry.installed_at)}")
        lines.append("")
    atomic_write_text(path, "\n".join(lines))


def _fetch_url(url: str, *, timeout: float = 30.0, max_bytes: int = 10 * 1024 * 1024) -> bytes:
    from urllib.parse import urlparse

    parsed = urlparse(url)
    if parsed.scheme != "https":
        raise ValueError("Plugin provenance URLs must use HTTPS")
    if timeout <= 0 or max_bytes <= 0:
        raise ValueError("timeout and max_bytes must be positive")
    request = urllib.request.Request(url, headers={"User-Agent": "dbwarden-plugin-provenance"})
    with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 - https only
        content_length = response.headers.get("Content-Length")
        if content_length is not None and int(content_length) > max_bytes:
            raise ValueError("Plugin provenance response exceeds the maximum allowed size")
        data = response.read(max_bytes + 1)
        if len(data) > max_bytes:
            raise ValueError("Plugin provenance response exceeds the maximum allowed size")
        return data


def _fetch_json(url: str) -> Any:
    return json.loads(_fetch_url(url).decode("utf-8"))


def _pypi_project_json(project: str) -> dict[str, Any]:
    return _fetch_json(f"{PYPI_BASE_URL}/pypi/{project}/json")


def _pypi_provenance(project: str, version: str, filename: str) -> dict[str, Any] | None:
    url = f"{PYPI_BASE_URL}/integrity/{project}/{version}/{filename}/provenance"
    try:
        return _fetch_json(url)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return None
        raise


def _resolve_target_version(
    spec: OfficialSpec, project: dict[str, Any], override: str | None
) -> tuple[str | None, str | None]:
    releases = project.get("releases", {})
    pinned = override or spec.pinned_version
    if pinned is not None:
        if pinned not in releases or not releases[pinned]:
            return None, f"version {pinned} is not published on PyPI"
        return pinned, None

    floor = parse_version(spec.min_version) if spec.min_version else None
    best: tuple[Any, str] | None = None
    for raw_version, files in releases.items():
        if not files or all(f.get("yanked") for f in files):
            continue
        try:
            parsed = parse_version(raw_version)
        except Exception:
            continue
        if parsed.is_prerelease or parsed.is_devrelease:
            continue
        if floor is not None and parsed < floor:
            continue
        if best is None or parsed > best[0]:
            best = (parsed, raw_version)
    if best is None:
        return None, "no eligible (non-prerelease, non-yanked) release found on PyPI"
    return best[1], None


def _select_distribution_file(project: dict[str, Any], version: str) -> dict[str, Any] | None:
    files = [f for f in project.get("releases", {}).get(version, []) if not f.get("yanked")]
    wheels = [f for f in files if f.get("packagetype") == "bdist_wheel"]
    for candidate in (*wheels, *files):
        if candidate.get("filename") and candidate.get("digests", {}).get("sha256"):
            return candidate
    return None


def _workflow_basename(workflow: str | None) -> str | None:
    if not workflow:
        return None
    return workflow.rsplit("/", 1)[-1]


def _attestation_covers_digest(attestation: dict[str, Any], sha256: str) -> bool:
    envelope = attestation.get("envelope") or {}
    statement_raw = envelope.get("statement")
    if not isinstance(statement_raw, str):
        return False
    if len(statement_raw) > 10 * 1024 * 1024:
        return False
    try:
        statement = json.loads(base64.b64decode(statement_raw, validate=True))
    except Exception:
        return False
    for subject in statement.get("subject", []) or []:
        if (subject.get("digest") or {}).get("sha256") == sha256:
            return True
    return False


def _provenance_matches(
    provenance: dict[str, Any], spec: OfficialSpec, sha256: str
) -> tuple[bool, str]:
    bundles = provenance.get("attestation_bundles", [])
    if not bundles:
        return False, "provenance contains no attestation bundles"
    expected_workflow = _workflow_basename(spec.workflow)
    saw_publisher = False
    for bundle in bundles:
        publisher = bundle.get("publisher") or {}
        if publisher.get("kind") != "GitHub":
            continue
        if (publisher.get("repository") or "").lower() != spec.repo_slug.lower():
            continue
        if expected_workflow and _workflow_basename(publisher.get("workflow")) != expected_workflow:
            continue
        saw_publisher = True
        for attestation in bundle.get("attestations", []):
            if _attestation_covers_digest(attestation, sha256):
                return True, ""
    if saw_publisher:
        return False, "attestation from the expected publisher does not cover the distribution digest"
    return False, (
        f"no attestation from expected publisher {spec.repo_slug}"
        + (f" (workflow {expected_workflow})" if expected_workflow else "")
    )


def verify_official_provenance(
    dist_name: str, spec: OfficialSpec, *, version: str | None = None
) -> ProvenanceResult:
    """Verify an official plugin's PyPI attestation (PEP 740) before install.

    Confirms PyPI published a Trusted-Publishing attestation whose recorded
    publisher is the expected GitHub repository/workflow, and that the
    attestation covers the exact distribution file we would install. Trust is
    anchored in PyPI's server-side attestation verification and TLS, the same
    root pip relies on. Any missing attestation, publisher mismatch, digest
    mismatch, or network error fails closed.
    """
    try:
        project = _pypi_project_json(spec.pypi)
    except Exception as exc:  # noqa: BLE001 - any failure must fail closed
        return ProvenanceResult(
            verified=False,
            identity=spec.repository,
            reason=f"could not query PyPI for '{spec.pypi}': {exc}",
        )

    target_version, reason = _resolve_target_version(spec, project, version)
    if target_version is None:
        return ProvenanceResult(verified=False, identity=spec.repository, reason=reason)

    file_info = _select_distribution_file(project, target_version)
    if file_info is None:
        return ProvenanceResult(
            verified=False,
            version=target_version,
            identity=spec.repository,
            reason=f"no usable distribution file for {spec.pypi} {target_version}",
        )
    filename = file_info["filename"]
    sha256 = file_info["digests"]["sha256"]

    try:
        provenance = _pypi_provenance(spec.pypi, target_version, filename)
    except Exception as exc:  # noqa: BLE001 - fail closed on any error
        return ProvenanceResult(
            verified=False,
            version=target_version,
            filename=filename,
            sha256=sha256,
            identity=spec.repository,
            reason=f"could not fetch provenance for {filename}: {exc}",
        )
    if provenance is None:
        return ProvenanceResult(
            verified=False,
            version=target_version,
            filename=filename,
            sha256=sha256,
            identity=spec.repository,
            reason="no PyPI attestation (PEP 740 provenance) published for this release",
        )

    matched, why = _provenance_matches(provenance, spec, sha256)
    if not matched:
        return ProvenanceResult(
            verified=False,
            version=target_version,
            filename=filename,
            sha256=sha256,
            identity=spec.repository,
            reason=why,
        )

    return ProvenanceResult(
        verified=True,
        version=target_version,
        identity=spec.repository,
        filename=filename,
        sha256=sha256,
    )


def installer_command(
    dist_name: str,
    *,
    use_uv: bool = False,
    version: str | None = None,
    action: str = "install",
) -> list[str]:
    """Build the package-manager command line for installing/uninstalling a plugin."""
    try:
        requirement_object = Requirement(dist_name)
    except InvalidRequirement as exc:
        raise ValueError(f"Invalid plugin distribution name: {dist_name!r}") from exc
    if requirement_object.name != dist_name or dist_name.startswith("-"):
        raise ValueError(f"Invalid plugin distribution name: {dist_name!r}")
    if version is not None:
        try:
            Requirement(f"{dist_name}=={version}")
        except InvalidRequirement as exc:
            raise ValueError(f"Invalid plugin version: {version!r}") from exc
    requirement = f"{dist_name}=={version}" if version else dist_name
    if use_uv:
        if action == "install":
            return ["uv", "add", requirement]
        return ["uv", "remove", dist_name]
    if action == "install":
        return [sys.executable, "-m", "pip", "install", requirement]
    return [sys.executable, "-m", "pip", "uninstall", "-y", dist_name]


def install_distribution(
    dist_name: str, *, use_uv: bool = False, version: str | None = None
) -> None:
    command = installer_command(dist_name, use_uv=use_uv, version=version, action="install")
    result = subprocess.run(command, check=False)
    if result.returncode != 0:
        raise PluginInstallError(f"Failed to install plugin distribution '{dist_name}'")


def uninstall_distribution(dist_name: str, *, use_uv: bool = False) -> None:
    command = installer_command(dist_name, use_uv=use_uv, action="uninstall")
    result = subprocess.run(command, check=False)
    if result.returncode != 0:
        raise PluginInstallError(f"Failed to uninstall plugin distribution '{dist_name}'")


def installed_distribution_version(dist_name: str) -> str:
    try:
        return package_version(dist_name)
    except PackageNotFoundError:
        return "unknown"


def add_plugin(
    dist_name: str, *, use_uv: bool = False, version: str | None = None
) -> PluginLockEntry | None:
    spec = OFFICIAL_PLUGINS.get(dist_name)
    if spec is None:
        install_distribution(dist_name, use_uv=use_uv, version=version)
        return None

    provenance = verify_official_provenance(dist_name, spec, version=version)
    if not provenance.verified:
        reason = provenance.reason or "provenance could not be verified"
        raise PluginInstallError(f"Refusing to install official plugin '{dist_name}': {reason}")

    # Install the exact version whose provenance we verified.
    install_distribution(dist_name, use_uv=use_uv, version=provenance.version)
    installed_version = installed_distribution_version(dist_name)
    if installed_version == "unknown" and provenance.version:
        installed_version = provenance.version
    entry = PluginLockEntry(
        distribution=dist_name,
        version=installed_version,
        filename=provenance.filename or "",
        sha256=provenance.sha256 or spec.sha256 or "",
        tier="official",
        verified="provenance",
        identity=provenance.identity or spec.repository,
        installed_at=datetime.now(timezone.utc).isoformat(),
    )
    write_plugin_lock_entry(_lock_key(dist_name), entry)
    return entry


def remove_plugin(dist_name: str, *, use_uv: bool = False) -> None:
    uninstall_distribution(dist_name, use_uv=use_uv)
    revoke_consent(dist_name)
    remove_plugin_lock_entry(_lock_key(dist_name))


def _lock_key(dist_name: str) -> str:
    prefix = "dbwarden-"
    return dist_name.removeprefix(prefix).replace("-", "_")


def prompt_community_consent(ep: EntryPoint, dist_name: str) -> bool:
    try:
        import typer
    except Exception:
        return False
    version = _dist_version(ep) or "unknown"
    return bool(typer.confirm(
        f"Enable community plugin '{dist_name}' version {version}?",
        default=False,
    ))


def declared_api_version(setup: Callable[..., Any]) -> Any:
    """The plugin API version a plugin targets, or None if it declares nothing.

    Read off the module the entry point resolved to, and off its top-level
    package, so declaring `DBWARDEN_PLUGIN_API` on the package works even when
    `setup` lives in a submodule. Nothing extra is imported: resolving the entry
    point already imported it.
    """
    module_name = getattr(setup, "__module__", "") or ""
    candidates = [module_name]
    root = module_name.partition(".")[0]
    if root and root != module_name:
        candidates.append(root)

    for name in candidates:
        module = sys.modules.get(name)
        if module is None:
            continue
        declared = getattr(module, PLUGIN_API_ATTR, None)
        if declared is not None:
            return declared
    return None


def check_api_compatibility(setup: Callable[..., Any], dist_name: str) -> None:
    """Refuse a plugin built against a different plugin API version.

    Refusing rather than warning: a plugin targeting another contract can still
    import and register cleanly and then produce wrong migration SQL, which is a
    worse failure than not loading at all.

    Declaring nothing means "version 1", so plugins written before the contract
    was versioned keep loading.
    """
    declared = declared_api_version(setup)
    if declared is None:
        return
    if declared != PLUGIN_API_VERSION:
        raise PluginApiMismatchError(dist_name, declared)


def _load_one(ep: EntryPoint, dist_name: str) -> None:
    setup = ep.load()
    check_api_compatibility(setup, dist_name)
    registrar = PluginRegistrar(plugin_name=dist_name)
    setup(registrar)


def iter_plugin_entry_points() -> list[EntryPoint]:
    return list(entry_points(group=PLUGIN_GROUP))


def load_plugins(*, interactive: bool = False) -> None:
    for ep in iter_plugin_entry_points():
        dist_name = _dist_name(ep)
        version = _dist_version(ep)
        _DISCOVERED[dist_name] = (version, ep.name, ep.value)
        tier = classify(dist_name)
        if tier == "official":
            _load_plugin_entry_point(ep, dist_name)
            continue
        if tier == "verified":
            if verified_allows(dist_name, version):
                _load_plugin_entry_point(ep, dist_name)
                continue
            logger.warning(
                "Plugin '%s' is verified, but installed version %s is below verified minimum %s; treating as community.",
                dist_name,
                version or "unknown",
                VERIFIED_PLUGINS.get(dist_name, "unknown"),
            )
        if consent_allows(dist_name, version):
            _load_plugin_entry_point(ep, dist_name)
            continue
        if interactive and prompt_community_consent(ep, dist_name):
            record_consent(dist_name, version)
            _load_plugin_entry_point(ep, dist_name)
            continue
        logger.warning(
            "Skipping community plugin '%s' (not consented). Run `dbwarden plugin trust %s` to enable it.",
            dist_name,
            dist_name,
        )
        _LOAD_STATES[dist_name] = "skipped"


def _load_plugin_entry_point(ep: EntryPoint, dist_name: str) -> None:
    try:
        _load_one(ep, dist_name)
        _LOAD_STATES[dist_name] = "loaded"
        _LOAD_ERRORS.pop(dist_name, None)
    except PluginApiMismatchError as exc:
        # Distinct from "failed": nothing is broken, the pairing is wrong. The
        # fix is a version change, not a bug report.
        _LOAD_STATES[dist_name] = "incompatible"
        _LOAD_ERRORS[dist_name] = str(exc)
        logger.error("%s", exc)
    except Exception as exc:
        _LOAD_STATES[dist_name] = "failed"
        _LOAD_ERRORS[dist_name] = str(exc)
        logger.warning("Failed to load plugin '%s': %s", dist_name, exc)


# The surface plugins are promised, and nothing else. This module also holds the
# install and provenance machinery the CLI drives (`add_plugin`,
# `verify_official_provenance`, the consent and lockfile helpers); those stay
# importable but carry no stability promise, because promising them would mean
# freezing how dbwarden installs things.
#
# tests/test_plugin_api_surface.py asserts this list exactly. Adding a name here
# is a promise; removing one is a breaking change.
__all__ = [
    "HOOK_CALL_SPECS",
    "ConfigKeyRegistry",
    "PLUGIN_CONFIG_KEY_OWNERS",
    "HookConflictError",
    "HookNotRegisteredError",
    "HookRegistry",
    "KNOWN_VALUE_HOOKS",
    "MULTI_VALUE_HOOKS",
    "ObjectHandlerConflictError",
    "ObjectHandlerRegistration",
    "ObjectPluginRegistry",
    "PLUGIN_API_ATTR",
    "PLUGIN_API_VERSION",
    "PLUGIN_GROUP",
    "PluginApiMismatchError",
    "PluginRegistrar",
]
