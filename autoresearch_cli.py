#!/usr/bin/env python3
"""
autoresearch_cli.py — CLI helper for autoresearch experiment tracking.

Handles JSONL state management, MAD-based confidence scoring, and experiment logging.
No external dependencies — stdlib only.

Usage:
    python3 autoresearch_cli.py init --jsonl FILE --name NAME --metric-name NAME [--metric-unit UNIT] [--direction lower|higher]
    python3 autoresearch_cli.py log --jsonl FILE --commit SHA --metric VALUE --status STATUS --description DESC [--direction lower|higher] [--metrics '{"k":v}'] [--asi '{"k":"v"}']
    python3 autoresearch_cli.py evaluate --jsonl FILE --metric VALUE --direction lower|higher
    python3 autoresearch_cli.py summary --jsonl FILE
    python3 autoresearch_cli.py status --jsonl FILE
"""

import argparse
import json
import logging
import os
import statistics
import sys
import time

from autoresearch_constants import MILLISECONDS_PER_SECOND


def _make_stdout_logger() -> logging.Logger:
    """Stdlib-only logger that emits %(message)s to stdout, byte-identical
    to print() output. Preserves the `DECISION: keep` / `DECISION: discard`
    stdout contract that autoresearch_experiment.evaluate_metric parses."""
    logger = logging.getLogger("autoresearch_cli")
    if not any(
        isinstance(h, logging.StreamHandler) and getattr(h, "stream", None) is sys.stdout
        for h in logger.handlers
    ):
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(logging.Formatter("%(message)s"))
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
        logger.propagate = False
    return logger


_log = _make_stdout_logger()


def read_jsonl(path):
    """Read a JSONL file, returning (config, results) where config is the latest config header."""
    config = None
    results = []
    segment = 0

    if not os.path.exists(path):
        return config, results

    with open(path, "r") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue

            if entry.get("type") == "config":
                if results:
                    segment += 1
                config = entry
                config["_segment"] = segment
                continue

            # Skip non-result entries (e.g. research_round thesis proposals)
            if entry.get("type") in ("research_round",):
                continue

            # Skip entries without a metric (not experiment results)
            if "metric" not in entry:
                continue

            entry.setdefault("segment", segment)
            entry.setdefault("metrics", {})
            entry.setdefault("confidence", None)
            entry.setdefault("asi", None)
            results.append(entry)

    return config, results


def current_segment_results(results, segment):
    """Filter results to the current segment only."""
    return [r for r in results if r.get("segment", 0) == segment]


def compute_mad(values):
    """Compute Median Absolute Deviation."""
    if len(values) < 2:
        return 0.0
    median = statistics.median(values)
    deviations = [abs(v - median) for v in values]
    return statistics.median(deviations)


def compute_confidence(results, segment, direction):
    """
    Compute confidence score: |best_improvement| / MAD.

    Returns None if fewer than 3 data points or MAD is 0.
    """
    cur = [
        r
        for r in current_segment_results(results, segment)
        if r.get("status") not in ("crash", "checks_failed")
    ]
    if len(cur) < 3:
        return None

    values = [r["metric"] for r in cur]
    mad = compute_mad(values)
    if mad == 0:
        return None

    baseline = find_baseline(results, segment)
    if baseline is None:
        return None

    best_kept = None
    for r in cur:
        if r.get("status") == "keep":
            val = r["metric"]
            if best_kept is None:
                best_kept = val
            elif direction == "lower" and val < best_kept:
                best_kept = val
            elif direction == "higher" and val > best_kept:
                best_kept = val

    if best_kept is None or best_kept == baseline:
        return None

    delta = abs(best_kept - baseline)
    return round(delta / mad, 2)


def find_baseline(results, segment):
    """Find the baseline metric (first experiment in current segment)."""
    cur = current_segment_results(results, segment)
    return cur[0]["metric"] if cur else None


def find_best_kept(results, segment, direction):
    """Find the best kept metric in the current segment."""
    cur = current_segment_results(results, segment)
    best = None
    for r in cur:
        if r.get("status") == "keep":
            val = r["metric"]
            if best is None:
                best = val
            elif direction == "lower" and val < best:
                best = val
            elif direction == "higher" and val > best:
                best = val
    return best


def is_better(current, best, direction):
    return current < best if direction == "lower" else current > best


def cmd_init(args):
    """Write a config header to the JSONL file."""
    config = {
        "type": "config",
        "name": args.name,
        "metricName": args.metric_name,
        "metricUnit": args.metric_unit or "",
        "bestDirection": args.direction or "lower",
    }
    mode = "a" if os.path.exists(args.jsonl) else "w"
    with open(args.jsonl, mode) as f:
        f.write(json.dumps(config) + "\n")
    _log.info(
        f"Initialized: {args.name} (metric: {args.metric_name}, direction: {args.direction or 'lower'})"
    )


