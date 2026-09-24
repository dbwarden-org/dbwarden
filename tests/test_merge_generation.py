import json
import subprocess
import sys
from copy import deepcopy

import pytest

from dbwarden.commands.make_migrations.generation import generate_files
from dbwarden.commands.merge import merge_cmd
from dbwarden.config import set_dev_mode
from dbwarden.engine.core.model_state import model_state_to_dict
from dbwarden.engine.core.models import ModelColumn, ModelTable
from dbwarden.engine.generation_state import effective_state, schema_state
from dbwarden.engine.offline import diff_model_states
from dbwarden.engine.safety.plans import read_trusted_plan
from dbwarden.engine.version import get_migration_filepaths_by_version
from dbwarden.merge.marker import is_superseded


@pytest.fixture(autouse=True)
def _no_bytecode(monkeypatch):
    """Model discovery imports write __pycache__, dirtying the merge precondition."""
    monkeypatch.setattr(sys, "dont_write_bytecode", True)


def _git(*args):
    return subprocess.check_output(
        [
            "git",
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.invalid",
            "-c",
            "commit.gpgsign=false",
            *args,
        ],
        text=True,
    ).strip()


@pytest.mark.parametrize("failed_stage", [None, "marker", "record", "state"])
def test_merge_collisions_and_pending_split_use_shared_writer(
    tmp_path, monkeypatch, failed_stage
):
    set_dev_mode(False)
    monkeypatch.chdir(tmp_path)
    (tmp_path / "dbwarden.py").write_text(
        "from dbwarden import database_config\ndatabase_config(database_name='primary', default=True, database_type='sqlite', database_url_sync='sqlite:///:memory:', model_paths=['models.py'])\n",
        encoding="utf-8",
    )
    (tmp_path / "models.py").write_text("", encoding="utf-8")
    directory = tmp_path / "migrations/primary"
    directory.mkdir(parents=True)
    state_path = tmp_path / ".dbwarden/model_state.primary.json"
    state_path.parent.mkdir()
    column = ModelColumn("id", "INTEGER", False, True, False, None, None)
    baseline = schema_state(
        model_state_to_dict(
            [ModelTable("items", [column]), ModelTable("obsolete", [column])]
        )
    )
    state_path.write_text(json.dumps(baseline), encoding="utf-8")
    _git("init", "-q")
    _git("add", ".")
    _git("commit", "-qm", "baseline")
    merge_base = _git("rev-parse", "HEAD")
    target_a = deepcopy(baseline)
    target_a["tables"]["items"]["columns"]["nickname"] = {
        "name": "nickname",
        "type": "TEXT",
        "nullable": True,
    }
    del target_a["tables"]["obsolete"]
    up, down = diff_model_states(baseline, target_a, db_name="primary")
    branch_a = generate_files(
        up,
        down,
        migrations_dir=str(directory),
        database="primary",
        db_name="primary",
        baseline=baseline,
        target=target_a,
        threshold="WARN",
        description="branch a",
    )
    branch_b_dir = tmp_path / "branch_b"
    target_b = deepcopy(baseline)
    target_b["tables"]["items"]["columns"]["active"] = {
        "name": "active",
        "type": "INTEGER",
        "nullable": True,
    }
    up, down = diff_model_states(baseline, target_b, db_name="primary")
    branch_b = generate_files(
        up,
        down,
        migrations_dir=str(branch_b_dir),
        database="primary",
        db_name="primary",
        baseline=baseline,
        target=target_b,
        description="branch b",
    )
    for path in branch_b_dir.iterdir():
        path.rename(directory / path.name)
    merged = deepcopy(target_a)
    merged["tables"]["items"]["columns"]["active"] = target_b["tables"]["items"][
        "columns"
    ]["active"]
    monkeypatch.setattr("dbwarden.commands.merge.get_merge_base", lambda: merge_base)
    monkeypatch.setattr(
        "dbwarden.commands.merge._rebuild_current_state", lambda _: merged
    )
    _git("add", ".")
    _git("commit", "-qm", "branch migrations")
    before = {path.name: path.read_bytes() for path in directory.iterdir()}
    merge_cmd(database="primary", split_at_severity="WARN", dry_run=True)
    assert {path.name: path.read_bytes() for path in directory.iterdir()} == before
    assert not (tmp_path / ".dbwarden/merges").exists()
    if failed_stage:
        from dbwarden import files

        write = files._atomic_write_bytes
        failed = False
        before_all = {
            p: p.read_bytes()
            for root in (directory, state_path.parent)
            for p in root.rglob("*")
            if p.is_file()
        }

        def fail_once(path, content):
            nonlocal failed
            matches = {
                "marker": content.startswith(b"-- dbwarden:superseded"),
                "record": path.parent.name == "merges",
                "state": path.name.startswith("model_state"),
            }
            if not failed and matches[failed_stage]:
                failed = True
                raise OSError("injected merge write failure")
            return write(path, content)

        with monkeypatch.context() as scoped:
            scoped.setattr(files, "_atomic_write_bytes", fail_once)
            with pytest.raises(OSError, match="injected merge"):
                merge_cmd(database="primary", split_at_severity="WARN")
        assert failed
        assert {
            p: p.read_bytes()
            for root in (directory, state_path.parent)
            for p in root.rglob("*")
            if p.is_file()
        } == before_all
    merge_cmd(database="primary", split_at_severity="WARN")
    originals = branch_a + branch_b
    assert all(
        is_superseded(directory / artifact["filename"]) for artifact in originals
    )
    records = list((tmp_path / ".dbwarden/merges").glob("*.json"))
    record = json.loads(records[0].read_text())
    assert record["merge_base_version"] == "0000"
    assert record["superseded_versions"] == ["0001", "0002"]
    assert record["reconciliation_versions"] == ["0003", "0004"]
    assert len(record["superseded_files"]) == 3
    assert all(
        read_trusted_plan(directory / name)[0]
        for name in record["reconciliation_files"]
    )
    composed, _ = effective_state(
        baseline, get_migration_filepaths_by_version(str(directory)), strict=True
    )
    assert composed == merged
    recorded = json.loads(state_path.read_text())
    assert schema_state(recorded) == merged
    assert recorded["generation_base"] == baseline
    assert recorded["generation_versions"] == ["0003", "0004"]
    assert json.loads((state_path.parent / "model_state.json").read_text()) == recorded
