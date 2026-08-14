from __future__ import annotations

from dbwarden.database.connection import sanitize_connection_error


def test_connection_error_redacts_url_password():
    message = sanitize_connection_error(
        "connection failed for postgresql://user:secret-password@db.example/app"
    )
    assert "secret-password" not in message
    assert "postgresql://user:***@db.example/app" in message


def test_connection_error_leaves_non_url_text_unchanged():
    assert sanitize_connection_error("connection refused by db.example") == "connection refused by db.example"
