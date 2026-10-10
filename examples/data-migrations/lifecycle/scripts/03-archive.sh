#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

DBWARDEN="${DBWARDEN:-dbwarden}"
PYTHON="${PYTHON:-python3}"

echo "=== 03: Archive the retired country ==="

# ── Adopt the archive revision ─────────────────────────────────
# AR is no longer declared. on_missing="archive" moves it to
# country_archive instead of leaving it in place or dropping it.
cp stage/archive/models.py app/models.py

# ── Generate and apply ─────────────────────────────────────────
# Archiving is a WARN-level data operation requiring --force.
"$DBWARDEN" make-migrations "archive retired country"
"$DBWARDEN" migrate --force

# ── Verify convergence and inspect the result ──────────────────
"$DBWARDEN" check --data
"$PYTHON" - <<'PY'
import sqlite3

with sqlite3.connect("app.db") as connection:
    countries = connection.execute(
        "SELECT code, name FROM country ORDER BY code"
    ).fetchall()
    archived = connection.execute(
        "SELECT code, name FROM country_archive ORDER BY code"
    ).fetchall()
print("country:        ", countries)
print("country_archive:", archived)
PY
