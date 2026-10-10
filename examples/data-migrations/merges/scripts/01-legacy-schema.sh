#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

DBWARDEN="${DBWARDEN:-dbwarden}"
PYTHON="${PYTHON:-python3}"

echo "=== 01: Legacy ledger schema ==="

# ── Clean slate ────────────────────────────────────────────────
rm -rf migrations .dbwarden app.db

# ── Restore the legacy models ──────────────────────────────────
# Both legacy_a and legacy_b share the same shape so they can be merged.
cp stage/legacy/models.py app/models.py

# ── Initialize and create the legacy schema ────────────────────
"$DBWARDEN" init
"$DBWARDEN" make-migrations "legacy merge sources"
"$DBWARDEN" migrate --force

# ── Seed overlapping ledger rows ───────────────────────────────
# Account 10 appears in both ledgers with different names/amounts; the
# merge sums the amounts and keeps the priority-0 name.
"$PYTHON" - <<'PY'
import sqlite3

legacy_a = [(1, 10, 2, "first"), (2, 10, 3, "second"), (3, 20, 5, "twenty")]
legacy_b = [(1, 10, 7, "other"), (2, 30, 11, "thirty")]
with sqlite3.connect("app.db") as connection:
    connection.executemany("INSERT INTO legacy_a VALUES (?, ?, ?, ?)", legacy_a)
    connection.executemany("INSERT INTO legacy_b VALUES (?, ?, ?, ?)", legacy_b)
print(f"Seeded {len(legacy_a)} + {len(legacy_b)} ledger rows")
PY

# ── Report the snapshot id ─────────────────────────────────────
"$PYTHON" - <<'PY'
import json

registry = json.load(open(".dbwarden/snapshots/registry.json"))
print("Snapshot id:", registry["snapshots"][-1]["snapshot_id"])
PY