def cmd_log(args):
    """Append an experiment result to the JSONL file."""
    config, results = read_jsonl(args.jsonl)

    if config is None:
        _log.error("Error: No config found. Run 'init' first.")
        sys.exit(1)

    segment = config.get("_segment", 0) if config else 0
    direction = args.direction or (config.get("bestDirection", "lower") if config else "lower")

    extra_metrics = {}
    if args.metrics:
        try:
            extra_metrics = json.loads(args.metrics)
        except json.JSONDecodeError:
            _log.error(f"Warning: could not parse --metrics JSON: {args.metrics}")

    asi = None
    if args.asi:
        try:
            asi = json.loads(args.asi)
        except json.JSONDecodeError:
            _log.error(f"Warning: could not parse --asi JSON: {args.asi}")

    entry = {
        "run": len(results) + 1,
        "commit": args.commit[:7] if args.commit else "0000000",
        "metric": args.metric,
        "metrics": extra_metrics,
        "status": args.status,
        "description": args.description,
        "timestamp": int(time.time() * MILLISECONDS_PER_SECOND),
        "segment": segment,
        "confidence": None,
        "asi": asi,
    }

    results.append(entry)

    confidence = compute_confidence(results, segment, direction)
    entry["confidence"] = confidence

    with open(args.jsonl, "a") as f:
        out = {k: v for k, v in entry.items() if v is not None or k in ("confidence",)}
        f.write(json.dumps(out) + "\n")

    baseline = find_baseline(results, segment)
    best = find_best_kept(results, segment, direction)

    _log.info(f"Logged #{entry['run']}: {args.status} — {args.description}")
    _log.info(f"  Metric: {args.metric}")
    if baseline is not None:
        _log.info(f"  Baseline: {baseline}")
    if best is not None and baseline is not None and baseline != 0:
        delta_pct = ((best - baseline) / baseline) * 100
        _log.info(f"  Best kept: {best} ({delta_pct:+.1f}%)")
    if confidence is not None:
        label = (
            "likely real"
            if confidence >= 2.0
            else "marginal" if confidence >= 1.0 else "within noise"
        )
        _log.info(f"  Confidence: {confidence}x ({label})")


def cmd_evaluate(args):
    """Evaluate whether a new metric value should be kept or discarded."""
    config, results = read_jsonl(args.jsonl)

    if not config:
        _log.info("No config found in JSONL. Run init first.")
        sys.exit(1)

    segment = config.get("_segment", 0)
    direction = args.direction or config.get("bestDirection", "lower")
    baseline = find_baseline(results, segment)
    best = find_best_kept(results, segment, direction)

    compare_against = best if best is not None else baseline

    if compare_against is None:
        _log.info("DECISION: keep (first experiment — this is the baseline)")
        _log.info(f"  Metric: {args.metric}")
        sys.exit(0)

    improved = is_better(args.metric, compare_against, direction)

    results_with_new = results + [{"metric": args.metric, "status": "keep", "segment": segment}]
    confidence = compute_confidence(results_with_new, segment, direction)

    delta = args.metric - compare_against
    delta_pct = (delta / compare_against) * 100 if compare_against != 0 else 0

    if improved:
        _log.info(f"DECISION: keep")
    else:
        _log.info(f"DECISION: discard")

    _log.info(f"  Metric: {args.metric}")
    _log.info(
        f"  Compare against: {compare_against} ({'best kept' if best is not None else 'baseline'})"
    )
    _log.info(f"  Delta: {delta:+.4f} ({delta_pct:+.1f}%)")
    _log.info(f"  Direction: {direction} is better")

    if confidence is not None:
        label = (
            "likely real"
            if confidence >= 2.0
            else "marginal" if confidence >= 1.0 else "within noise"
        )
        _log.info(f"  Confidence: {confidence}x ({label})")
        if confidence < 1.0 and improved:
            _log.info(
                f"  Warning: improvement is within noise floor. Consider re-running to confirm."
            )


