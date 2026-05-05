#!/usr/bin/env bash
set -euo pipefail

base_ref="${1:-origin/main}"
if [[ $# -gt 0 ]]; then
  shift
fi

timeout_seconds="${CLAUDE_CODE_SIMPLIFIER_TIMEOUT_SECONDS:-3600}"
if [[ ! "$timeout_seconds" =~ ^[1-9][0-9]*$ ]]; then
  echo "CLAUDE_CODE_SIMPLIFIER_TIMEOUT_SECONDS must be a positive integer: $timeout_seconds" >&2
  exit 2
fi

repo_root="$(git rev-parse --show-toplevel)"
review_parent="$(mktemp -d "${TMPDIR:-/tmp}/autoresearch-claude-simplifier.XXXXXX")"
review_dir="${review_parent}/worktree"

cleanup() {
  git -C "$repo_root" worktree remove --force "$review_dir" >/dev/null 2>&1 || true
  rm -rf "$review_parent"
}
trap cleanup EXIT

if ! git -C "$repo_root" worktree add --detach "$review_dir" HEAD >/dev/null; then
  echo "could not create Claude simplifier worktree; skipping" >&2
  exit 0
fi
cd "$review_dir"

if ! command -v claude >/dev/null 2>&1; then
  echo "claude CLI not found on PATH; skipping code simplifier" >&2
  exit 0
fi

if ! claude auth status >/dev/null 2>&1; then
  echo "claude auth unavailable; skipping code simplifier" >&2
  exit 0
fi

prompt="You are the code-simplifier agent. Review and propose simplifications for the current branch compared to ${base_ref}. Do not modify files; output suggestions only."

if ! printf '%s\n' "$prompt" | perl -e 'alarm shift @ARGV; exec @ARGV' \
  "$timeout_seconds" \
  claude -p --bare --output-format text --permission-mode dontAsk --tools "Bash,Read,Glob,Grep" --agent code-simplifier; then
  echo "claude code simplifier failed; skipping non-blocking review" >&2
fi
