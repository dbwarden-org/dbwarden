"""Git utilities for merge handling.

Minimal git integration via subprocess calls.
No gitpython dependency; assumes git is available.
"""
from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Optional

from dbwarden.logging import get_component_logger

logger = get_component_logger("merge")


def _run_git(args: list[str], cwd: str | None = None) -> tuple[int, str, str]:
    """Run a git command and return (returncode, stdout, stderr).

    Args:
        args: Git command arguments (without 'git' prefix).
        cwd: Working directory.

    Returns:
        Tuple of (returncode, stdout, stderr).
    """
    try:
        result = subprocess.run(
            ["git"] + args,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=30,
        )
        return result.returncode, result.stdout.strip(), result.stderr.strip()
    except subprocess.TimeoutExpired:
        logger.warning("Git command timed out: git %s", " ".join(args))
        return 1, "", "timeout"
    except FileNotFoundError:
        logger.warning("Git not found in PATH")
        return 1, "", "git not found"


def is_git_available() -> bool:
    """Check if git is available in PATH.

    Returns:
        True if git is available.
    """
    rc, _, _ = _run_git(["--version"])
    return rc == 0


def get_merge_base() -> Optional[str]:
    """Get the merge-base commit hash of the current HEAD's parents.

    Returns:
        Merge-base commit hash, or None if not in a merge state.
    """
    # First check if we're in a merge state
    rc, stdout, _ = _run_git(["rev-parse", "HEAD"])
    if rc != 0:
        return None

    # Try to find merge-base of HEAD's parents
    rc, stdout, _ = _run_git(["merge-base", "HEAD^1", "HEAD^2"])
    if rc == 0 and stdout:
        return stdout

    # Not in a merge state
    return None


def get_file_at_commit(commit: str, file_path: str) -> Optional[str]:
    """Read a file at a specific git commit.

    Args:
        commit: Git commit hash or ref.
        file_path: Path to the file relative to repo root.

    Returns:
        File content as string, or None if not found.
    """
    rc, stdout, _ = _run_git(["show", f"{commit}:{file_path}"])
    if rc == 0:
        return stdout
    return None


def is_clean_working_tree() -> bool:
    """Check if the working tree has no uncommitted changes.

    Returns:
        True if working tree is clean.
    """
    rc, stdout, _ = _run_git(["status", "--porcelain"])
    return rc == 0 and stdout == ""


def has_conflict_markers(file_path: str) -> bool:
    """Check if a file contains git conflict markers.

    Args:
        file_path: Path to the file.

    Returns:
        True if conflict markers are found.
    """
    try:
        content = Path(file_path).read_text()
        # Check for conflict markers
        markers = ["<<<<<<<", ">>>>>>>", "======="]
        for marker in markers:
            if marker in content:
                return True
    except Exception:
        pass
    return False


def get_current_branch() -> Optional[str]:
    """Get the current branch name.

    Returns:
        Branch name, or None if in detached HEAD state.
    """
    rc, stdout, _ = _run_git(["rev-parse", "--abbrev-ref", "HEAD"])
    if rc == 0 and stdout != "HEAD":
        return stdout
    return None


def get_file_content_at_commit(commit: str, file_path: str) -> Optional[str]:
    """Get file content at a specific commit.

    Args:
        commit: Git commit hash.
        file_path: Path to the file.

    Returns:
        File content, or None if not found.
    """
    return get_file_at_commit(commit, file_path)
