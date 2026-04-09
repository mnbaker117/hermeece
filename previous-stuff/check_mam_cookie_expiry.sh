#!/bin/bash

set -u

CONTAINER_NAME="autobrr"
TOPIC_URL="https://ntfy.sh/PUT-YOUR-SECRET-TOPIC-HERE"
STATE_FILE="/mnt/user/appdata/autobrr/mam_cookie_alert_lastcheck.txt"

log() {
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*"
}

DEFAULT_SINCE="48h"

if [ -f "$STATE_FILE" ]; then
  SINCE="$(cat "$STATE_FILE" 2>/dev/null || true)"
else
  SINCE="$DEFAULT_SINCE"
fi

NOW_UTC="$(date -u +"%Y-%m-%dT%H:%M:%SZ")"

LOG_OUTPUT="$(docker logs --since "$SINCE" "$CONTAINER_NAME" 2>&1 || true)"

if [ -z "$LOG_OUTPUT" ]; then
  log "No logs returned from container '$CONTAINER_NAME' since '$SINCE'."
  printf '%s\n' "$NOW_UTC" > "$STATE_FILE"
  exit 0
fi

MATCHES="$(printf '%s\n' "$LOG_OUTPUT" \
  | grep -F 'check indexer keys for MyAnonamouse' \
  | grep -F 'status code: 401' || true)"

if [ -z "$MATCHES" ]; then
  log "No new MAM 401/session issues found since '$SINCE'."
  printf '%s\n' "$NOW_UTC" > "$STATE_FILE"
  exit 0
fi

MESSAGE="Autobrr MAM ID Warning: Session likely expired. Refresh the mam_id cookie in the MyAnonamouse indexer."

if curl -fsS \
  --connect-timeout 10 \
  --max-time 20 \
  -H "Title: Autobrr MAM Session Warning" \
  -H "Priority: high" \
  -H "Tags: warning,books" \
  -d "$MESSAGE" \
  "$TOPIC_URL" >/dev/null
then
  printf '%s\n' "$NOW_UTC" > "$STATE_FILE"
  log "ntfy alert sent. Checkpoint updated to $NOW_UTC."
else
  log "Failed to send ntfy alert. Checkpoint not updated."
  exit 1
fi

exit 0
