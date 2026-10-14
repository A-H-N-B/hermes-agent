#!/usr/bin/env bash
# Nightly PostgreSQL backup with rotation (C4.2), run by deploy/systemd/gold-backup.timer.
#   scripts/backup.sh [backup_dir] [keep_days]     (defaults: backups/ and 14 days)
# A custom-format dump of the whole database; prove it restores with scripts/restore_check.sh.
set -euo pipefail
cd "$(dirname "$0")/.."
dir="${1:-backups}"
keep="${2:-14}"
mkdir -p "$dir"
stamp="$(date -u +%Y%m%dT%H%M%SZ)"
partial="$dir/gold-$stamp.dump.partial"
trap 'rm -f "$partial"' EXIT
docker compose exec -T postgres pg_dump -U gold -d gold --format=custom --compress=6 >"$partial"
mv "$partial" "$dir/gold-$stamp.dump"
find "$dir" -maxdepth 1 -name 'gold-*.dump' -mtime +"$keep" -delete
echo "backup: $dir/gold-$stamp.dump ($(du -h "$dir/gold-$stamp.dump" | cut -f1))"
