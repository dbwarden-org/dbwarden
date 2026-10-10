#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

DBWARDEN="${DBWARDEN:-dbwarden}"
PYTHON="${PYTHON:-python3}"

echo "=== 01: Base schema ==="

# ── Clean slate ────────────────────────────────────────────────
rm -rf migrations .dbwarden app.db

# ── Restore the base models ────────────────────────────────────
# No data declarations yet: item(id, source, value) and
# country(code, name) are plain tables.
cp stage/base/models.py app/models.py

# ── Initialize and create the schema ───────────────────────────
"$DBWARDEN" init
"$DBWARDEN" make-migrations "base schema"
"$DBWARDEN" migrate --force

# ── Seed item rows ─────────────────────────────────────────────
# Three rows means the batch(size=1) transformation in step 02 runs
# in three chunks.
"$PYTHON" - <<'PY'
import sqlite3

items = [(1, 7, 3), (2, 10, 4), (3, 20, 5)]
with sqlite3.connect("app.db") as connection:
    connection.executemany("INSERT INTO item VALUES (?, ?, ?)", items)
print(f"Seeded {len(items)} item rows")
PY
