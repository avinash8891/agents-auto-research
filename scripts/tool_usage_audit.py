#!/usr/bin/env python3
"""Audit conductor tool usage from the trace-events.jsonl stream emitted by trace_sdk.

Every research tool emits an `agent`/`tool_call` event (with the call args = the
agent's *semantic intent*) and an `agent`/`tool_result` event (status + duration).
This aggregates them so we can decide — on real data — which tools earn their keep
and which are theatre.

Usage:
    python scripts/tool_usage_audit.py                  # mechanical: calls/ok/error per tool
    python scripts/tool_usage_audit.py --by job         # group by trace job id
    python scripts/tool_usage_audit.py --by run         # group by run_id
    python scripts/tool_usage_audit.py --semantic       # + sample call args (intent) per tool
    python scripts/tool_usage_audit.py --tool web_search # raw calls for one tool (args + result preview)
    python scripts/tool_usage_audit.py --since 2026-06-19
    python scripts/tool_usage_audit.py --path /custom/trace-events.jsonl
    python scripts/tool_usage_audit.py --logs-dir logs/
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_LOGS_DIR = REPO_ROOT / "logs"


def _resolve_paths(path_arg: str | None, logs_dir_arg: str | None) -> list[Path]:
    if path_arg:
        return [Path(path_arg).expanduser()]
    base = Path(logs_dir_arg).expanduser() if logs_dir_arg else DEFAULT_LOGS_DIR
    if not base.exists():
        return []
    return sorted(base.rglob("trace-events.jsonl"))


def _iter_events(paths: list[Path], since: str | None):
    for path in paths:
        try:
            text = path.read_text()
        except OSError:
            continue
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if rec.get("category") != "agent":
                continue
            if rec.get("action") not in ("tool_call", "tool_result"):
                continue
            if since and rec.get("timestamp", "") < since:
                continue
            yield rec


def _group_key(rec: dict, by: str) -> str:
    if by == "job":
        job = rec.get("job")
        return f"job-{job}" if job not in (None, "") else "no-job"
    if by == "run":
        return rec.get("run_id") or "no-run"
    if by == "agent":
        return (rec.get("payload") or {}).get("agent_name") or "research-conductor"
    return "all"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--by", choices=["tool", "job", "run", "agent"], default="tool")
    parser.add_argument("--semantic", action="store_true", help="show sample call args (intent)")
    parser.add_argument("--tool", help="raw calls for one tool: args + result preview")
    parser.add_argument("--since", help="UTC date or ISO timestamp lower bound")
    parser.add_argument("--path")
    parser.add_argument("--logs-dir")
    parser.add_argument("--samples", type=int, default=5, help="sample intents per tool")
    args = parser.parse_args()

    paths = _resolve_paths(args.path, args.logs_dir)
    if not paths:
        print("no trace-events.jsonl found (try --logs-dir or --path)", file=sys.stderr)
        return 1

    # tool -> {calls, ok, error, group -> count, sample intents}
    stats: dict[str, dict] = defaultdict(
        lambda: {"calls": 0, "ok": 0, "error": 0, "groups": defaultdict(int), "intents": []}
    )

    for rec in _iter_events(paths, args.since):
        payload = rec.get("payload") or {}
        tool = payload.get("tool_name") or "unknown"
        if args.tool and tool != args.tool:
            continue
        s = stats[tool]
        if rec.get("action") == "tool_call":
            s["calls"] += 1
            s["groups"][_group_key(rec, args.by)] += 1
            intent = (payload.get("tool_input_preview") or "").strip()
            if args.tool:
                print(f"[{rec.get('timestamp','')}] {tool} <- {intent[:400]}")
            elif intent and len(s["intents"]) < args.samples:
                s["intents"].append(intent[:200])
        elif rec.get("action") == "tool_result":
            if payload.get("status") == "error":
                s["error"] += 1
            else:
                s["ok"] += 1
            if args.tool:
                out = (payload.get("tool_output_preview") or "").strip()
                print(f"    -> [{payload.get('status','?')}] {out[:300]}")

    if args.tool:
        return 0

    if not stats:
        print("no tool calls recorded in the scanned trace events.")
        return 0

    width = max(len(t) for t in stats)
    print(f"{'TOOL'.ljust(width)}  calls   ok  err")
    for tool in sorted(stats, key=lambda t: -stats[t]["calls"]):
        s = stats[tool]
        print(f"{tool.ljust(width)}  {s['calls']:5d}  {s['ok']:3d}  {s['error']:3d}")
        if args.by != "tool":
            for grp in sorted(s["groups"]):
                print(f"    {grp}: {s['groups'][grp]}")
        if args.semantic:
            for intent in s["intents"]:
                print(f"    · {intent}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
