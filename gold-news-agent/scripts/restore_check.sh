#!/usr/bin/env bash
# Prove the newest backup restores (C4.2): load it into a throw-away database inside the
# postgres container, count the rows of the main tables, drop it again. Run it weekly and after
# every change to the backup set-up; an untested backup is not a backup.
#   scripts/restore_check.sh [backup_dir]
set -euo pipefail
cd "$(dirname "$0")/.."
dir="${1:-backups}"
if [ ! -d "$dir" ]; then
  echo "backup directory not found: $dir (run scripts/backup.sh first, or pass the directory)" >&2
  exit 1
fi
latest="$(find "$dir" -maxdepth 1 -name 'gold-*.dump' -printf '%T@ %p\n' 2>/dev/null |
  sort -n | tail -1 | cut -d' ' -f2-)"
if [ -z "$latest" ]; then
  echo "no backup in $dir" >&2
  exit 1
fi
db="restore_check_$(date -u +%s)"
psql() { docker compose exec -T postgres psql -U gold -v ON_ERROR_STOP=1 -X -q "$@"; }
psql -d postgres -c "CREATE DATABASE $db"
trap 'psql -d postgres -c "DROP DATABASE IF EXISTS $db"' EXIT
docker compose exec -T postgres pg_restore -U gold -d "$db" --no-owner --exit-on-error <"$latest"
psql -d "$db" -At -F ' ' -c "
  SELECT 'alembic', version_num FROM alembic_version
  UNION ALL SELECT 'news_items', count(*)::text FROM news_items
  UNION ALL SELECT 'analyses', count(*)::text FROM analyses
  UNION ALL SELECT 'signals', count(*)::text FROM signals
  UNION ALL SELECT 'signal_outcomes', count(*)::text FROM signal_outcomes
  UNION ALL SELECT 'candles', count(*)::text FROM candles
  UNION ALL SELECT 'notifications', count(*)::text FROM notifications"
echo "restore ok: $latest"
