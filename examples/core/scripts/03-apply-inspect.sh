#!/usr/bin/env bash
set -euo pipefail

echo "=== 03: Apply & Inspect ==="

# ── dbwarden migrate ───────────────────────────────────────────
# Applies all pending migrations in order.  Behind the scenes:
#   1. Acquires a migration lock in the database (prevents
#      concurrent migrations in multi-process deployments)
#   2. Checks the _dbwarden_migrations tracking table for which
#      migrations have already been applied
#   3. Executes each pending migration's --upgrade section in a
#      transaction (for transactional databases)
#   4. Writes a schema snapshot to .dbwarden/schemas/ so future
#      diffs can compare against the applied state
#   5. Records the migration in the tracking table
echo "--- Applying migrations ---"
dbwarden migrate --database primary

# ── dbwarden status ────────────────────────────────────────────
# Shows which migrations are applied vs pending.  Reads the
# tracking table and compares it against the filesystem.  Use
# this before a deploy to confirm the target is in the expected
# state.
echo ""
echo "--- Migration Status ---"
dbwarden status --database primary

# ── dbwarden history ───────────────────────────────────────────
# Full audit log of every applied migration: version, description,
# filename, timestamp, and migration type (versioned, repeatable).
# Useful for forensic analysis ("how did the schema get here?")
echo ""
echo "--- Migration History ---"
dbwarden history --database primary

# ── dbwarden rollback ──────────────────────────────────────────
# Reverts the last N applied migrations.  Executes the --rollback
# section of each migration in reverse order.  The rollback SQL
# was generated alongside the upgrade SQL and committed together,
# so rollbacks are always available; no "sorry, I didn't write a
# downgrade" surprises in code review.
echo ""
echo "--- Rolling back 1 migration ---"
dbwarden rollback --database primary --count 1

echo ""
echo "--- Status after rollback ---"
dbwarden status --database primary

# Re-apply
echo ""
echo "--- Re-applying ---"
dbwarden migrate --database primary

# ── dbwarden downgrade ─────────────────────────────────────────
# Roll back to a specific version number.  Unlike rollback --count,
# this targets an exact version.  Useful for reverting to a known-
# good state before a problematic deploy.
echo ""
echo "--- Downgrade to version 0000 (all rolled back) ---"
dbwarden downgrade --to 0000 --database primary
dbwarden status --database primary

# Re-apply all
echo ""
echo "--- Final apply ---"
dbwarden migrate --database primary
dbwarden status --database primary

# ── dbwarden check ─────────────────────────────────────────────
# Schema safety analyzer.  Scans all migrations (applied and
# pending) and flags potential issues: destructive column drops,
# NOT NULL additions on populated tables, type changes that
# might truncate data, etc.  Run this before merge.
echo ""
echo "--- Schema validation (check) ---"
dbwarden check --database primary

# ── dbwarden check-db ──────────────────────────────────────────
# Connectivity check.  Tries to connect to the database and
# reports success/failure.  Useful in CI init scripts to verify
# the service is reachable before running migrations.
echo ""
echo "--- Database connectivity (check-db) ---"
dbwarden check-db --database primary
