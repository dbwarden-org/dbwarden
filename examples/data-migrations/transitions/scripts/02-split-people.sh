#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

DBWARDEN="${DBWARDEN:-dbwarden}"
PYTHON="${PYTHON:-python3}"

echo "=== 02: Split legacy_people ==="

# ── Adopt the final models ─────────────────────────────────────
# person(id, name) and customer(person_id, tier) replace legacy_people.
cp stage/final/models.py app/models.py

# ── Materialize the transition ─────────────────────────────────
# The snapshot id is dynamic, so generate the declaration from a
# template and drop it where data_paths=["app/data"] will find it.
SNAPSHOT="$("$PYTHON" - <<'PY'
import json

registry = json.load(open(".dbwarden/snapshots/registry.json"))
print(registry["snapshots"][-1]["snapshot_id"])
PY
)"
echo "Pinning snapshot: $SNAPSHOT"
sed "s/__SNAPSHOT__/$SNAPSHOT/g" \
    stage/transitions/split_people.py.tmpl > app/data/split_people.py

# ── Generate one unified schema + data migration ───────────────
# The transition owns the legacy_people drop, so dbwarden does not
# emit a duplicate schema drop. Dropping the source is CRITICAL.
"$DBWARDEN" make-migrations "split legacy people"

# ── Apply ──────────────────────────────────────────────────────
# Data operations are reported as WARN/CRITICAL and need --force.
"$DBWARDEN" migrate --force

# ── Verify convergence and inspect the result ──────────────────
"$DBWARDEN" check --data
"$PYTHON" - <<'PY'
import sqlite3

with sqlite3.connect("app.db") as connection:
    people = connection.execute("SELECT id, name FROM person ORDER BY id").fetchall()
    customers = connection.execute(
        "SELECT person_id, tier FROM customer ORDER BY person_id"
    ).fetchall()
    legacy = connection.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='legacy_people'"
    ).fetchall()
print("person:  ", people)
print("customer:", customers)
print("legacy_people dropped:", not legacy)
PY
