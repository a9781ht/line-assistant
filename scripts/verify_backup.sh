#!/bin/sh
set -eu

latest="$(find /backups -maxdepth 1 -type f -name 'linebot_*.dump' | sort | tail -n 1)"
if [ -z "${latest}" ]; then
    echo "No backup file found" >&2
    exit 1
fi

restore_database="linebot_restore_check"
dropdb --if-exists "${restore_database}"
createdb "${restore_database}"
pg_restore --no-owner --dbname="${restore_database}" "${latest}"
table_count="$(psql --dbname="${restore_database}" -tAc \
    "SELECT count(*) FROM information_schema.tables WHERE table_schema = 'public';")"
dropdb "${restore_database}"

if [ "${table_count}" -lt 11 ]; then
    echo "Restore verification failed: only ${table_count} public tables" >&2
    exit 1
fi

echo "Restore verification completed: ${table_count} public tables"
