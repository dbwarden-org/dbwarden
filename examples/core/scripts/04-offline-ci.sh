#!/usr/bin/env bash
set -euo pipefail

echo "=== 04: Offline & CI Workflows ==="

# ── The offline workflow ───────────────────────────────────────
# Normally, make-migrations compares your models against a live
# database to detect changes.  In CI, you may not have a database
# available.  The offline workflow solves this:
#
#   1. Run `dbwarden export-models` against a database (dev/CI)
#      to produce a .dbwarden/model_state.json snapshot
#   2. Commit that JSON file to your repo
#   3. In CI, run `dbwarden make-migrations --offline`; it
#      compares models against the committed snapshot instead of
#      a live database
#   4. The snapshot stays in sync as migrations are applied

# Ensure models are applied so export has a baseline
dbwarden migrate --database primary 2>/dev/null || true

# ── dbwarden export-models ─────────────────────────────────────
# Connects to the database, reads its schema, and writes a JSON
# representation to .dbwarden/model_state.json.  This file is
# the "source of truth" for offline diffing.  It includes table
# definitions, column types, indexes, and checksums.
echo "--- Exporting model state ---"
dbwarden export-models --database primary

# Show the exported state file
echo ""
echo "=== Exported Model State ==="
cat .dbwarden/model_state.json 2>/dev/null || echo "(file not found)"

# ── Offline migration ──────────────────────────────────────────
# With model_state.json committed, make-migrations --offline
# generates SQL using only the file; no database connection
# needed.  The CLI reads the snapshot, compares it against the
# current Python model definitions, and produces a migration.
# The snapshot is updated in-place after generation.
echo ""
echo "--- Offline migration generation ---"
dbwarden make-migrations "offline schema change" --offline --database primary --verbose 2>&1 || echo "Note: Offline mode requires state to differ from models"
