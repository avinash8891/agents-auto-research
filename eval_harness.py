"""Held-out eval harness.

Loads ``configs/eval/holdout_tasks.yaml`` and runs each task ``repeat``
times against a fresh controller. Per-task temp ``controller.root`` so
no shadow state leaks between tasks.

Split into a pure layer (computes EvalResult from a list of TaskOutcome)
and a live layer (drives a real controller). Tests exercise the pure
layer with a synthetic ``task_runner``; the live layer drives ``eval_cli``.
"""

from __future__ import annotations

import tempfile
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import yaml

from autoresearch_constants import HOLDOUT_TASKS_PATH
from autoresearch_logging import get_logger
from eval_metrics import (
    KEEP_OUTCOMES,
    OUTCOME_CONDUCTOR_ERROR,
    EvalResult,
    SuiteSummary,
    TaskOutcome,
    summarize_eval,
    summarize_suite,
)
from persistence_utils import utc_now_iso8601, write_json_atomic

log = get_logger(__name__)

EVAL_RESULTS_DIRNAME = "eval_results"


@dataclass
class HoldoutTask:
    family: str
    dataset_window: str
    overrides: dict


# A task_runner takes a HoldoutTask + a controller_root and returns the
# round's TaskOutcome. The default implementation drives a real
# controller; tests substitute a stub.
TaskRunner = Callable[[HoldoutTask, Path], TaskOutcome]


def load_holdout_tasks(path: Path) -> list[HoldoutTask]:
    """Parse the held-out task YAML.

    Schema:
        tasks:
          - family: ema5
            dataset_window: "2024-01-01..2024-06-30"
            overrides: {}    # optional family-specific knobs
    """
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or "tasks" not in raw:
        raise ValueError(f"{path}: expected a top-level 'tasks' list")
    tasks_raw = raw["tasks"]
    if not isinstance(tasks_raw, list) or not tasks_raw:
        raise ValueError(f"{path}: 'tasks' must be a non-empty list")
    tasks: list[HoldoutTask] = []
    for idx, item in enumerate(tasks_raw):
        if not isinstance(item, dict):
            raise ValueError(f"{path}: tasks[{idx}] is not a mapping")
        try:
            family = str(item["family"])
            dataset_window = str(item["dataset_window"])
        except KeyError as exc:
            raise ValueError(f"{path}: tasks[{idx}] missing key {exc}") from exc
        overrides = item.get("overrides") or {}
        if not isinstance(overrides, dict):
            raise ValueError(f"{path}: tasks[{idx}].overrides must be a mapping")
        tasks.append(
            HoldoutTask(family=family, dataset_window=dataset_window, overrides=dict(overrides))
        )
    return tasks


def _default_task_runner(task: HoldoutTask, controller_root: Path) -> TaskOutcome:
    """Run one research round on a fresh controller in `controller_root`.

    Imports are deferred so unit tests can substitute the runner without
    pulling in the full controller graph.
    """
    from autoresearch_controller import AutoresearchController, default_controller_paths
    from autoresearch_research import _classify_round_outcome
    from strategy_family import load_family

    family = load_family(task.family)
    state_path, current_md_path, ideas_md_path, runs_dir = default_controller_paths(
        controller_root, family
    )
    controller = AutoresearchController(
        root=controller_root,
        state_path=state_path,
        current_md_path=current_md_path,
        ideas_md_path=ideas_md_path,
        runs_dir=runs_dir,
        family=family,
    )
    initial_state = {
        "state": "running",
        "job": 1,
        "research_round": 0,
        "job_usage": None,
        "heartbeat": {},
        "eval_dataset_window": task.dataset_window,
    }
    controller.write_state(initial_state)
    try:
        result = controller.execute_research_one()
    except Exception as exc:
        log.error(
            f"EVAL_HARNESS task family={task.family} window={task.dataset_window} "
            f"raised {type(exc).__name__}: {exc}; recording as conductor_error. "
            f"Action: inspect controller log for stack trace."
        )
        return TaskOutcome(
            family=task.family,
            dataset_window=task.dataset_window,
            outcome=OUTCOME_CONDUCTOR_ERROR,
            overall_score=0.0,
        )
    outcome = _classify_round_outcome(result if isinstance(result, dict) else {})
    score = 1.0 if outcome in KEEP_OUTCOMES else 0.0
    return TaskOutcome(
        family=task.family,
        dataset_window=task.dataset_window,
        outcome=outcome,
        overall_score=score,
    )


