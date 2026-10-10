#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

DBWARDEN="${DBWARDEN:-dbwarden}"
PYTHON="${PYTHON:-python3}"

echo "=== 04: Rollback, reapply, converge ==="

# ── Roll back the two data migrations ──────────────────────────
# Rolling back the archive migration restores AR from country_archive.
# Rolling back the capture migration restores the captured preimage of
# item.value, so the original values reappear.
"$DBWARDEN" rollback --count 2
"$PYTHON" - <<'PY'
import sqlite3

with sqlite3.connect("app.db") as connection:
    items = connection.execute(
        "SELECT id, source, value FROM item ORDER BY id"
    ).fetchall()
    countries = connection.execute(
        "SELECT code, name FROM country ORDER BY code"
    ).fetchall()
print("restored item:   ", items)
print("restored country:", countries)
PY

# ── Reapply the data migrations ────────────────────────────────
# Ordinary migrate will not silently replay a rolled-back data
# migration: --reapply-data starts a new linked epoch. --force is
# still needed to acknowledge the WARN/CRITICAL data operations.
"$DBWARDEN" migrate --force --reapply-data

# ── Confirm convergence ────────────────────────────────────────
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
    archived = connection.execute(
        "SELECT code, name FROM country_archive ORDER BY code"
    ).fetchall()
print("reapplied item:  ", items)
print("country:         ", countries)
print("country_archive: ", archived)
PY
