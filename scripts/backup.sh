#!/bin/sh
set -eu

timestamp="$(date +%Y%m%d_%H%M%S)"
target="/backups/linebot_${timestamp}.dump"

pg_dump --format=custom --compress=9 --file="${target}"
pg_restore --list "${target}" >/dev/null
find /backups -type f -name 'linebot_*.dump' -mtime +30 -delete

echo "Backup completed: ${target}"
