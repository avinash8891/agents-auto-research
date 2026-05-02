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
PATH="$HOME/.cubic/bin:$PATH" cubic review --base "$base_ref" "$@"
