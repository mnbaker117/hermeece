#!/bin/bash

set -u

ALLOW_FILE="/mnt/user/appdata/autobrr/ebook_authors.txt"
IGNORE_FILE="/mnt/user/appdata/autobrr/ignored_ebook_authors.txt"
WEEKLY_FILE="/mnt/user/appdata/autobrr/weekly_skipped_authors.txt"
ARCHIVE_DIR="/mnt/user/appdata/autobrr/weekly_skipped_author_archive"

# Optional ntfy summary
SEND_NTFY=1
TOPIC_URL="https://ntfy.sh/turtles81-autobrr-books"

mkdir -p "$ARCHIVE_DIR"

log() {
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*"
}

trim() {
  printf '%s' "$1" | sed 's/^ *//; s/ *$//'
}

normalize() {
  printf '%s' "$1" \
    | tr '[:upper:]' '[:lower:]' \
    | sed 's/[._-]/ /g' \
    | sed "s/[^[:alnum:] ',&]//g" \
    | sed 's/  */ /g; s/^ *//; s/ *$//'
}

name_in_file() {
  target_norm="$(normalize "$1")"
  file_path="$2"

  [ -f "$file_path" ] || return 1

  while IFS= read -r line; do
    case "$line" in
      ""|\#*) continue ;;
    esac

    line_norm="$(normalize "$line")"
    [ "$target_norm" = "$line_norm" ] && return 0
  done < "$file_path"

  return 1
}

touch "$ALLOW_FILE" "$IGNORE_FILE" "$WEEKLY_FILE"
chmod 666 "$IGNORE_FILE" "$WEEKLY_FILE" 2>/dev/null || true

if [ ! -s "$WEEKLY_FILE" ]; then
  log "No weekly skipped authors to process."
  exit 0
fi

ARCHIVE_FILE="$ARCHIVE_DIR/weekly_skipped_authors_$(date +%Y-%m-%d_%H-%M-%S).txt"
cp "$WEEKLY_FILE" "$ARCHIVE_FILE"

TMP_SORTED="$(mktemp)"
TMP_NEW_IGNORE="$(mktemp)"
TMP_BODY="$(mktemp)"

cleanup() {
  rm -f "$TMP_SORTED" "$TMP_NEW_IGNORE" "$TMP_BODY"
}
trap cleanup EXIT

# Normalize weekly file by trimming blanks and sorting unique
awk 'NF {print}' "$WEEKLY_FILE" | sort -u > "$TMP_SORTED"

PROMOTED=0
ALREADY_ALLOWED=0
ALREADY_IGNORED=0

while IFS= read -r author; do
  author="$(trim "$author")"
  [ -z "$author" ] && continue

  if name_in_file "$author" "$ALLOW_FILE"; then
    ALREADY_ALLOWED=$((ALREADY_ALLOWED + 1))
    continue
  fi

  if name_in_file "$author" "$IGNORE_FILE"; then
    ALREADY_IGNORED=$((ALREADY_IGNORED + 1))
    continue
  fi

  printf '%s\n' "$author" >> "$TMP_NEW_IGNORE"
  PROMOTED=$((PROMOTED + 1))
done < "$TMP_SORTED"

if [ -s "$TMP_NEW_IGNORE" ]; then
  cat "$TMP_NEW_IGNORE" >> "$IGNORE_FILE"
  sort -u "$IGNORE_FILE" -o "$IGNORE_FILE"
fi

: > "$WEEKLY_FILE"
chmod 666 "$IGNORE_FILE" "$WEEKLY_FILE" 2>/dev/null || true

log "Weekly author review complete."
log "Already allowed: $ALREADY_ALLOWED"
log "Already ignored: $ALREADY_IGNORED"
log "Moved to ignore list: $PROMOTED"
log "Archived weekly list to: $ARCHIVE_FILE"

if [ "$SEND_NTFY" -eq 1 ]; then
  {
    echo "Autobrr weekly skipped-author review"
    echo
    echo "Moved to ignore list: $PROMOTED"
    echo "Already allowed: $ALREADY_ALLOWED"
    echo "Already ignored: $ALREADY_IGNORED"
    echo
    echo "Archive: $ARCHIVE_FILE"

    if [ -s "$TMP_NEW_IGNORE" ]; then
      echo
      echo "Newly ignored authors:"
      sed 's/^/- /' "$TMP_NEW_IGNORE"
    fi
  } > "$TMP_BODY"

  curl -fsS \
    --connect-timeout 10 \
    --max-time 20 \
    -H "Title: Autobrr Weekly Author Review" \
    -H "Priority: default" \
    -H "Tags: books" \
    --data-binary @"$TMP_BODY" \
    "$TOPIC_URL" >/dev/null || log "Failed to send weekly ntfy summary."
fi

exit 0
