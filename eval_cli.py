#!/usr/bin/env python3
"""CLI entry point for the held-out eval harness.

Usage:
    python eval_cli.py --label baseline-pre-halo
    python eval_cli.py --label halo-trial-1 --repeat 5

This is a separate file from ``autoresearch_cli.py`` (which is the
SQLite experiment-tracker CLI) to keep the two concerns from
tangling.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from autoresearch_logging import get_logger
from eval_harness import latest_eval_result_path, run_eval
from eval_metrics import compare_eval_results

log = get_logger(__name__)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run the held-out eval suite and persist the result."
    )
    parser.add_argument(
        "--label",
        required=True,
        help="Label for this eval run (used in the output filename).",
    )
    parser.add_argument(
        "--repeat",
        type=int,
        default=3,
        help="Number of independent suite repetitions for variance (default: 3).",
    )
    parser.add_argument(
        "--holdout-path",
        type=Path,
        default=None,
        help="Override path to holdout_tasks.yaml.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Override output directory (default: eval_results/).",
    )
    parser.add_argument(
        "--primary-metric",
        choices=["compiled_rate", "quality_score_p50"],
        default="compiled_rate",
    )
    parser.add_argument(
        "--max-workers",
        type=int,
        default=1,
        help=(
            "Run held-out tasks in parallel via ProcessPoolExecutor "
            "(default: 1 = sequential). Each worker runs in its own "
            "process, so trace_sdk's process-global state stays isolated."
        ),
    )
    return parser


def _load_prior_result(output_dir: Path, current_path: Path | None):
    """Return the most recent eval JSON other than ``current_path``.

    mtime ordering, not lex — labels with different prefixes
    (``baseline-...`` vs ``halo-trial-...``) sort wrong lexicographically
    even when their timestamps would order them correctly.
    """
    if not output_dir.exists():
        return None
    candidates = [p for p in output_dir.glob("*.json") if current_path is None or p != current_path]
    if not candidates:
        return None
    import json

    from eval_metrics import EvalResult

    most_recent = max(candidates, key=lambda p: p.stat().st_mtime)
    return EvalResult.from_dict(json.loads(most_recent.read_text(encoding="utf-8")))


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    output_dir = args.output_dir or (Path(__file__).resolve().parent / "eval_results")
    # Two distinct roles for latest_eval_result_path here:
    #   1. pre-run call (this line) is the GATE — if no prior result exists,
    #      skip the delta comparison entirely.
    #   2. post-run call (below) is the EXCLUSION KEY — it identifies the
    #      file run_eval just wrote so _load_prior_result can exclude it
    #      and recover the actual prior. Both calls are required.
    pre_run_latest = latest_eval_result_path(output_dir)
    result = run_eval(
        label=args.label,
        repeat=args.repeat,
        holdout_path=args.holdout_path,
        output_dir=output_dir,
        primary_metric_name=args.primary_metric,
        max_workers=args.max_workers,
    )
    log.info(
        f"EVAL primary={result.primary_metric_name} "
        f"mean={result.primary_metric_mean} stdev={result.primary_metric_stdev} "
        f"min={result.primary_metric_min} max={result.primary_metric_max}"
    )
    if pre_run_latest is not None:
        prior = _load_prior_result(output_dir, latest_eval_result_path(output_dir))
        if prior is not None and prior.primary_metric_name == result.primary_metric_name:
            delta = compare_eval_results(result, prior)
            log.info(
                f"EVAL DELTA vs prior label={prior.label} "
                f"sign={delta['delta_sign']} delta={delta['delta']:.4f} "
                f"in_stdevs={delta['delta_in_stdevs']}"
            )
    return 0


if __name__ == "__main__":
    sys.exit(main())
