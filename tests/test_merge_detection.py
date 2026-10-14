"""Regression tests for the merge/divergence git ancestry heuristic."""

from types import SimpleNamespace

import subprocess

import dbwarden.merge.detection as detection
import dbwarden.engine.version as version


def _ok(stdout=""):
    return SimpleNamespace(returncode=0, stdout=stdout, stderr="")


def _fail():
    return SimpleNamespace(returncode=1, stdout="", stderr="")


def test_is_descendant_uses_configured_upstream(monkeypatch):
    """A branch on `master` with upstream origin/master is forward-moving.

    The previous check was hard-coded to `origin/main` and inverted (it asked
    whether HEAD was an ancestor of the remote), so it reported a divergence on
    any ahead branch and on any repo whose default branch is not `main`.
    """
    merge_base_cmds: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        if cmd[:3] == ["git", "rev-parse", "--show-toplevel"]:
            return _ok("/repo\n")
        if cmd[:4] == ["git", "rev-parse", "--abbrev-ref", "--symbolic-full-name"]:
            return _ok("origin/master\n")
        if cmd[:3] == ["git", "rev-parse", "--verify"]:
            return _ok()
        if cmd[:3] == ["git", "merge-base", "--is-ancestor"]:
            merge_base_cmds.append(cmd)
            return _ok()
        return _fail()

    monkeypatch.setattr(version, "get_migrations_directory", lambda db_name=None: "/repo/migrations/primary")
    monkeypatch.setattr(subprocess, "run", fake_run)

    assert detection._is_descendant_of_base("primary", "deadbeef") is True
    # The remote must be the ancestor (forward progress), not HEAD.
    assert merge_base_cmds == [
        ["git", "merge-base", "--is-ancestor", "origin/master", "HEAD"]
    ]


def test_is_descendant_false_when_no_remote_ref(monkeypatch):
    def fake_run(cmd, **kwargs):
        if cmd[:3] == ["git", "rev-parse", "--show-toplevel"]:
            return _ok("/repo\n")
        # No upstream and no origin/* refs resolve.
        return _fail()

    monkeypatch.setattr(version, "get_migrations_directory", lambda db_name=None: "/repo/migrations/primary")
    monkeypatch.setattr(subprocess, "run", fake_run)

    assert detection._is_descendant_of_base("primary", "deadbeef") is False
