#!/bin/bash
# Daily scrape runner — invoked by cron at 07:00 every day.
# Always uses --force so per-source schedules are ignored:
# this run scrapes every configured source, every day.

set -euo pipefail

PROJECT_DIR="/Users/keremyilmaz/Warren Workflow"
LOG_DIR="$PROJECT_DIR/logs"
LOG_FILE="$LOG_DIR/scrape-$(date +%Y-%m-%d).log"

mkdir -p "$LOG_DIR"

cd "$PROJECT_DIR"
source venv/bin/activate

{
  echo "=========================================="
  echo "Daily scrape started: $(date)"
  echo "=========================================="
  python3 main.py scrape --force
  echo ""
  echo "Daily scrape finished: $(date)"
} >> "$LOG_FILE" 2>&1
