#!/usr/bin/env bash
set -euo pipefail

echo "=== Observability Example ==="
echo "This requires Docker for PostgreSQL, Prometheus, and Grafana."
echo ""

# ── Start services ────────────────────────────────────────────
# docker-compose.yml defines three services:
#   postgres    : PostgreSQL 16 on port 5432
#   prometheus  : Prometheus on port 9090, configured to scrape
#                 the FastAPI app's /metrics endpoint
#   grafana     : Grafana on port 3000, with Prometheus as the
#                 default data source (anonymous access enabled)
echo "Starting PostgreSQL, Prometheus, and Grafana..."
docker compose up -d
echo "Waiting for services to be ready..."
sleep 5

# ── Initialize and migrate ────────────────────────────────────
echo ""
echo "--- Initializing ---"
dbwarden init 2>&1

echo ""
echo "--- Generating migration ---"
dbwarden make-migrations "create users table" --database primary 2>&1

echo ""
echo "--- Applying ---"
dbwarden migrate --database primary 2>&1

echo ""
echo "=== Services ==="
echo "  App:      http://localhost:8000"
echo "  Metrics:  http://localhost:8000/metrics"
echo "  Health:   http://localhost:8000/health/"
echo "  Prometheus: http://localhost:9090"
echo "  Grafana:  http://localhost:3000"
echo ""
echo "Start the FastAPI app: uvicorn app.main:app --reload"
echo "Stop services: docker compose down"
