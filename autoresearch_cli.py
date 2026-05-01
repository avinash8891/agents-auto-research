#!/usr/bin/env python3
"""SQLite-backed CLI helper for autoresearch experiment tracking."""

import argparse
import json
import logging
import math
import statistics
import sys
from pathlib import Path
from typing import Any

from artifact_io import timestamp_now
from experiment_db import ExperimentDB, ExperimentResult
from persistence_utils import json_loads_metric_sentinels


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


def _db(path: str) -> ExperimentDB:
    return ExperimentDB(Path(path))


def _coerce_cli_metric(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def read_session(path: str) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    db = _db(path)
    config = db.session_meta() or None
    if config is not None:
        config["_segment"] = 0
    primary_metric_name = (
        config.get("metricName", "median_expectancy") if config else "median_expectancy"
    )
    results = []
    for idx, record in enumerate(db.all(), start=1):
        metric = record.validation_metrics.get(primary_metric_name) if config else None
        if metric is None:
            metric = record.train_metrics.get(primary_metric_name)
        metric = _coerce_cli_metric(metric)
        results.append(
            {
                "run": idx,
                "commit": record.code_commit[:7],
                "metric": metric,
                "metrics": json_loads_metric_sentinels(json.dumps(record.validation_metrics)),
                "status": "keep" if record.accepted else "discard",
                "description": getattr(
                    record,
                    "_description_export",
                    f"strict-native loop: {Path(record.config_path).stem}",
                ),
                "timestamp": record.timestamp,
                "segment": 0,
                "confidence": None,
                "asi": getattr(record, "_asi_export", None),
            }
        )
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
    baseline = cur[0].get("metric") if cur else None
    try:
        if baseline is None or not math.isfinite(float(baseline)):
            return None
        baseline = float(baseline)
    except (TypeError, ValueError):
        return None

    numeric_cur = []
    for r in cur:
        metric = r.get("metric")
        if metric is None:
            continue
        try:
            if not math.isfinite(float(metric)):
                continue
        except (TypeError, ValueError):
            continue
        numeric_cur.append(r)
    if len(numeric_cur) < 3:
        return None

    values = [float(r["metric"]) for r in numeric_cur]
    mad = compute_mad(values)
    if mad == 0:
        return None

    best_kept = None
    for r in numeric_cur:
        if r.get("status") == "keep":
            metric = r.get("metric")
            if metric is None:
                continue
            val = float(metric)
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
    if not cur:
        return None
    metric = cur[0].get("metric")
    if metric is None:
        return None
    val = float(metric)
    return val if math.isfinite(val) else None


def find_best_kept(results, segment, direction):
    """Find the best kept metric in the current segment."""
    cur = current_segment_results(results, segment)
    best = None
    for r in cur:
        if r.get("status") == "keep":
            metric = r.get("metric")
            if metric is None:
                continue
            try:
                val = float(metric)
            except (TypeError, ValueError):
                continue
            if not math.isfinite(val):
                continue
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
    """Initialize the sqlite-backed experiment session."""
    db = _db(args.db)
    db.init_session(
        name=args.name,
        metric_name=args.metric_name,
        direction=args.direction or "lower",
    )
    _log.info(
        f"Initialized: {args.name} (metric: {args.metric_name}, direction: {args.direction or 'lower'})"
    )


def cmd_log(args):
    """Append an experiment result to the sqlite-backed session."""
    config, results = read_session(args.db)

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

    primary_metric_name = (
        config.get("metricName", "median_expectancy") if config else "median_expectancy"
    )
    merged_metrics = dict(extra_metrics)
    merged_metrics[primary_metric_name] = args.metric

    entry = {
        "run": len(results) + 1,
        "commit": args.commit[:7] if args.commit else "0000000",
        "metric": args.metric,
        "metrics": merged_metrics,
        "status": args.status,
        "description": args.description,
        "timestamp": timestamp_now(),
        "segment": segment,
        "confidence": None,
        "asi": asi,
    }

    results.append(entry)

    confidence = compute_confidence(results, segment, direction)
    entry["confidence"] = confidence

    db = _db(args.db)
    record = ExperimentResult(
        experiment_id=f"cli-run-{entry['run']}",
        thesis_id=f"cli-run-{entry['run']}",
        config_path=args.description,
        runtime_config={},
        code_commit=args.commit,
        data_hash="",
        train_metrics=merged_metrics,
        validation_metrics=merged_metrics,
        trade_count=int(merged_metrics.get("trade_count", 0) or 0),
        trades_file="",
        strategy_events_file="",
        diagnostics_file="",
        strategy_diagnostics={},
        accepted=args.status == "keep",
        rejection_reason="N/A" if args.status == "keep" else args.description,
        verdict_status="accepted" if args.status == "keep" else "rejected",
        verdict_summary="kept by CLI evaluation" if args.status == "keep" else args.description,
        timestamp=entry["timestamp"],
    )
    setattr(record, "_description_export", args.description)
    if asi is not None:
        setattr(record, "_asi_export", asi)
    db.add(record)

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
    config, results = read_session(args.db)

    if not config:
        _log.info("No config found in the experiment database. Run init first.")
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
    config, results = read_session(args.db)

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
    config, results = read_session(args.db)

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
    p_init.add_argument("--db", required=True, help="Path to experiments sqlite database")
    p_init.add_argument("--name", required=True, help="Session name")
    p_init.add_argument("--metric-name", required=True, help="Primary metric name")
    p_init.add_argument("--metric-unit", default="", help="Metric unit (e.g., us, ms, s, kb)")
    p_init.add_argument("--direction", default="lower", choices=["lower", "higher"])

    # log
    p_log = subparsers.add_parser("log", help="Log an experiment result")
    p_log.add_argument("--db", required=True, help="Path to experiments sqlite database")
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
    p_eval.add_argument("--db", required=True, help="Path to experiments sqlite database")
    p_eval.add_argument("--metric", required=True, type=float, help="New metric value to evaluate")
    p_eval.add_argument(
        "--direction", choices=["lower", "higher"], help="Override direction from config"
    )

    # summary
    p_summary = subparsers.add_parser("summary", help="Print experiment summary")
    p_summary.add_argument("--db", required=True, help="Path to experiments sqlite database")

    # status
    p_status = subparsers.add_parser("status", help="Print current status as JSON")
    p_status.add_argument("--db", required=True, help="Path to experiments sqlite database")

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
