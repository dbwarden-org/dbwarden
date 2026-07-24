#!/usr/bin/env bash
set -euo pipefail

echo "=== 02: Models & Migrations ==="

# ── dbwarden make-migrations ───────────────────────────────────
# This is the core command.  It:
#   1. Scans the model_paths configured in dbwarden.py for
#      SQLAlchemy DeclarativeBase subclasses
#   2. Compares the current model schema against the live database
#      (or against a stored snapshot if --offline is used)
#   3. Generates an .sql file with both --upgrade and --rollback
#      sections, and a .plan.json with metadata about what changed
#   4. Names the file as: {database}__{0001}_{description}.sql
#
# The description argument becomes part of the filename.  Keep it
# short and semantic: it's what appears in `dbwarden history`.
dbwarden make-migrations "create core tables" --database primary

# Show the generated migration file so you can inspect the SQL
# before applying it.  In a real workflow, you'd review this in
# code review before merging.
echo ""
echo "=== Generated Migration ==="
MIGRATION_FILE=$(ls migrations/primary/*.sql 2>/dev/null | head -1)
if [ -n "$MIGRATION_FILE" ]; then
    cat "$MIGRATION_FILE"
fi

# ── dbwarden new ───────────────────────────────────────────────
# Creates a blank migration file with --upgrade and --rollback
# sections that you fill in manually.  Useful for operations that
# aren't model-driven: data backfills, stored procedures, index
# maintenance, etc.  The file follows the same numbering scheme
# so it's tracked alongside auto-generated migrations.
echo ""
echo "=== Creating manual migration ==="
dbwarden new add_custom_table --database primary

echo ""
echo "=== Manual migration template ==="
MANUAL_FILE=$(ls migrations/primary/*.sql 2>/dev/null | tail -1)
if [ -n "$MANUAL_FILE" ]; then
    cat "$MANUAL_FILE"
fi

# ── dbwarden make-rollback ─────────────────────────────────────
# Extracts just the --rollback section from an existing migration
# file and prints it to stdout.  Useful for quickly verifying
# what `dbwarden rollback` will actually execute before you run it.
echo ""
echo "=== Generated rollback SQL ==="
FIRST_FILE=$(ls migrations/primary/*.sql 2>/dev/null | head -1)
if [ -n "$FIRST_FILE" ]; then
    dbwarden make-rollback "$FIRST_FILE"
fi
