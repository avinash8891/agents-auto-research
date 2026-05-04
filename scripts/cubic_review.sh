#!/usr/bin/env bash
set -euo pipefail

base_ref="${1:-origin/main}"
if [[ $# -gt 0 ]]; then
  shift
fi

# Prevent indefinite pre-push hangs if cubic blocks on network/service calls.
# Default to 60 minutes so large diffs have enough time to complete review
# without requiring a manual override.
timeout_seconds="${CUBIC_REVIEW_TIMEOUT_SECONDS:-3600}"
if [[ ! "$timeout_seconds" =~ ^[1-9][0-9]*$ ]]; then
  echo "CUBIC_REVIEW_TIMEOUT_SECONDS must be a positive integer: $timeout_seconds" >&2
  exit 2
fi

repo_root="$(git rev-parse --show-toplevel)"

# Warn if the cubic CLI token is close to expiry (< 30 min). The token
# cannot be refreshed non-interactively; the developer should run
# `cubic auth login` before pushing if this message appears.
CUBIC_EXPIRY=$(python3 -c "
import json, time, base64
try:
    tok = json.load(open('$HOME/.local/share/cubic/auth.json'))['cli']['accessToken']
    padded = tok.split('.')[1] + '=='
    exp = json.loads(base64.b64decode(padded.encode()).decode()).get('exp', 0)
    print(int(exp - time.time()))
except Exception:
    print(9999)
" 2>/dev/null) || CUBIC_EXPIRY=9999
if [[ "$CUBIC_EXPIRY" -lt 1800 ]]; then
  echo "WARNING: cubic auth token expires in ${CUBIC_EXPIRY}s. Run 'cubic auth login' to refresh." >&2
fi

review_dir="$(mktemp -d "${TMPDIR:-/tmp}/autoresearch-cubic-review.XXXXXX")"

cleanup() {
  git -C "$repo_root" worktree remove --force "$review_dir" >/dev/null 2>&1 || true
  rm -rf "$review_dir"
}
trap cleanup EXIT

git -C "$repo_root" worktree add --detach "$review_dir" HEAD >/dev/null

cd "$review_dir"

if perl -e 'alarm shift @ARGV; exec @ARGV' \
  "$timeout_seconds" \
  env PATH="$HOME/.superset/bin:$HOME/.cubic/bin:$PATH" cubic review --print-logs --base "$base_ref" "$@"
then
  :
else
  status=$?
  if [[ "$status" -eq 142 ]]; then
    echo "cubic review timed out after ${timeout_seconds}s" >&2
  fi
  exit "$status"
fi
