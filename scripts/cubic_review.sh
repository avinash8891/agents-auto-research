#!/usr/bin/env bash
set -euo pipefail

base_ref="${1:-origin/main}"
if [[ $# -gt 0 ]]; then
  shift
fi

# Prevent indefinite pre-push hangs if cubic blocks on network/service calls.
# Default to 20 minutes so normal large diffs do not fail pre-push simply due
# to a short watchdog window.
timeout_seconds="${CUBIC_REVIEW_TIMEOUT_SECONDS:-1200}"
if [[ ! "$timeout_seconds" =~ ^[1-9][0-9]*$ ]]; then
  echo "CUBIC_REVIEW_TIMEOUT_SECONDS must be a positive integer: $timeout_seconds" >&2
  exit 2
fi

repo_root="$(git rev-parse --show-toplevel)"
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
  env PATH="$HOME/.cubic/bin:$PATH" cubic review --print-logs --base "$base_ref" "$@"
then
  :
else
  status=$?
  if [[ "$status" -eq 142 ]]; then
    echo "cubic review timed out after ${timeout_seconds}s" >&2
  fi
  exit "$status"
fi
