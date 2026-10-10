#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

DBWARDEN="${DBWARDEN:-dbwarden}"
PYTHON="${PYTHON:-python3}"

echo "=== 03: Route legacy_people by priority ==="

# This is an alternative to scripts/02-split-people.sh. Run 01-legacy-schema.sh
# first (to create and seed legacy_people), then run EITHER 02 (fan_out split)
# OR this script (priority routing), not both.

# ── Adopt the final models ─────────────────────────────────────
# person(id, name) and customer(person_id, tier) replace legacy_people.
cp stage/final/models.py app/models.py

# ── Materialize the transition ─────────────────────────────────
# The snapshot id is dynamic, so generate the declaration from a template.
SNAPSHOT="$("$PYTHON" - <<'PY'
import json

registry = json.load(open(".dbwarden/snapshots/registry.json"))
print(registry["snapshots"][-1]["snapshot_id"])
PY
)"
echo "Pinning snapshot: $SNAPSHOT"
sed "s/__SNAPSHOT__/$SNAPSHOT/g" \
    stage/transitions/route_people.py.tmpl > app/data/route_people.py

# ── Validate the declaration ───────────────────────────────────
"$DBWARDEN" data transition validate RouteLegacyPeople

# ── Generate and apply one unified migration ───────────────────
# Each source row is routed to exactly one target; the source is preserved.
"$DBWARDEN" make-migrations "route legacy people"
"$DBWARDEN" migrate --force
"$DBWARDEN" check --data

# ── Inspect the result ─────────────────────────────────────────
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
    preserved = [
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name LIKE '_dbwarden_preserved%'"
        ).fetchall()
    ]
print("person:  ", people)
print("customer:", customers)
print("legacy_people present:", bool(legacy))
print("preserved source tables:", preserved)
PY
