#!/usr/bin/env bash
# Host side of the watchdog's clock check (C4.1). Containers have no chrony, so this writes the
# host's offset in seconds (`chronyc -c tracking`, field 5) to a file the watchdog container
# mounts read-only. Run every minute by deploy/systemd/gold-clock-offset.timer; the watchdog
# treats a file older than 10 minutes as "offset unknown" and alerts.
#   scripts/clock_offset.sh [target]               (default /run/gold/clock_offset)
set -euo pipefail
target="${1:-/run/gold/clock_offset}"
mkdir -p "$(dirname "$target")"
offset="$(chronyc -c tracking | cut -d, -f5)"
printf '%s\n' "$offset" >"$target.tmp"
mv "$target.tmp" "$target"
