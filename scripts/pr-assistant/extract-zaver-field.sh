#!/usr/bin/env bash
# Extract a single machine-readable field from the final Zaver/Závěr section.
set -euo pipefail

if [ "$#" -eq 1 ] && [ "$1" = "--self-test" ]; then
  tmp_review="$(mktemp)"
  tmp_nested="$(mktemp)"
  trap 'rm -f "$tmp_review" "$tmp_nested"' EXIT
  cat >"$tmp_review" <<'EOF'
## Zaver
  ```text
  Verdict: approved_for_closeout
  Documentation Obligation State: not_required
  ```
Verdict: changes_required
Documentation Obligation State: not_required
EOF
  extracted="$("$0" "Verdict" "$tmp_review")"
  if [ "$extracted" != "Verdict: changes_required" ]; then
    echo "self-test failed: expected real Zaver field outside indented fence, got: $extracted" >&2
    exit 1
  fi
  cat >"$tmp_nested" <<'EOF'
### Zaver
Verdict: approved_for_closeout
## Zaver
Verdict: changes_required
### Details
Documentation Obligation State: missing
EOF
  nested_extracted="$("$0" "Verdict" "$tmp_nested")"
  if [ "$nested_extracted" != "Verdict: changes_required" ]; then
    echo "self-test failed: expected top-level Zaver field, got: $nested_extracted" >&2
    exit 1
  fi
  nested_docs="$("$0" "Documentation Obligation State" "$tmp_nested" || true)"
  if [ -n "$nested_docs" ]; then
    echo "self-test failed: nested subsection field was parsed: $nested_docs" >&2
    exit 1
  fi
  echo '{"ok":true,"self_test":"passed"}'
  exit 0
fi

if [ "$#" -ne 2 ]; then
  echo "Usage: $0 <field-name> <review-file>" >&2
  exit 2
fi

FIELD_NAME="$1"
REVIEW_FILE="$2"

awk -v field_name="$FIELD_NAME" '
  BEGIN { in_zaver = 0; in_code = 0 }
  /^[[:space:]]*```/ {
    if (in_zaver) {
      in_code = !in_code
    }
    next
  }
  in_zaver && in_code {
    next
  }
  /^##[[:space:]]+/ {
    header = $0
    gsub(/^[#[:space:]]+/, "", header)
    gsub(/[*_`[:space:]]/, "", header)
    if (!in_zaver && (tolower(header) == "zaver" || tolower(header) == "závěr")) {
      in_zaver = 1
      in_code = 0
      next
    }
    if (in_zaver) {
      exit
    }
    next
  }
  /^###+[[:space:]]+/ {
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
      gsub(/[*`]/, "", line)
      print field_name ": " line
      exit
    }
  }
' "$REVIEW_FILE"
