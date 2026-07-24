#!/usr/bin/env bash
set -euo pipefail

echo "=== Multi-Database Example ==="
echo "This requires Docker for PostgreSQL and ClickHouse."
echo ""

# ── Start database services ────────────────────────────────────
# docker-compose.yml defines two services:
#   postgres   : PostgreSQL 16 on port 5432
#   clickhouse : ClickHouse on ports 8123 (HTTP) and 9000 (native)
echo "Starting PostgreSQL and ClickHouse..."
docker compose up -d
echo "Waiting for databases to be ready..."
sleep 5

# ── Initialize ─────────────────────────────────────────────────
# Creates migration directories for each registered database:
#   migrations/primary/
#   migrations/analytics/
echo ""
echo "--- Initializing ---"
dbwarden init 2>&1

# ── Generate migrations ────────────────────────────────────────
# Each --database target produces backend-specific SQL.
# The PostgreSQL migration uses PG-specific DDL (identity columns,
# partial indexes) while the ClickHouse migration uses MergeTree
# engine DDL with projections and skip indexes.
echo ""
echo "--- Generating migrations ---"
dbwarden make-migrations "create user table" --database primary 2>&1
dbwarden make-migrations "create page view table" --database analytics 2>&1

# ── Status (all) ───────────────────────────────────────────────
# Shows migration state for every registered database at once.
echo ""
echo "--- Status (all) ---"
dbwarden status --all 2>&1

# ── Apply ──────────────────────────────────────────────────────
# --all applies pending migrations to every database in order.
echo ""
echo "--- Applying migrations ---"
dbwarden migrate --all 2>&1

echo ""
echo "=== Done ==="
echo "Databases are running. Stop them with: docker compose down"
