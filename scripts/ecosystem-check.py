#!/usr/bin/env python3
"""Run every known plugin's test suite against this checkout of DBWarden core.

Usage:
    python scripts/ecosystem-check.py                       # clone official plugins
    python scripts/ecosystem-check.py --all                 # official + verified
    python scripts/ecosystem-check.py --only dbwarden-seeds
    python scripts/ecosystem-check.py --local ~/src         # sibling working copies

Clones each plugin, installs it alongside the local core, and runs its suite.
The point is to learn that a core change breaks a plugin *before* the release
that ships it, rather than from a bug report afterwards.

Report-only by design when run over verified (third-party) plugins: their CI is
not core's responsibility and a plugin can be red for reasons that have nothing
to do with this change. Exit status reflects official plugins only, unless
--strict is passed.

A checkout whose ``setup()`` registers nothing is reported ``empty`` rather than
``ok``. Placeholder packages otherwise pass trivially and make the whole run look
green while proving nothing.
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from dbwarden._verified import VERIFIED_METADATA  # noqa: E402
from dbwarden._official import OFFICIAL_PLUGINS  # noqa: E402


@dataclass(frozen=True)
class Target:
    dist_name: str
    repository: str
    official: bool


@dataclass
class Result:
    target: Target
    status: str  # ok | failed | empty | skipped
    detail: str = ""


def _targets(include_verified: bool, only: str | None) -> list[Target]:
    targets = [
        Target(name, spec.repository, official=True)
        for name, spec in sorted(OFFICIAL_PLUGINS.items())
    ]
    if include_verified:
        targets += [
            Target(name, meta.repository, official=False)
            for name, meta in sorted(VERIFIED_METADATA.items())
        ]
    if only:
        targets = [t for t in targets if t.dist_name == only]
    return targets


def _run(command: list[str], cwd: Path) -> tuple[int, str]:
    proc = subprocess.run(command, cwd=cwd, capture_output=True, text=True)
    return proc.returncode, (proc.stdout + proc.stderr)


# A plugin whose setup() registers nothing is a stub, not a plugin. Without this
# check the run reports "ok" for an unpublished placeholder, which is worse than
# reporting nothing: it says the ecosystem is fine when it was never tested.
SUBSTANCE_CHECK = """
import sys
from dbwarden.plugin_conformance import RecordingRegistrar
import {package} as plugin