def _run_one_task_with_runner(runner: TaskRunner, task: HoldoutTask) -> TaskOutcome:
    """Picklable bridge for ProcessPoolExecutor.

    Each subprocess re-imports trace_sdk → fresh _STATE, _PROVIDER, and
    Traceloop init. Process boundary, not threading, is the isolation
    layer because OTel/Traceloop/OpenAIInstrumentor are process-globals
    by design.
    """
    with tempfile.TemporaryDirectory(prefix="eval-task-") as tmpdir:
        return runner(task, Path(tmpdir))


def run_one_suite(
    tasks: list[HoldoutTask],
    *,
    task_runner: TaskRunner | None = None,
    max_workers: int = 1,
) -> SuiteSummary:
    """Run every task once and roll up to a SuiteSummary.

    ``task_runner`` defaults to the module-level ``_default_task_runner``
    resolved at call time — tests can monkeypatch the module attribute
    without re-importing.

    ``max_workers > 1`` runs tasks concurrently via ProcessPoolExecutor.
    The caller-supplied runner must be picklable (top-level function, not
    a closure).
    """
    runner = task_runner or _default_task_runner
    if max_workers > 1:
        with ProcessPoolExecutor(max_workers=max_workers) as pool:
            futures = [pool.submit(_run_one_task_with_runner, runner, t) for t in tasks]
            return summarize_suite([f.result() for f in futures])

    return summarize_suite([_run_one_task_with_runner(runner, t) for t in tasks])


def run_eval(
    *,
    label: str,
    repeat: int = 3,
    holdout_path: Path | None = None,
    output_dir: Path | None = None,
    task_runner: TaskRunner | None = None,
    primary_metric_name: str = "compiled_rate",
    max_workers: int = 1,
) -> EvalResult:
    """Run the full held-out suite ``repeat`` times and persist the result.

    Returns the EvalResult; also writes ``eval_results/{label}-{ts}.json``
    relative to ``output_dir`` (default: repo root).
    """
    if repeat < 1:
        raise ValueError(f"repeat must be >= 1, got {repeat}")
    if max_workers < 1:
        raise ValueError(f"max_workers must be >= 1, got {max_workers}")
    holdout_path = holdout_path or _resolve_holdout_path()
    tasks = load_holdout_tasks(holdout_path)
    log.info(
        f"EVAL_HARNESS START label={label} tasks={len(tasks)} repeat={repeat} "
        f"max_workers={max_workers}"
    )
    suites: list[SuiteSummary] = []
    for i in range(repeat):
        suite = run_one_suite(tasks, task_runner=task_runner, max_workers=max_workers)
        log.info(
            f"EVAL_HARNESS suite={i + 1}/{repeat} compiled_rate={suite.compiled_rate} "
            f"quality_p50={suite.quality_score_p50}"
        )
        suites.append(suite)
    timestamp = utc_now_iso8601()
    result = summarize_eval(
        label=label,
        timestamp=timestamp,
        suites=suites,
        primary_metric_name=primary_metric_name,
    )
    output_dir = output_dir or _default_output_dir()
    persist_eval_result(result, output_dir)
    return result


def persist_eval_result(result: EvalResult, output_dir: Path) -> Path:
    """Write the eval result as JSON; return the path."""
    output_dir.mkdir(parents=True, exist_ok=True)
    safe_label = result.label.replace("/", "_").replace(" ", "_")
    safe_ts = result.timestamp.replace(":", "").replace("+", "p")
    out_path = output_dir / f"{safe_label}-{safe_ts}.json"
    write_json_atomic(out_path, result.to_dict())
    log.info(f"EVAL_HARNESS WROTE {out_path}")
    return out_path


def latest_eval_result_path(output_dir: Path) -> Path | None:
    """Return the most recently modified ``*.json`` under ``output_dir``.

    mtime ordering, not lex order — labels with different prefixes
    ("baseline-..." vs "halo-trial-...") otherwise sort wrong.
    """
    if not output_dir.exists():
        return None
    candidates = list(output_dir.glob("*.json"))
    if not candidates:
        return None
    return max(candidates, key=lambda p: p.stat().st_mtime)


def _resolve_holdout_path() -> Path:
    return Path(__file__).resolve().parent / HOLDOUT_TASKS_PATH


def _default_output_dir() -> Path:
    return Path(__file__).resolve().parent / EVAL_RESULTS_DIRNAME
