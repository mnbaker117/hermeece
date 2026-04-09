#!/bin/bash

set -u

CSV="/mnt/user/appdata/autobrr/skipped_ebooks.csv"
ARCHIVE_DIR="/mnt/user/appdata/autobrr/skipped_archive"
TOPIC_URL="https://ntfy.sh/turtles81-autobrr-books"
TODAY="$(date +%Y-%m-%d)"
NOW_UTC="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
MAX_LINES=50

mkdir -p "$ARCHIVE_DIR"

log() {
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*"
}

if [ ! -f "$CSV" ]; then
  log "No skipped_ebooks.csv found. Nothing to send."
  exit 0
fi

LINE_COUNT=$(wc -l < "$CSV")
if [ "$LINE_COUNT" -le 1 ]; then
  log "CSV has no new skipped entries. Nothing to send."
  exit 0
fi

ARCHIVE_FILE="$ARCHIVE_DIR/skipped_ebooks_$(date +%Y-%m-%d_%H-%M-%S).csv"

TMP_BODY="$(mktemp)"
TMP_DATA="$(mktemp)"
TMP_REVIEWABLE="$(mktemp)"
TMP_SORTED="$(mktemp)"
TMP_PARSED="$(mktemp)"

cleanup() {
  rm -f "$TMP_BODY" "$TMP_DATA" "$TMP_REVIEWABLE" "$TMP_SORTED" "$TMP_PARSED"
}
trap cleanup EXIT

# Archive full raw CSV first
cp "$CSV" "$ARCHIVE_FILE"

# Skip header row for processing
tail -n +2 "$CSV" > "$TMP_DATA"

# Count ignored-author rows for informational summary only
IGNORED_COUNT=$(awk -F'","' '
  {
    reason=$7
    gsub(/^"/,"",reason)
    gsub(/"$/,"",reason)
    if (reason == "ignored_author") count++
  }
  END { print count+0 }
' "$TMP_DATA")

# Keep only reviewable rows
awk -F'","' '
  {
    reason=$7
    gsub(/^"/,"",reason)
    gsub(/"$/,"",reason)
    if (reason == "author_not_allowlisted" || reason == "author_not_allowlisted_fallback") print $0
  }
' "$TMP_DATA" > "$TMP_REVIEWABLE"

FILTERED_COUNT=$(wc -l < "$TMP_REVIEWABLE")

if [ "$FILTERED_COUNT" -eq 0 ]; then
  if [ "$IGNORED_COUNT" -gt 0 ]; then
    {
      echo "Autobrr skipped ebooks summary"
      echo "Date: $TODAY"
      echo "Unique reviewable books: 0"
      echo "Authors to review: 0"
      echo "Ignored-author skips suppressed: $IGNORED_COUNT"
      echo
      echo "No new reviewable authors today."
      echo
      echo "Archive: $ARCHIVE_FILE"
      echo "Generated: $NOW_UTC"
    } > "$TMP_BODY"

    log "Sending ntfy notification (ignored-author summary only)"
    if curl -fsS \
      --connect-timeout 10 \
      --max-time 20 \
      -H "Title: Autobrr Skipped Ebooks" \
      -H "Priority: default" \
      -H "Tags: books" \
      --data-binary @"$TMP_BODY" \
      "$TOPIC_URL" >/dev/null
    then
      log "ntfy notification sent successfully."
    else
      log "Failed to send ntfy notification. Leaving CSV untouched."
      exit 1
    fi
  else
    log "No reviewable skipped entries today. Archived CSV without notification."
  fi

  head -n 1 "$CSV" > "$CSV.tmp" && mv "$CSV.tmp" "$CSV"
  chmod 666 "$CSV"
  log "Active CSV reset to header only and permissions refreshed."
  exit 0
fi

# Parse reviewable rows into a simpler tab-separated structure
while IFS= read -r line; do
  title=$(printf '%s\n' "$line" | awk -F'","' '{gsub(/^"/,"",$3); gsub(/"$/,"",$3); print $3}')
  author=$(printf '%s\n' "$line" | awk -F'","' '{gsub(/^"/,"",$4); gsub(/"$/,"",$4); print $4}')
  info_url=$(printf '%s\n' "$line" | awk -F'","' '{gsub(/^"/,"",$5); gsub(/"$/,"",$5); print $5}')
  category=$(printf '%s\n' "$line" | awk -F'","' '{gsub(/^"/,"",$2); gsub(/"$/,"",$2); print $2}')
  reason=$(printf '%s\n' "$line" | awk -F'","' '{gsub(/^"/,"",$7); gsub(/"$/,"",$7); print $7}')

  [ -z "$title" ] && title="Unknown Title"
  [ -z "$author" ] && author="Unknown Author"
  [ -z "$category" ] && category="Unknown Category"
  [ -z "$reason" ] && reason="unknown"

  printf '%s\t%s\t%s\t%s\t%s\n' "$author" "$title" "$category" "$info_url" "$reason"
done < "$TMP_REVIEWABLE" > "$TMP_PARSED"

sort -u "$TMP_PARSED" | sort -t $'\t' -k1,1 -k2,2 > "$TMP_SORTED"

UNIQUE_TOTAL=$(wc -l < "$TMP_SORTED")
AUTHOR_TOTAL=$(cut -f1 "$TMP_SORTED" | sort -u | wc -l)

{
  echo "Autobrr skipped ebooks summary"
  echo "Date: $TODAY"
  echo "Unique reviewable books: $UNIQUE_TOTAL"
  echo "Authors to review: $AUTHOR_TOTAL"
  echo "Ignored-author skips suppressed: $IGNORED_COUNT"
  echo

  current_author=""
  printed_lines=0

  while IFS=$'\t' read -r author title category info_url reason; do
    printed_lines=$((printed_lines + 1))
    if [ "$printed_lines" -gt "$MAX_LINES" ]; then
      break
    fi

    if [ "$author" != "$current_author" ]; then
      [ -n "$current_author" ] && echo
      echo "$author"
      current_author="$author"
    fi

    echo " - $title"
    echo "   Category: $category"
    [ -n "$info_url" ] && echo "   Link: $info_url"
  done < "$TMP_SORTED"

  if [ "$UNIQUE_TOTAL" -gt "$MAX_LINES" ]; then
    echo
    echo "...and $((UNIQUE_TOTAL - MAX_LINES)) more unique entries."
  fi

  echo
  echo "Archive: $ARCHIVE_FILE"
  echo "Generated: $NOW_UTC"
} > "$TMP_BODY"

log "Sending ntfy notification"
if curl -fsS \
  --connect-timeout 10 \
  --max-time 20 \
  -H "Title: Autobrr Skipped Ebooks" \
  -H "Priority: default" \
  -H "Tags: books" \
  --data-binary @"$TMP_BODY" \
  "$TOPIC_URL" >/dev/null
then
  log "ntfy notification sent successfully."
  head -n 1 "$CSV" > "$CSV.tmp" && mv "$CSV.tmp" "$CSV"
  chmod 666 "$CSV"
  log "Active CSV reset to header only and permissions refreshed."
else
  log "Failed to send ntfy notification. Leaving CSV untouched."
  exit 1
fi

exit 0
