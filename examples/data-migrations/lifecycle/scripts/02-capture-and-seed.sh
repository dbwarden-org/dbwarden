#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

DBWARDEN="${DBWARDEN:-dbwarden}"
PYTHON="${PYTHON:-python3}"

echo "=== 02: Capture values and seed countries ==="

# ── Adopt the capture revision ─────────────────────────────────
# item.value becomes derived (capture rollback + batching); country
# gains a managed_rows declaration owning UY and AR.
cp stage/capture/models.py app/models.py

# ── Generate and apply ─────────────────────────────────────────
# The generated write is WARN-level, so --force acknowledges it.
"$DBWARDEN" make-migrations "capture values and seed countries"
"$DBWARDEN" migrate --force

# ── Verify convergence and inspect the result ──────────────────
"$DBWARDEN" check --data
"$PYTHON" - <<'PY'
import sqlite3

with sqlite3.connect("app.db") as connection:
    items = connection.execute(
        "SELECT id, source, value FROM item ORDER BY id"
    ).fetchall()
    countries = connection.execute(
        "SELECT code, name FROM country ORDER BY code"
    ).fetchall()
print("item (value = source + 1):", items)
print("country:                 ", countries)
PY
