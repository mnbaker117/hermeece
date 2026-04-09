#!/bin/sh

AUTHOR_FILE="/config/ebook_authors.txt"
IGNORED_AUTHOR_FILE="/config/ignored_ebook_authors.txt"
WEEKLY_SKIPPED_AUTHOR_FILE="/config/weekly_skipped_authors.txt"
SKIP_LOG="/config/skipped_ebooks.csv"
DEBUG_LOG="/config/ebook_gate_debug.log"

CATEGORY=""
TORRENT_NAME=""
TITLE=""
DESCRIPTION=""
INFO_URL=""

while [ "$#" -gt 0 ]; do
  case "$1" in
    --category)
      CATEGORY="$2"
      shift 2
      ;;
    --torrent-name)
      TORRENT_NAME="$2"
      shift 2
      ;;
    --title)
      TITLE="$2"
      shift 2
      ;;
    --description)
      DESCRIPTION="$2"
      shift 2
      ;;
    --info-url)
      INFO_URL="$2"
      shift 2
      ;;
    *)
      shift
      ;;
  esac
done

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

is_allowed_category() {
  case "$(normalize "$1")" in
    "ebooks action/adventure"| \
    "ebooks science fiction"| \
    "ebooks fantasy"| \
    "ebooks urban fantasy"| \
    "ebooks general fiction"| \
    "ebooks mixed collections"| \
    "ebooks young adult")
      return 0
      ;;
    *)
      return 1
      ;;
  esac
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

extract_author_blob() {
  for text in "$TORRENT_NAME" "$TITLE" "$DESCRIPTION"; do
    [ -z "$text" ] && continue

    # MAM-style full announce:
    # "New Torrent: ... By: Author Names Category: ( Ebooks - Fantasy ) ..."
    result="$(printf '%s\n' "$text" | sed -n 's/.*[Bb][Yy]: *\(.*\) [Cc]ategory: (.*/\1/p')"
    if [ -n "$result" ]; then
      trim "$result"
      return 0
    fi

    # Generic title format:
    # "Book Title by Author1, Author2 [English / epub]"
    # or "Book Title by Author1, Author2"
    result="$(printf '%s\n' "$text" | sed -n 's/.* [Bb][Yy] \(.*\)$/\1/p')"
    if [ -n "$result" ]; then
      result="$(printf '%s' "$result" | sed 's/ *\[[^]]*\]$//')"
      trim "$result"
      return 0
    fi

    # Fallback:
    # "By: Author Names" without category section
    result="$(printf '%s\n' "$text" | sed -n 's/.*[Bb][Yy]: *\([^|[]*\).*/\1/p')"
    if [ -n "$result" ]; then
      trim "$result"
      return 0
    fi
  done

  return 1
}

split_authors_to_file() {
  blob="$1"
  outfile="$2"

  : > "$outfile"

  # Normalize common multi-author separators to |
  # Then split on | and commas.
  # This intentionally treats comma-separated names as separate authors,
  # which is what we want for entries like:
  # "J N Chaney, Jason Anspach"
  printf '%s\n' "$blob" \
    | sed 's/ and /|/Ig; s/ \& /|/g; s# / #|#g; s/; */|/g; s/, */|/g' \
    | awk -F'|' '{ for (i = 1; i <= NF; i++) print $i }' \
    | while IFS= read -r author; do
        author="$(trim "$author")"
        [ -n "$author" ] && printf '%s\n' "$author"
      done >> "$outfile"
}

append_skip_log() {
  now="$(date -u +"%Y-%m-%dT%H:%M:%SZ")"

  if [ ! -f "$SKIP_LOG" ]; then
    echo "timestamp_utc,category,title,author,info_url,torrent_name,reason" > "$SKIP_LOG"
  fi

  safe_category=$(printf '%s' "$CATEGORY" | sed 's/"/""/g')
  safe_title=$(printf '%s' "$TITLE" | sed 's/"/""/g')
  safe_author=$(printf '%s' "$1" | sed 's/"/""/g')
  safe_info_url=$(printf '%s' "$INFO_URL" | sed 's/"/""/g')
  safe_torrent_name=$(printf '%s' "$TORRENT_NAME" | sed 's/"/""/g')
  safe_reason=$(printf '%s' "$2" | sed 's/"/""/g')

  if ! echo "\"$now\",\"$safe_category\",\"$safe_title\",\"$safe_author\",\"$safe_info_url\",\"$safe_torrent_name\",\"$safe_reason\"" >> "$SKIP_LOG"; then
    debug_log "result=ERROR | reason=skip_log_write_failed | file=$SKIP_LOG"
  fi
}