registrar = RecordingRegistrar()
plugin.setup(registrar)
count = len(registrar.hooks) + len(registrar.object_handlers)
print(f"registered={{count}}")
sys.exit(0 if count else 1)
"""


def _dev_dependencies(checkout: Path) -> list[str]:
    """The plugin's own dev group, so its suite runs the way its CI runs it.

    A fixed dependency list here would test the plugins under a different
    environment than they are developed in, and would report failures that say
    more about this script than about core.
    """
    import tomllib

    pyproject = checkout / "pyproject.toml"
    try:
        data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    except Exception:
        return ["pytest"]

    declared = data.get("dependency-groups", {}).get("dev", [])
    requirements = [item for item in declared if isinstance(item, str)]
    if not any(item.split("[")[0].split(">")[0].split("=")[0].strip() == "pytest" for item in requirements):
        requirements.append("pytest")
    return requirements


def _remote_url(checkout: Path) -> str:
    code, output = _run(["git", "remote", "get-url", "origin"], cwd=checkout)
    return output.strip() if code == 0 else ""


def _local_checkout(target: Target, local_root: Path) -> tuple[Path | None, str]:
    """Find the working copy that really is this plugin.

    Matching on the git remote rather than the directory name: a directory named
    after a plugin is not necessarily that plugin's repository, and an abandoned
    copy sitting next to the real one would otherwise be tested instead of it.
    """
    slug = target.repository.rstrip("/").split("github.com/")[-1]
    candidates = [
        path
        for path in (
            local_root / "dbwarden-org" / target.dist_name,
            local_root / target.dist_name,
            *sorted(local_root.glob(f"*/{target.dist_name}")),
        )
        if (path / "pyproject.toml").exists()
    ]
    if not candidates:
        return None, f"no working copy of {target.dist_name} under {local_root}"

    for candidate in candidates:
        if slug in _remote_url(candidate):
            return candidate, ""

    listed = ", ".join(str(c) for c in candidates)
    return None, (
        f"no working copy of {target.dist_name} has origin {slug}. "
        f"Checked: {listed}. Rename or remove the ones that are not this plugin."
    )


def _obtain(target: Target, workdir: Path, local_root: Path | None) -> tuple[Path | None, str]:
    """Get a checkout to test: a sibling working copy, or a fresh clone."""
    if local_root is not None:
        return _local_checkout(target, local_root)

    checkout = workdir / target.dist_name
    code, output = _run(
        ["git", "clone", "--depth", "1", f"{target.repository}.git", str(checkout)],
        cwd=workdir,
    )
    if code != 0:
        return None, f"clone failed:\n{output[-600:]}"
    return checkout, ""


def _check(target: Target, workdir: Path, local_root: Path | None) -> Result:
    checkout, problem = _obtain(target, workdir, local_root)
    if checkout is None:
        return Result(target, "skipped", problem)

    # Never build inside the user's working copy.
    venv = workdir / f"{target.dist_name}-venv"
    code, output = _run(["uv", "venv", str(venv)], cwd=workdir)
    if code != 0:
        return Result(target, "skipped", f"venv failed:\n{output[-600:]}")

    python = venv / "bin" / "python"
    # The local core, not the released one. That is the entire point.
    code, output = _run(
        ["uv", "pip", "install", "--python", str(python),
         "-e", str(REPO_ROOT), "-e", str(checkout), *_dev_dependencies(checkout)],
        cwd=workdir,
    )
    if code != 0:
        return Result(target, "skipped", f"install failed:\n{output[-600:]}")

    package = target.dist_name.replace("-", "_")
    code, output = _run(
        [str(python), "-c", SUBSTANCE_CHECK.format(package=package)], cwd=workdir
    )
    if code != 0:
        return Result(
            target,
            "empty",
            "setup() registered nothing. This checkout is a placeholder, so a "
            f"green test run below would prove nothing.\n{output[-400:]}",
        )

    code, output = _run(
        [str(python), "-m", "pytest", "-q", "-p", "no:cacheprovider"],
        cwd=checkout,
    )
    if code != 0:
        return Result(target, "failed", output[-2000:])
    return Result(target, "ok", output.strip().splitlines()[-1] if output.strip() else "")


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--all", action="store_true", help="include verified plugins")
    parser.add_argument("--only", help="check a single distribution")
    parser.add_argument(
        "--strict",
        action="store_true",
        help="fail the run when a verified (third-party) plugin fails",
    )
    parser.add_argument(
        "--local",
        metavar="DIR",
        help=(
            "test sibling working copies under DIR instead of cloning. Use this "
            "before pushing, which is exactly when you want to know."
        ),
    )
    args = parser.parse_args(argv)
    local_root = Path(args.local).expanduser().resolve() if args.local else None
    if local_root is not None and not local_root.is_dir():
        print(f"{local_root} is not a directory")
        return 2

    if shutil.which("uv") is None:
        print("uv is required: https://docs.astral.sh/uv/")
        return 2

    targets = _targets(args.all, args.only)
    if not targets:
        print("No targets.")
        return 0

    results: list[Result] = []
    with tempfile.TemporaryDirectory(prefix="dbwarden-ecosystem-") as tmp:
        workdir = Path(tmp)
        for target in targets:
            tier = "official" if target.official else "verified"
            print(f"==> {target.dist_name} ({tier})", flush=True)
            result = _check(target, workdir, local_root)
            results.append(result)
            print(f"    {result.status}: {result.detail.splitlines()[-1] if result.detail else ''}", flush=True)

    print("\n" + "=" * 60)
    print("Ecosystem check against local core")
    print("=" * 60)
    for result in results:
        tier = "official" if result.target.official else "verified"
        print(f"{result.status.upper():8} {result.target.dist_name} ({tier})")

    # "empty" counts as a failure for official plugins: a placeholder on the
    # remote means this run verified nothing about them.
    failures = [r for r in results if r.status in ("failed", "empty")]
    for result in failures:
        print(f"\n----- {result.target.dist_name} ({result.status}) -----\n{result.detail}")

    blocking = [
        r for r in failures if r.target.official or args.strict
    ]
    if blocking:
        print(f"\n{len(blocking)} blocking failure(s).")
        return 1
    if failures:
        print(f"\n{len(failures)} third-party failure(s), not blocking.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
