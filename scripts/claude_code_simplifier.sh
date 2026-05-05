#!/usr/bin/env bash
set -uo pipefail

base_ref="${1:-origin/main}"
if [[ $# -gt 0 ]]; then
  shift
fi

timeout_seconds="${CLAUDE_CODE_SIMPLIFIER_TIMEOUT_SECONDS:-3600}"
if [[ ! "$timeout_seconds" =~ ^[1-9][0-9]*$ ]]; then
  echo "invalid CLAUDE_CODE_SIMPLIFIER_TIMEOUT_SECONDS=$timeout_seconds; skipping code simplifier" >&2
  exit 0
fi

if ! repo_root="$(git rev-parse --show-toplevel)"; then
  echo "could not resolve repo root; skipping code simplifier" >&2
  exit 0
fi

if ! review_parent="$(mktemp -d "${TMPDIR:-/tmp}/autoresearch-claude-simplifier.XXXXXX")"; then
  echo "could not create temp directory; skipping code simplifier" >&2
  exit 0
fi
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

timeout_cmd=""
if command -v timeout >/dev/null 2>&1; then
  timeout_cmd="$(command -v timeout)"
elif command -v gtimeout >/dev/null 2>&1; then
  timeout_cmd="$(command -v gtimeout)"
else
  echo "timeout command unavailable; skipping code simplifier" >&2
  exit 0
fi

diff_stat="$(git diff --stat "$base_ref" 2>/dev/null || true)"
diff_text="$(git diff "$base_ref" 2>/dev/null || true)"
prompt="You are the code-simplifier agent. Review and propose simplifications for the current branch compared to ${base_ref}. Do not modify files or run commands. Output suggestions only.

DIFF STAT:
${diff_stat}

DIFF:
${diff_text}"

if ! printf '%s\n' "$prompt" | "$timeout_cmd" \
  "$timeout_seconds" \
  claude -p --bare --output-format text --permission-mode dontAsk --tools "Read,Glob,Grep" --agent code-simplifier; then
  echo "claude code simplifier failed; skipping non-blocking review" >&2
fi
