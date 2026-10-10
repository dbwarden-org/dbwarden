#!/usr/bin/env bash
set -euo pipefail

# Run from the example root regardless of where it is invoked.
cd "$(dirname "$0")/.."

# Allow overriding the tools, e.g. to point at a virtualenv.
DBWARDEN="${DBWARDEN:-dbwarden}"
PYTHON="${PYTHON:-python3}"

echo "=== 01: Generate and apply the data migration ==="

# ── Clean slate ────────────────────────────────────────────────
# Remove generated migrations, snapshots, and the SQLite database so
# the example always starts from the same pre-migration state.
rm -rf migrations .dbwarden app.db

# ── Initialize ─────────────────────────────────────────────────
# `init` reads dbwarden.py and creates migrations/primary/.
"$DBWARDEN" init

# ── Generate one unified schema + data migration ───────────────
# app/models.py declares three kinds of data work:
#   * Country  - managed rows written inline in Python
#   * Currency - managed rows loaded from data/currencies.csv
#   * User     - a derived `slug` column plus an `email` validation
# The generator emits CREATE TABLE statements and the data operations
# into one versioned migration.
"$DBWARDEN" make-migrations "seed countries, currencies, and slugs"

# ── Apply ──────────────────────────────────────────────────────
# Data operations are reported as WARN and require --force. On a real
# project you would review `dbwarden check --data` first.
"$DBWARDEN" migrate --force

# ── Verify convergence ─────────────────────────────────────────
# check --data confirms the declared rows, the derived column, and the
# validation rule all hold against the database.
"$DBWARDEN" check --data

# ── Inspect the result ─────────────────────────────────────────
"$PYTHON" - <<'PY'
import sqlite3

with sqlite3.connect("app.db") as connection:
    countries = connection.execute("SELECT code, name FROM countries ORDER BY code").fetchall()
    currencies = connection.execute("SELECT code, name FROM currencies ORDER BY code").fetchall()
    users = connection.execute("SELECT id, name, slug FROM users ORDER BY id").fetchall()
print("countries: ", countries)
print("currencies:", currencies)
print("users:     ", users)
PY
