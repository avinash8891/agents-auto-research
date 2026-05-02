#!/usr/bin/env bash
set -euo pipefail

base_ref="${1:-origin/main}"
if [[ $# -gt 0 ]]; then
  shift
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

# Prevent indefinite pre-push hangs if cubic blocks on network/service calls.
timeout_seconds="${CUBIC_REVIEW_TIMEOUT_SECONDS:-300}"
if ! perl -e 'alarm shift @ARGV; exec @ARGV' \
  "$timeout_seconds" \
  env PATH="$HOME/.cubic/bin:$PATH" cubic review --print-logs --base "$base_ref" "$@"
then
  status=$?
  if [[ "$status" -eq 142 ]]; then
    echo "cubic review timed out after ${timeout_seconds}s" >&2
  fi
  exit "$status"
fi
