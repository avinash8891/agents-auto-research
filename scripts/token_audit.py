#!/usr/bin/env python3
"""Query token usage from the trace-events.jsonl stream emitted by trace_sdk.

Usage:
    python scripts/token_audit.py                       # totals by agent
    python scripts/token_audit.py --by caller           # group by source_module
    python scripts/token_audit.py --by hour             # group by hour bucket
    python scripts/token_audit.py --by job              # group by trace job id
    python scripts/token_audit.py --by provider         # openai vs anthropic etc.
    python scripts/token_audit.py --by model            # specific model id
    python scripts/token_audit.py --by run              # group by run_id
    python scripts/token_audit.py --by hypothesis       # group by hypothesis_id
    python scripts/token_audit.py --since 2026-05-04    # filter (UTC date or ISO ts)
    python scripts/token_audit.py --tail 20             # last N usage events, raw
    python scripts/token_audit.py --path /custom/trace-events.jsonl
    python scripts/token_audit.py --logs-dir logs/      # scan all trace-events.jsonl in dir
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from autoresearch_logging import get_logger

logger = get_logger("token_audit")

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_LOGS_DIR = REPO_ROOT / "logs"


def _resolve_paths(path_arg: str | None, logs_dir_arg: str | None) -> list[Path]:
    if path_arg:
        return [Path(path_arg).expanduser()]
    base = Path(logs_dir_arg).expanduser() if logs_dir_arg else DEFAULT_LOGS_DIR
    if not base.exists():
        logger.error("logs dir not found: %s", base)
        sys.exit(1)
    paths = sorted(base.rglob("trace-events.jsonl"))
    if not paths:
        logger.error("no trace-events.jsonl files under %s", base)
        sys.exit(1)
    return paths


def _iter_usage_records(paths: list[Path], since: str | None):
    for path in paths:
        if not path.exists():
            continue
        with path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if rec.get("category") != "usage":
                    continue
                if since and rec.get("timestamp", "") < since:
                    continue
                yield rec


def _group_key(rec: dict, by: str) -> str:
    payload = rec.get("payload") or {}
    if by == "agent":
        return payload.get("agent") or "unknown"
    if by == "caller":
        return rec.get("source_module") or "unknown"
    if by == "provider":
        return rec.get("model_provider") or "unknown"
    if by == "model":
        return rec.get("model_name") or "unknown"
    if by == "provider_model":
        return f"{rec.get('model_provider') or 'unknown'}/{rec.get('model_name') or 'unknown'}"
    if by == "run":
        return rec.get("run_id") or "unknown"
    if by == "hypothesis":
        return rec.get("hypothesis_id") or "no-hypothesis"
    if by == "job":
        job = rec.get("job")
        return f"job-{job}" if job is not None else "no-job"
    if by == "hour":
        ts = rec.get("timestamp", "")
        return ts[:13] if len(ts) >= 13 else "unknown"
    if by == "day":
        ts = rec.get("timestamp", "")
        return ts[:10] if len(ts) >= 10 else "unknown"
    return "unknown"


def _print_grouped(records, by: str) -> None:
    buckets: dict[str, dict[str, float]] = defaultdict(
        lambda: {
            "calls": 0,
            "input": 0,
            "cached": 0,
            "output": 0,
            "reasoning": 0,
            "total": 0,
            "cost": 0.0,
        }
    )
    for rec in records:
        payload = rec.get("payload") or {}
        b = buckets[_group_key(rec, by)]
        b["calls"] += 1
        b["input"] += payload.get("input_tokens") or 0
        b["cached"] += payload.get("cached_input_tokens") or 0
        b["output"] += payload.get("output_tokens") or 0
        b["reasoning"] += payload.get("reasoning_output_tokens") or 0
        b["total"] += payload.get("total_tokens") or 0
        b["cost"] += payload.get("cost_usd") or 0.0

    if not buckets:
        print("(no usage events found)")
        return

    rows = sorted(buckets.items(), key=lambda kv: kv[1]["total"], reverse=True)
    header = (
        f"{by:<40} {'calls':>8} {'input':>12} {'cached':>12} {'output':>12} "
        f"{'reasoning':>12} {'total':>12} {'cost_usd':>10}"
    )
    print(header)
    print("-" * len(header))
    grand = {
        "calls": 0,
        "input": 0,
        "cached": 0,
        "output": 0,
        "reasoning": 0,
        "total": 0,
        "cost": 0.0,
    }
    for key, b in rows:
        print(
            f"{key[:40]:<40} {int(b['calls']):>8} {int(b['input']):>12} "
            f"{int(b['cached']):>12} {int(b['output']):>12} "
            f"{int(b['reasoning']):>12} {int(b['total']):>12} {b['cost']:>10.4f}"
        )
        for k in grand:
            grand[k] += b[k]
    print("-" * len(header))
    print(
        f"{'TOTAL':<40} {int(grand['calls']):>8} {int(grand['input']):>12} "
        f"{int(grand['cached']):>12} {int(grand['output']):>12} "
        f"{int(grand['reasoning']):>12} {int(grand['total']):>12} {grand['cost']:>10.4f}"
    )


def _print_tail(paths: list[Path], n: int) -> None:
    rows: list[str] = []
    for path in paths:
        if not path.exists():
            continue
        with path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if rec.get("category") == "usage":
                    rows.append(line)
    for line in rows[-n:]:
        print(line)


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--path", help="path to a specific trace-events.jsonl file")
    parser.add_argument(
        "--logs-dir",
        help="directory to scan recursively for trace-events.jsonl (default: ./logs)",
    )
    parser.add_argument(
        "--by",
        choices=[
            "agent",
            "caller",
            "provider",
            "model",
            "provider_model",
            "run",
            "hypothesis",
            "job",
            "hour",
            "day",
        ],
        default="agent",
    )
    parser.add_argument("--since", help="ISO timestamp or YYYY-MM-DD; only records >= since")
    parser.add_argument("--tail", type=int, help="print last N raw usage events and exit")
    args = parser.parse_args(argv)

    paths = _resolve_paths(args.path, args.logs_dir)

    if args.tail:
        _print_tail(paths, args.tail)
        return 0

    _print_grouped(_iter_usage_records(paths, args.since), args.by)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
