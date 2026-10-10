#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

DBWARDEN="${DBWARDEN:-dbwarden}"
PYTHON="${PYTHON:-python3}"

echo "=== 02: Consolidate the ledgers ==="

# ── Adopt the final model ──────────────────────────────────────
# account(id, total, name) replaces legacy_a and legacy_b.
cp stage/final/models.py app/models.py

# ── Materialize the merge transition ───────────────────────────
SNAPSHOT="$("$PYTHON" - <<'PY'
import json

registry = json.load(open(".dbwarden/snapshots/registry.json"))
print(registry["snapshots"][-1]["snapshot_id"])
PY
)"
echo "Pinning snapshot: $SNAPSHOT"
sed "s/__SNAPSHOT__/$SNAPSHOT/g" \
    stage/transitions/consolidate.py.tmpl > app/data/consolidate.py

# ── Generate and apply ─────────────────────────────────────────
# Merging is a WARN-level data operation, so --force acknowledges it.
"$DBWARDEN" make-migrations "consolidate accounts"
"$DBWARDEN" migrate --force

# ── Verify convergence and inspect the result ──────────────────
"$DBWARDEN" check --data
"$PYTHON" - <<'PY'
import sqlite3

with sqlite3.connect("app.db") as connection:
    accounts = connection.execute(
        "SELECT id, total, name FROM account ORDER BY id"
    ).fetchall()
    legacy = connection.execute(
        "SELECT name FROM sqlite_master "
        "WHERE type='table' AND name IN ('legacy_a', 'legacy_b')"
    ).fetchall()
    preserved = connection.execute(
        "SELECT COUNT(*) FROM sqlite_master "
        "WHERE name LIKE '_dbwarden_merge_preserved_%'"
    ).fetchone()[0]
print("account:            ", accounts)
print("legacy tables left: ", legacy)
print("preserved sources:  ", preserved)
PY