append_weekly_skipped_author() {
  author="$1"
  [ -n "$author" ] || return 0

  touch "$WEEKLY_SKIPPED_AUTHOR_FILE"

  if ! name_in_file "$author" "$WEEKLY_SKIPPED_AUTHOR_FILE"; then
    printf '%s\n' "$author" >> "$WEEKLY_SKIPPED_AUTHOR_FILE"
  fi
}

debug_log() {
  now="$(date -u +"%Y-%m-%dT%H:%M:%SZ")"
  echo "$now | $1" >> "$DEBUG_LOG"
}

[ -n "$TITLE" ] || TITLE="$TORRENT_NAME"

debug_log "called | category=$CATEGORY | title=$TITLE | torrent=$TORRENT_NAME | info=$INFO_URL"

if ! is_allowed_category "$CATEGORY"; then
  debug_log "result=SKIP | reason=category_not_allowed | category=$CATEGORY"
  exit 1
fi

AUTHOR_BLOB="$(extract_author_blob)"

if [ -z "$AUTHOR_BLOB" ]; then
  append_skip_log "" "author_not_detected"
  debug_log "result=SKIP | reason=author_not_detected"
  exit 1
fi

TMP_AUTHORS="/tmp/ebook_gate_authors.$$"
split_authors_to_file "$AUTHOR_BLOB" "$TMP_AUTHORS"

MATCHED_ALLOWED_AUTHOR=""
HAS_IGNORED_AUTHOR=0
FIRST_IGNORED_AUTHOR=""
PRIMARY_LOG_AUTHOR=""
HAS_UNKNOWN_AUTHOR=0

while IFS= read -r author; do
  [ -z "$author" ] && continue

  # If any author is allowed, allow the whole book immediately.
  if name_in_file "$author" "$AUTHOR_FILE"; then
    MATCHED_ALLOWED_AUTHOR="$author"
    break
  fi

  # Track ignored authors, but do not stop yet in case another co-author is allowed.
  if name_in_file "$author" "$IGNORED_AUTHOR_FILE"; then
    HAS_IGNORED_AUTHOR=1
    [ -z "$FIRST_IGNORED_AUTHOR" ] && FIRST_IGNORED_AUTHOR="$author"
    continue
  fi

  # Unknown author: add all of them to the weekly skipped author list.
  HAS_UNKNOWN_AUTHOR=1
  append_weekly_skipped_author "$author"

  # Use the first unknown author as the main author shown in skipped_ebooks.csv
  [ -z "$PRIMARY_LOG_AUTHOR" ] && PRIMARY_LOG_AUTHOR="$author"
done < "$TMP_AUTHORS"

rm -f "$TMP_AUTHORS"

if [ -n "$MATCHED_ALLOWED_AUTHOR" ]; then
  debug_log "result=ALLOW | matched_author=$MATCHED_ALLOWED_AUTHOR | authors=$AUTHOR_BLOB"
  exit 0
fi

# If none were allowed, but at least one was unknown, keep this in daily review.
if [ "$HAS_UNKNOWN_AUTHOR" -eq 1 ]; then
  append_skip_log "$PRIMARY_LOG_AUTHOR" "author_not_allowlisted"
  debug_log "result=SKIP | reason=author_not_allowlisted | authors=$AUTHOR_BLOB | primary_author=$PRIMARY_LOG_AUTHOR"
  exit 1
fi

# If none were allowed and all remaining authors were ignored, mark as ignored.
if [ "$HAS_IGNORED_AUTHOR" -eq 1 ]; then
  append_skip_log "$FIRST_IGNORED_AUTHOR" "ignored_author"
  debug_log "result=SKIP | reason=ignored_author | authors=$AUTHOR_BLOB | primary_author=$FIRST_IGNORED_AUTHOR"
  exit 1
fi

# Fallback: should be rare, but keep a record.
append_skip_log "$AUTHOR_BLOB" "author_not_allowlisted_fallback"
append_weekly_skipped_author "$AUTHOR_BLOB"
debug_log "result=SKIP | reason=author_not_allowlisted_fallback | authors=$AUTHOR_BLOB"
exit 1
