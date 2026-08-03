#!/bin/bash
# Run one crawl cycle per site (separate Python process each time).
# This keeps memory use low on small servers and avoids OOM kills.
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"
source .venv/bin/activate
mkdir -p logs

LOCK="/tmp/port_news_crawler.lock"
exec 9>"$LOCK"
if ! flock -n 9; then
  echo "$(date -Is) crawl_once already running, skip" >> logs/cron.log
  exit 0
fi

SITES=(
  hket
  manifold_times
  hk_marine_dept
  scmp
  cnn
  reuters
  afp
  people_daily
  takungpao
)

log() { echo "$(date -Is) $*" >> logs/cron.log; }

log "=== crawl_once start ==="
for site in "${SITES[@]}"; do
  log "--- site: $site ---"
  if python run.py --once --site "$site" >> logs/cron.log 2>&1; then
    log "site $site OK"
  else
    log "site $site FAILED (exit $?)"
  fi
  pkill -f chromedriver 2>/dev/null || true
  pkill -f "chrome.*--headless" 2>/dev/null || true
  sleep 5
done
log "=== crawl_once done ==="
