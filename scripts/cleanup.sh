#!/bin/sh
set -eu

psql --set=ON_ERROR_STOP=1 <<'SQL'
DELETE FROM conversation_sessions
WHERE expires_at < CURRENT_TIMESTAMP - INTERVAL '7 days';

DELETE FROM webhook_events
WHERE status = 'PROCESSED'
  AND created_at < CURRENT_TIMESTAMP - INTERVAL '90 days';
SQL

echo "Expired conversations and webhook events cleaned"
