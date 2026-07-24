#!/usr/bin/env bash
set -euo pipefail

echo "=== 07: Seeds ==="

# ── Seeds overview ─────────────────────────────────────────────
# Seeds are versioned, idempotent data insertions tracked in a
# _dbwarden_seeds table alongside migrations.  Unlike migrations,
# which manage schema, seeds manage reference data (countries,
# roles, admin accounts) that must exist in every environment.
#
# Seeds can be:
#   - SQL files (seeds/V*.sql): plain INSERT statements
#   - Python files: run arbitrary logic
#   - @seed_data decorators: inline in model files

# Ensure migrations are applied (tables must exist before seeding)
mkdir -p seeds
dbwarden migrate --database primary 2>/dev/null || true

# ── dbwarden seed create ───────────────────────────────────────
# Creates a blank seed file in the seeds/ directory with a
# sequential version prefix (V0001__, V0002__, ...).  The seed
# file has the same --upgrade / --rollback structure as a migration.
echo "--- Creating SQL seed ---"
dbwarden seed create "initial admin users" --database primary

# Populate the seed file with SQL content
SEED_FILE=$(ls seeds/V*.sql 2>/dev/null | head -1)
if [ -n "$SEED_FILE" ]; then
    cat > "$SEED_FILE" << 'SEEDEOF'
-- Seed: initial admin users
-- Applied once and tracked in _dbwarden_seeds

INSERT INTO users (email, username, full_name, is_active, created_at)
VALUES ('admin@example.com', 'admin', 'Admin User', 1, CURRENT_TIMESTAMP);

INSERT INTO users (email, username, full_name, is_active, created_at)
VALUES ('moderator@example.com', 'moderator', 'Moderator User', 1, CURRENT_TIMESTAMP);
SEEDEOF
    echo "Seed file written: $SEED_FILE"
fi

# ── dbwarden seed apply ────────────────────────────────────────
# Applies all pending seeds (those not yet recorded in
# _dbwarden_seeds).  Each seed is executed in a transaction
# and its checksum is recorded.  Re-running applies only new
# or changed seeds; already-applied seeds are skipped.
echo ""
echo "--- Applying seeds ---"
dbwarden seed apply --database primary

# ── dbwarden seed list ─────────────────────────────────────────
# Lists all applied seeds with their version, description,
# filename, and applied_at timestamp.  Useful for verifying
# which seed data is present in a given environment.
echo ""
echo "--- Applied seeds ---"
dbwarden seed list --database primary

# Create a second seed to demonstrate version tracking
echo ""
echo "--- Creating second seed ---"
dbwarden seed create "demo products" --database primary

SEED_FILE2=$(ls seeds/V*.sql 2>/dev/null | tail -1)
if [ -n "$SEED_FILE2" ] && [ "$SEED_FILE2" != "$SEED_FILE" ]; then
    cat > "$SEED_FILE2" << 'SEEDEOF'
-- Seed: demo products

INSERT INTO products (name, price, description, in_stock, created_at)
VALUES ('Widget', 9.99, 'A standard widget', 1, CURRENT_TIMESTAMP);

INSERT INTO products (name, price, description, in_stock, created_at)
VALUES ('Gadget', 24.99, 'A fancy gadget', 1, CURRENT_TIMESTAMP);

INSERT INTO products (name, price, description, in_stock, created_at)
VALUES ('Doohickey', 4.99, 'A small doohickey', 1, CURRENT_TIMESTAMP);
SEEDEOF
    echo "Seed file written: $SEED_FILE2"
fi

# Apply the second seed
dbwarden seed apply --database primary

# List all seeds
echo ""
echo "--- All seeds after applying second ---"
dbwarden seed list --database primary

# ── dbwarden seed rollback ─────────────────────────────────────
# Reverts the last N applied seeds by executing their --rollback
# sections.  Seeds are tracked with checksums, so re-applying a
# rolled-back seed re-runs it fresh (the checksum changed after
# rollback).
echo ""
echo "--- Rolling back last seed ---"
dbwarden seed rollback --database primary --count 1

# List after rollback
echo ""
echo "--- Seeds after rollback ---"
dbwarden seed list --database primary
