from __future__ import annotations

from pathlib import Path

import pytest

from dbwarden.engine.model_discovery.path_discovery import discover_models_in_directory
from dbwarden.plugin import _fetch_url


def test_model_discovery_rejects_symlinked_python_file(tmp_path: Path):
    target = tmp_path / "outside.py"
    target.write_text("class Outside: pass", encoding="utf-8")
    models = tmp_path / "models"
    models.mkdir()
    link = models / "linked.py"
    link.symlink_to(target)

    assert discover_models_in_directory(str(models)) == []


def test_fetch_url_rejects_oversized_response(monkeypatch):
    class Headers:
        def get(self, name):
            return "11"

    class Response:
        headers = Headers()

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self, _size):
            return b"x" * 11

    monkeypatch.setattr("dbwarden.plugin.urllib.request.urlopen", lambda *args, **kwargs: Response())
    with pytest.raises(ValueError, match="maximum allowed size"):
        _fetch_url("https://example.invalid/provenance", max_bytes=10)


def test_fetch_url_rejects_plain_http():
    with pytest.raises(ValueError, match="HTTPS"):
        _fetch_url("http://example.invalid/provenance")
