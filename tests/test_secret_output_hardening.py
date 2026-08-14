from __future__ import annotations

from dbwarden.commands.utils import _mask_password


def test_mask_password_handles_encoded_and_ipv6_urls():
    masked = _mask_password("postgresql://user:p%40ss@[2001:db8::1]:5432/app")
    assert "p%40ss" not in masked
    assert "user:***@" in masked


def test_mask_password_keeps_passwordless_url():
    assert _mask_password("sqlite:///app.db") == "sqlite:///app.db"
