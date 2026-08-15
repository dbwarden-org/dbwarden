from __future__ import annotations

import json
from importlib.metadata import PackageNotFoundError, version

from rich.table import Table

from dbwarden._verified import VERIFIED_PLUGINS
from dbwarden._official import OFFICIAL_PLUGINS, classify
from dbwarden.output import (
    _format_value,
    empty_state,
    error_panel,
    info,
    info_panel,
    kv_table,
    plain,
    render,
    success_panel,
)
from dbwarden.plugin import (
    PluginInstallError,
    add_plugin,
    consent_allows,
    installer_command,
    iter_plugin_entry_points,
    plugin_reports,
    record_consent,
    remove_plugin,
    revoke_consent,
)


def _report_to_dict(report) -> dict:
    return {
        "distribution": report.distribution,
        "version": report.version,
        "entry_point": report.entry_point,
        "value": report.value,
        "tier": report.tier,
        "trusted": report.trusted,
        "state": report.state,
        "hooks": list(report.hooks),
        "object_handlers": list(report.object_handlers),
        "error": report.error,
        "lock": (
            {
                "verified": report.lock.verified,
                "identity": report.lock.identity,
                "sha256": report.lock.sha256,
                "filename": report.lock.filename,
                "installed_at": report.lock.installed_at,
            }
            if report.lock
            else None
        ),
    }


def _entry_point_dist(ep) -> tuple[str, str | None]:
    dist = getattr(ep, "dist", None)
    if dist is None:
        return "<unknown>", None
    return dist.name, dist.version


def plugin_list_cmd(output_format: str = "table") -> None:
    reports = plugin_reports()
    if output_format == "json":
        plain(json.dumps([_report_to_dict(report) for report in reports], indent=2))
        return
    if not reports:
        empty_state("No dbwarden plugins discovered.")
        return

    table = Table(title="dbwarden Plugins")
    min_dist_width = max(
        len("Distribution"),
        max(len(report.distribution) for report in reports),
    ) + 2
    table.add_column("Distribution", no_wrap=True, min_width=min_dist_width)
    for column in ("Version", "Tier", "Trusted", "State", "Hooks", "Objects", "Lock"):
        table.add_column(column, no_wrap=True)
    for report in reports:
        table.add_row(
            report.distribution,
            report.version or "unknown",
            report.tier,
            _format_value(report.trusted),
            report.state,
            _format_value(report.hooks),
            _format_value(report.object_handlers),
            report.lock.verified if report.lock else "-",
        )
    render(table)


def plugin_info_cmd(dist_name: str, output_format: str = "table") -> None:
    reports = [report for report in plugin_reports() if report.distribution == dist_name]
    if not reports:
        if output_format == "json":
            plain(json.dumps({"error": f"Plugin '{dist_name}' was not found."}, indent=2))
        else:
            error_panel("Plugin Not Found", f"Plugin '{dist_name}' was not found.")
        raise SystemExit(1)

    report = reports[0]
    spec = OFFICIAL_PLUGINS.get(dist_name)
    if output_format == "json":
        payload = _report_to_dict(report)
        payload["repository"] = spec.repository if spec else None
        payload["verified_minimum"] = VERIFIED_PLUGINS.get(dist_name)
        plain(json.dumps(payload, indent=2))
        return
    render(kv_table("Plugin Info", {
        "Distribution": report.distribution,
        "Version": report.version or "unknown",
        "Entry point": f"{report.entry_point} = {report.value}",
        "Tier": report.tier,
        "Trusted": report.trusted,
        "State": report.state,
        "Hooks": report.hooks,
        "Object handlers": report.object_handlers,
        "Error": report.error or "",
        "Repository": spec.repository if spec else "",
        "Verified minimum": VERIFIED_PLUGINS.get(dist_name, ""),
        "Lock verified": report.lock.verified if report.lock else "",
        "Lock identity": report.lock.identity if report.lock else "",
    }))


def plugin_trust_cmd(dist_name: str) -> None:
    try:
        dist_version = version(dist_name)
    except PackageNotFoundError:
        error_panel("Plugin Not Found", f"Plugin distribution '{dist_name}' was not found.")
        raise SystemExit(1)
    record_consent(dist_name, dist_version)
    success_panel("Plugin Trusted", f"Trusted community plugin '{dist_name}' version {dist_version}.")


def plugin_untrust_cmd(dist_name: str) -> None:
    if revoke_consent(dist_name):
        success_panel("Plugin Untrusted", f"Removed trust for plugin '{dist_name}'.")
    else:
        info_panel("Plugin Trust", f"Plugin '{dist_name}' was not trusted.")


def plugin_add_cmd(
    dist_name: str,
    *,
    use_uv: bool = False,
    version: str | None = None,
    dry_run: bool = False,
) -> None:
    tier = classify(dist_name)
    command = installer_command(dist_name, use_uv=use_uv, version=version, action="install")
    if dry_run:
        render(kv_table(f"Plugin Add (dry run): {dist_name}", {
            "Tier": tier,
            "Installer": " ".join(command),
            "Version": version or "latest",
            "After install": (
                "provenance-verified, lockfile updated"
                if tier == "official"
                else "auto-loaded when version meets verified minimum"
                if tier == "verified"
                else "requires `dbwarden plugin trust` before loading"
            ),
        }))
        return
    try:
        lock_entry = add_plugin(dist_name, use_uv=use_uv, version=version)
    except PluginInstallError as exc:
        error_panel("Plugin Install Failed", str(exc))
        raise SystemExit(1)
    if lock_entry is None:
        success_panel("Plugin Installed", f"Installed community plugin '{dist_name}'.")
        info(f"Run `dbwarden plugin trust {dist_name}` to allow it to load.")
        return
    success_panel("Plugin Installed", f"Installed official plugin '{dist_name}' version {lock_entry.version}.")


def plugin_remove_cmd(
    dist_name: str,
    *,
    use_uv: bool = False,
    dry_run: bool = False,
) -> None:
    command = installer_command(dist_name, use_uv=use_uv, action="uninstall")
    if dry_run:
        render(kv_table(f"Plugin Remove (dry run): {dist_name}", {
            "Uninstaller": " ".join(command),
            "Also removes": "consent entry and lockfile record",
        }))
        return
    try:
        remove_plugin(dist_name, use_uv=use_uv)
    except PluginInstallError as exc:
        error_panel("Plugin Remove Failed", str(exc))
        raise SystemExit(1)
    success_panel("Plugin Removed", f"Removed plugin '{dist_name}'.")
