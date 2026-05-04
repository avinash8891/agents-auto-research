"""Unit tests for eval_harness.

The harness is split into a pure layer (load_holdout_tasks,
run_one_suite, run_eval, persist_eval_result) and a live runner that
drives the real controller. These tests use a synthetic ``task_runner``
to exercise the pure layer without spinning up the conductor or hitting
an LLM proxy.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from eval_harness import (
    EVAL_RESULTS_DIRNAME,
    HoldoutTask,
    latest_eval_result_path,
    load_holdout_tasks,
    persist_eval_result,
    run_eval,
    run_one_suite,
)
from eval_metrics import TaskOutcome


def _write_holdout_yaml(path: Path, tasks: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump({"tasks": tasks}), encoding="utf-8")


# ── load_holdout_tasks ──────────────────────────────────────────


def test_load_holdout_tasks_basic(tmp_path):
    p = tmp_path / "holdout.yaml"
    _write_holdout_yaml(
        p,
        [
            {"family": "ema", "dataset_window": "2024-h1"},
            {"family": "orb", "dataset_window": "2024-h2", "overrides": {"k": 1}},
        ],
    )
    tasks = load_holdout_tasks(p)
    assert len(tasks) == 2
    assert tasks[0] == HoldoutTask(family="ema", dataset_window="2024-h1", overrides={})
    assert tasks[1].overrides == {"k": 1}


def test_load_holdout_tasks_missing_top_level_raises(tmp_path):
    p = tmp_path / "h.yaml"
    p.write_text("not_tasks: []\n", encoding="utf-8")
    with pytest.raises(ValueError, match="expected a top-level 'tasks' list"):
        load_holdout_tasks(p)


def test_load_holdout_tasks_empty_list_raises(tmp_path):
    p = tmp_path / "h.yaml"
    p.write_text("tasks: []\n", encoding="utf-8")
    with pytest.raises(ValueError, match="non-empty list"):
        load_holdout_tasks(p)


def test_load_holdout_tasks_missing_family_raises(tmp_path):
    p = tmp_path / "h.yaml"
    _write_holdout_yaml(p, [{"dataset_window": "2024-h1"}])
    with pytest.raises(ValueError, match="missing key"):
        load_holdout_tasks(p)


def test_load_holdout_tasks_overrides_must_be_mapping(tmp_path):
    p = tmp_path / "h.yaml"
    _write_holdout_yaml(p, [{"family": "ema", "dataset_window": "x", "overrides": [1, 2]}])
    with pytest.raises(ValueError, match="overrides must be a mapping"):
        load_holdout_tasks(p)


# ── run_one_suite ───────────────────────────────────────────────


def test_run_one_suite_invokes_task_runner_per_task():
    tasks = [
        HoldoutTask(family="ema", dataset_window="w1", overrides={}),
        HoldoutTask(family="orb", dataset_window="w2", overrides={}),
    ]
    seen: list[HoldoutTask] = []

    def fake_runner(task: HoldoutTask, controller_root: Path) -> TaskOutcome:
        seen.append(task)
        # Each fake task root is a tempdir — assert it's writable + isolated.
        assert controller_root.exists()
        assert controller_root.is_dir()
        return TaskOutcome(
            family=task.family,
            dataset_window=task.dataset_window,
            outcome="compiled" if task.family == "ema" else "rejected",
            overall_score=1.0 if task.family == "ema" else 0.0,
        )

    suite = run_one_suite(tasks, task_runner=fake_runner)
    assert [t.family for t in seen] == ["ema", "orb"]
    assert suite.n_tasks == 2
    assert suite.compiled_rate == 0.5


def test_run_one_suite_per_task_root_is_isolated():
    """Each task gets its own tempdir — no cross-task shadow state."""
    seen_roots: list[Path] = []

    def runner(task, root):
        seen_roots.append(root)
        return TaskOutcome(
            family=task.family,
            dataset_window=task.dataset_window,
            outcome="compiled",
            overall_score=1.0,
        )

    tasks = [HoldoutTask("ema", "w1", {}), HoldoutTask("ema", "w2", {})]
    run_one_suite(tasks, task_runner=runner)
    assert len(seen_roots) == 2
    assert seen_roots[0] != seen_roots[1]


# ── run_eval (full path with persistence) ───────────────────────


def test_run_eval_persists_result_and_returns_summary(tmp_path):
    holdout = tmp_path / "holdout.yaml"
    _write_holdout_yaml(
        holdout,
        [
            {"family": "ema", "dataset_window": "w1"},
            {"family": "orb", "dataset_window": "w2"},
        ],
    )
    output_dir = tmp_path / "eval_results"

    def fake_runner(task, root):
        return TaskOutcome(
            family=task.family,
            dataset_window=task.dataset_window,
            outcome="compiled",
            overall_score=1.0,
        )

    result = run_eval(
        label="unit-test",
        repeat=2,
        holdout_path=holdout,
        output_dir=output_dir,
        task_runner=fake_runner,
    )
    assert result.label == "unit-test"
    assert result.repeat == 2
    assert result.primary_metric_mean == 1.0
    written = list(output_dir.glob("*.json"))
    assert len(written) == 1
    payload = json.loads(written[0].read_text())
    assert payload["label"] == "unit-test"
    assert payload["primary_metric_name"] == "compiled_rate"
    assert payload["primary_metric"]["mean"] == 1.0


def test_run_eval_repeat_zero_raises(tmp_path):
    holdout = tmp_path / "h.yaml"
    _write_holdout_yaml(holdout, [{"family": "ema", "dataset_window": "w1"}])
    with pytest.raises(ValueError, match="repeat must be"):
        run_eval(
            label="t",
            repeat=0,
            holdout_path=holdout,
            output_dir=tmp_path,
            task_runner=lambda *a: TaskOutcome("ema", "w1", "compiled", 1.0),
        )


def test_run_eval_variance_across_repeats(tmp_path):
    """Stochastic runner: different repeats yield different rates → stdev > 0."""
    holdout = tmp_path / "h.yaml"
    _write_holdout_yaml(
        holdout,
        [
            {"family": "ema", "dataset_window": "w1"},
            {"family": "orb", "dataset_window": "w2"},
        ],
    )
    counter = {"n": 0}

    def runner(task, root):
        counter["n"] += 1
        # First suite: 2 compiled. Second: 1 compiled. Third: 0 compiled.
        suite_idx = (counter["n"] - 1) // 2
        compiled = (counter["n"] - 1) % 2 == 0 and suite_idx < 2
        return TaskOutcome(
            family=task.family,
            dataset_window=task.dataset_window,
            outcome="compiled" if compiled else "rejected",
            overall_score=1.0 if compiled else 0.0,
        )

    result = run_eval(
        label="var",
        repeat=3,
        holdout_path=holdout,
        output_dir=tmp_path / "out",
        task_runner=runner,
    )
    assert result.primary_metric_stdev > 0.0
    assert result.primary_metric_min < result.primary_metric_max


# ── persist_eval_result + latest_eval_result_path ──────────────


def test_latest_eval_result_path_returns_none_when_dir_missing(tmp_path):
    assert latest_eval_result_path(tmp_path / "nope") is None


def test_latest_eval_result_path_returns_lex_max(tmp_path):
    out = tmp_path / "results"
    out.mkdir()
    (out / "a-2026-01-01.json").write_text("{}")
    (out / "b-2026-02-01.json").write_text("{}")
    latest = latest_eval_result_path(out)
    assert latest is not None and latest.name == "b-2026-02-01.json"


def test_persist_eval_result_writes_under_label_filename(tmp_path):
    from eval_metrics import SuiteSummary, summarize_eval

    result = summarize_eval(
        label="hello world/slash",
        timestamp="2026-01-01T00:00:00+00:00",
        suites=[SuiteSummary(0.5, 0.5, 10, 5)],
    )
    out = tmp_path / EVAL_RESULTS_DIRNAME
    p = persist_eval_result(result, out)
    assert p.exists()
    assert "hello_world_slash" in p.name
    assert "/" not in p.name
