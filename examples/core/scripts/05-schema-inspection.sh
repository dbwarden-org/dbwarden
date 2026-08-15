#!/usr/bin/env bash
set -euo pipefail

echo "=== 05: Schema Inspection ==="

# ── dbwarden diff ──────────────────────────────────────────────
# Compares the current Python model definitions against the live
# database schema and shows what's different.  Useful for:
#   - Verifying that a migration had the intended effect
#   - Detecting manual schema changes that bypassed dbwarden
#   - Checking whether the database is in sync with your models
# It reads the live database schema via information_schema /
# PRAGMA and compares it column-by-column, index-by-index.
echo "--- Diff: models vs database ---"
dbwarden diff --database primary 2>&1 || echo "(no differences expected)"

# ── dbwarden snapshot ──────────────────────────────────────────
# Generates the full DDL for a single table as a SQL CREATE
# statement.  Reads the live database schema and reverse-engineers
# it into DDL.  Useful for:
#   - Documenting the current state of a table
#   - Comparing DDL across environments
#   - Feeding into schema change management tooling
echo ""
echo "--- DDL Snapshot: users table ---"
dbwarden snapshot users --database primary 2>&1 || echo "Note: snapshot requires a live PostgreSQL database"
