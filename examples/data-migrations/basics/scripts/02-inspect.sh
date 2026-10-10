#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

DBWARDEN="${DBWARDEN:-dbwarden}"

echo "=== 02: Inspect the data declarations ==="

# `data render` prints the compiled data specification. Values are
# redacted by default; pass --show-managed-values to reveal literals.
"$DBWARDEN" data render --format operations

# `data describe` summarises models, constraints, and the declared data
# and transition sections in a human-readable form.
"$DBWARDEN" data describe --format markdown

# `diff --data` compares declared data against the live database.
"$DBWARDEN" diff --data
