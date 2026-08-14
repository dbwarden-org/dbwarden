from __future__ import annotations

from pathlib import Path

from dbwarden.commands.generate_models.writer import _write_models


def test_generated_model_filename_is_path_safe(tmp_path: Path):
    _write_models(
        str(tmp_path),
        [{"name": "tenant/users", "columns": [], "dialect": "sqlite"}],
        single_file=False,
    )
    assert (tmp_path / "tenant_users.py").exists()
    assert not (tmp_path / "tenant" / "users.py").exists()
