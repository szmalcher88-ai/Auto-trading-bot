#!/bin/sh
set -e

# Path relative to CWD (/app) — Prisma CLI resolves env() URLs from CWD
export DATABASE_URL="file:./autoresearch.db"

echo "[ENTRYPOINT] Initializing runtime data files..."

# Ensure runtime files exist so bind-mount paths are files, not directories
touch /app/trade_log.csv
touch /app/trading_bot.log
touch /app/autoresearch_results.csv
touch /app/autoresearch_alltime.csv

[ -f /app/autoresearch_meta.json ] || echo '{}' > /app/autoresearch_meta.json

echo "[ENTRYPOINT] Pushing Prisma schema to SQLite..."
python -m prisma db push --skip-generate

echo "[ENTRYPOINT] Starting trading bot..."
exec python main.py
