#!/usr/bin/env bash
set -euo pipefail

# Run from the example root regardless of where it is invoked.
cd "$(dirname "$0")/.."

# Allow overriding the tools, e.g. to point at a virtualenv.
DBWARDEN="${DBWARDEN:-dbwarden}"
PYTHON="${PYTHON:-python3}"

echo "=== 01: Legacy schema ==="

# ── Clean slate ────────────────────────────────────────────────
# Remove generated migrations, snapshots, and the SQLite database so
# the example always starts from the same pre-migration state.
rm -rf migrations .dbwarden app.db

# ── Restore the legacy model ───────────────────────────────────
# The legacy stage owns legacy_people(id, full_name, email).
cp stage/legacy/models.py app/models.py

# ── Initialize and create the legacy schema ────────────────────
# `init` reads dbwarden.py and creates migrations/primary/.
# `make-migrations` diffs app/models.py and writes SQL + a plan.
# `migrate --force` applies it and registers a schema snapshot.
"$DBWARDEN" init
"$DBWARDEN" make-migrations "legacy schema"
"$DBWARDEN" migrate --force

# ── Seed legacy rows ───────────────────────────────────────────
# Insert the rows the split transition will move. This uses the
# stdlib so the example has no extra dependencies.
"$PYTHON" - <<'PY'
import sqlite3

rows = [
    (1, "Ada Lovelace", "ada@example.com"),
    (2, "Alan Turing", "alan@math.org"),
    (3, "Grace Hopper", "grace@navy.mil"),
]
with sqlite3.connect("app.db") as connection:
    connection.executemany("INSERT INTO legacy_people VALUES (?, ?, ?)", rows)
print(f"Seeded {len(rows)} legacy_people rows")
PY

# ── Report the snapshot id ─────────────────────────────────────
# The split transition must pin the schema snapshot that still has
# legacy_people. scripts/02 reads the same registry entry.
"$PYTHON" - <<'PY'
import json

registry = json.load(open(".dbwarden/snapshots/registry.json"))
print("Snapshot id:", registry["snapshots"][-1]["snapshot_id"])
PY
