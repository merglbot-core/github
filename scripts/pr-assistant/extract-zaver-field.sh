#!/usr/bin/env bash
# Extract a single machine-readable field from the final Zaver/Závěr section.
set -euo pipefail

if [ "$#" -ne 2 ]; then
  echo "Usage: $0 <field-name> <review-file>" >&2
  exit 2
fi

FIELD_NAME="$1"
REVIEW_FILE="$2"

awk -v field_name="$FIELD_NAME" '
  BEGIN { in_zaver = 0 }
  /^##+[[:space:]]+/ {
    header = $0
    gsub(/^[#[:space:]]+/, "", header)
    gsub(/[*_`[:space:]]/, "", header)
    if (tolower(header) == "zaver" || tolower(header) == "závěr") {
      in_zaver = 1
      next
    }
    if (in_zaver) {
      exit
    }
    next
  }
  in_zaver {
    line = $0
    gsub(/^[[:space:]>*+-]*/, "", line)
    n = split(line, parts, ":")
    field_label = parts[1]
    gsub(/[*_`]/, "", field_label)
    gsub(/^[[:space:]]+|[[:space:]]+$/, "", field_label)
    if (n >= 2 && tolower(field_label) == tolower(field_name)) {
      # Preserve any additional colons in the value; only remove the field label.
      sub(/^[^:]*:[[:space:]]*/, "", line)
      print field_name ": " line
      exit
    }
  }
' "$REVIEW_FILE"