def cmd_summary(args):
    """Print a summary of the experiment session."""
    config, results = read_jsonl(args.jsonl)

    if not config:
        _log.info("No experiments found.")
        return

    segment = config.get("_segment", 0)
    cur = current_segment_results(results, segment)
    direction = config.get("bestDirection", "lower")

    total = len(cur)
    kept = [r for r in cur if r.get("status") == "keep"]
    discarded = [r for r in cur if r.get("status") == "discard"]
    crashed = [r for r in cur if r.get("status") in ("crash", "checks_failed")]

    baseline = find_baseline(results, segment)
    best = find_best_kept(results, segment, direction)
    confidence = compute_confidence(results, segment, direction)

    _log.info(f"Session: {config.get('name', 'unnamed')}")
    _log.info(
        f"Metric: {config.get('metricName', 'metric')} ({config.get('metricUnit', '')}), {direction} is better"
    )
    _log.info(
        f"Experiments: {total} total, {len(kept)} kept, {len(discarded)} discarded, {len(crashed)} crashed"
    )
    _log.info("")

    if baseline is not None:
        _log.info(f"Baseline: {baseline}")
    if best is not None and baseline is not None and baseline != 0:
        delta_pct = ((best - baseline) / baseline) * 100
        _log.info(f"Best kept: {best} ({delta_pct:+.1f}% from baseline)")
    if confidence is not None:
        label = (
            "likely real"
            if confidence >= 2.0
            else "marginal" if confidence >= 1.0 else "within noise"
        )
        _log.info(f"Confidence: {confidence}x ({label})")

    _log.info("")
    _log.info("Kept experiments:")
    for r in kept:
        desc = r.get("description", "")
        metric = r.get("metric", 0)
        commit = r.get("commit", "?")
        _log.info(
            f"  #{r.get('run', '?')} [{commit}] {config.get('metricName', 'metric')}={metric}  {desc}"
        )

    if crashed:
        _log.info("")
        _log.info("Crashed/failed:")
        for r in crashed:
            desc = r.get("description", "")
            status = r.get("status", "crash")
            _log.info(f"  #{r.get('run', '?')} [{status}] {desc}")


def cmd_status(args):
    """Print current status (baseline, best, confidence) as JSON for programmatic use."""
    config, results = read_jsonl(args.jsonl)

    if not config:
        _log.info(json.dumps({"error": "no config found"}))
        return

    segment = config.get("_segment", 0)
    direction = config.get("bestDirection", "lower")
    cur = current_segment_results(results, segment)

    baseline = find_baseline(results, segment)
    best = find_best_kept(results, segment, direction)
    confidence = compute_confidence(results, segment, direction)

    status = {
        "name": config.get("name"),
        "metricName": config.get("metricName"),
        "direction": direction,
        "totalExperiments": len(cur),
        "keptCount": len([r for r in cur if r.get("status") == "keep"]),
        "baseline": baseline,
        "bestKept": best,
        "confidence": confidence,
        "deltaPercent": (
            round(((best - baseline) / baseline) * 100, 2)
            if best is not None and baseline is not None and baseline != 0
            else None
        ),
    }
    _log.info(json.dumps(status, indent=2))


def main():
    parser = argparse.ArgumentParser(description="Autoresearch experiment helper")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # init
    p_init = subparsers.add_parser("init", help="Initialize experiment session")
    p_init.add_argument("--jsonl", required=True, help="Path to autoresearch.jsonl")
    p_init.add_argument("--name", required=True, help="Session name")
    p_init.add_argument("--metric-name", required=True, help="Primary metric name")
    p_init.add_argument("--metric-unit", default="", help="Metric unit (e.g., us, ms, s, kb)")
    p_init.add_argument("--direction", default="lower", choices=["lower", "higher"])

    # log
    p_log = subparsers.add_parser("log", help="Log an experiment result")
    p_log.add_argument("--jsonl", required=True, help="Path to autoresearch.jsonl")
    p_log.add_argument("--commit", required=True, help="Git commit hash")
    p_log.add_argument("--metric", required=True, type=float, help="Primary metric value")
    p_log.add_argument(
        "--status", required=True, choices=["keep", "discard", "crash", "checks_failed"]
    )
    p_log.add_argument("--description", required=True, help="What was tried")
    p_log.add_argument(
        "--direction", choices=["lower", "higher"], help="Override direction from config"
    )
    p_log.add_argument("--metrics", help="Additional metrics as JSON object")
    p_log.add_argument("--asi", help="Actionable Side Information as JSON object")

    # evaluate
    p_eval = subparsers.add_parser("evaluate", help="Evaluate whether to keep or discard")
    p_eval.add_argument("--jsonl", required=True, help="Path to autoresearch.jsonl")
    p_eval.add_argument("--metric", required=True, type=float, help="New metric value to evaluate")
    p_eval.add_argument(
        "--direction", choices=["lower", "higher"], help="Override direction from config"
    )

    # summary
    p_summary = subparsers.add_parser("summary", help="Print experiment summary")
    p_summary.add_argument("--jsonl", required=True, help="Path to autoresearch.jsonl")

    # status
    p_status = subparsers.add_parser("status", help="Print current status as JSON")
    p_status.add_argument("--jsonl", required=True, help="Path to autoresearch.jsonl")

    args = parser.parse_args()

    commands = {
        "init": cmd_init,
        "log": cmd_log,
        "evaluate": cmd_evaluate,
        "summary": cmd_summary,
        "status": cmd_status,
    }
    commands[args.command](args)


if __name__ == "__main__":
    main()
