from __future__ import annotations

import pytest

from dbwarden.plugin import installer_command


@pytest.mark.parametrize("name", ["-e", "--index-url=http://evil", "foo/bar", "foo;rm -rf /"])
def test_installer_rejects_unsafe_distribution_names(name):
    with pytest.raises(ValueError, match="Invalid plugin distribution name"):
        installer_command(name)


def test_installer_accepts_normal_distribution_name():
    command = installer_command("dbwarden-seeds", version="1.2.3")
    assert command[-1] == "dbwarden-seeds==1.2.3"
