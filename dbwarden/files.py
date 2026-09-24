from __future__ import annotations

import os
import tempfile
from contextlib import contextmanager
from pathlib import Path


def atomic_write_text(path: Path, content: str) -> None:
    """Write text beside the destination, then replace it atomically."""
    _atomic_write_bytes(path, content.encode("utf-8"))


def _atomic_write_bytes(path: Path, content: bytes) -> None:
    path = path.resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
        text=True,
    )
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except Exception:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


@contextmanager
def preserve_files_on_error(paths):
    """Restore file bytes after a failed write sequence; not a crash journal."""
    previous = {
        Path(path): Path(path).read_bytes() if Path(path).exists() else None
        for path in paths
    }
    try:
        yield
    except Exception as original:
        failed = []
        for path, content in reversed(list(previous.items())):
            try:
                if content is None:
                    path.unlink(missing_ok=True)
                elif not path.exists() or path.read_bytes() != content:
                    _atomic_write_bytes(path, content)
            except OSError:
                failed.append(str(path))
        if failed:
            raise OSError("Could not restore files: " + ", ".join(failed)) from original
        raise
