#!/usr/bin/env bash
set -euo pipefail

echo "=== 01: Project Setup ==="

# ── Clean slate ────────────────────────────────────────────────
# These commands remove any state from a previous run so the
# example starts fresh.  `migrations/` holds generated SQL files
# and `.dbwarden/` stores schema snapshots and cached model state.
rm -rf migrations .dbwarden

# ── dbwarden init ──────────────────────────────────────────────
# This reads dbwarden.py, validates the configuration, and creates
# the migration directory structure:
#   migrations/{database_name}/
# It also writes a default dbwarden.py if none exists.
# Behind the scenes, the CLI:
#   1. Sandbox-loads dbwarden.py to register the database targets
#   2. Creates per-database directories under migrations/
#   3. Optionally writes a fresh dbwarden.py template
dbwarden init

# ── dbwarden config ────────────────────────────────────────────
# Displays the loaded configuration in human-readable form:
# which databases are registered, their types, URLs, and model
# paths.  Useful for verifying dbwarden.py was parsed correctly.
dbwarden config

echo ""
echo "Setup complete. Files created:"
ls -la dbwarden.py migrations/
