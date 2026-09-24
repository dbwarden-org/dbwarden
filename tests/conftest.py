import os
import shutil
from pathlib import Path

import pytest


@pytest.fixture(params=["native", "mocked"])
def symlink_factory(request, monkeypatch):
    """Exercise native links and rejection logic without symlink privileges."""
    links = {}
    resolve = Path.resolve
    is_symlink = Path.is_symlink
    islink = os.path.islink

    def resolved(path, *args, **kwargs):
        absolute = path.absolute()
        for link, target in links.items():
            if absolute.is_relative_to(link):
                return resolve(target / absolute.relative_to(link), *args, **kwargs)
        return resolve(path, *args, **kwargs)

    def create(link, target, *, target_is_directory=False):
        link, target = Path(link).absolute(), Path(target).absolute()
        if request.param == "native":
            try:
                link.symlink_to(target, target_is_directory=target_is_directory)
            except OSError as exc:
                if getattr(exc, "winerror", None) != 1314:
                    raise
                pytest.skip(
                    "Windows account lacks native symlink privilege; mocked case still runs"
                )
            return
        if target_is_directory:
            link.mkdir()
            for child in target.glob("*.py"):
                shutil.copyfile(child, link / child.name)
        else:
            shutil.copyfile(target, link)
        links[link] = target
        monkeypatch.setattr(Path, "resolve", resolved)
        monkeypatch.setattr(
            Path,
            "is_symlink",
            lambda path: path.absolute() in links or is_symlink(path),
        )
        monkeypatch.setattr(
            os.path,
            "islink",
            lambda path: Path(path).absolute() in links or islink(path),
        )

    return create
