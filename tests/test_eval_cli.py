"""Unit tests for eval_cli.

The CLI is thin: it wires args through to ``run_eval`` and logs delta
vs. the prior result. Tests substitute the ``task_runner`` indirectly
by monkeypatching ``run_eval`` so we don't need a real conductor.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

import eval_cli
from eval_harness import HoldoutTask
from eval_metrics import TaskOutcome


def _write_holdout(tmp_path: Path) -> Path:
    p = tmp_path / "holdout.yaml"
    p.write_text(
        yaml.safe_dump({"tasks": [{"family": "ema", "dataset_window": "w1"}]}),
        encoding="utf-8",
    )
    return p


def test_main_returns_zero_and_writes_result(tmp_path, monkeypatch):
    holdout = _write_holdout(tmp_path)
    output = tmp_path / "out"

    # Patch eval_harness's default task_runner so we don't try to spin
    # up the real controller.
    import eval_harness as eh

    def fake_runner(task: HoldoutTask, root: Path) -> TaskOutcome:
        return TaskOutcome(
            family=task.family,
            dataset_window=task.dataset_window,
            outcome="compiled",
            overall_score=1.0,
        )

    monkeypatch.setattr(eh, "_default_task_runner", fake_runner)

    rc = eval_cli.main(
        [
            "--label",
            "baseline",
            "--repeat",
            "2",
            "--holdout-path",
            str(holdout),
            "--output-dir",
            str(output),
        ]
    )
    assert rc == 0
    written = list(output.glob("*.json"))
    assert len(written) == 1
    payload = json.loads(written[0].read_text())
    assert payload["label"] == "baseline"
    assert payload["repeat"] == 2


def test_load_prior_result_returns_prior_when_present(tmp_path):
    output = tmp_path / "out"
    output.mkdir()
    prior_payload = {
        "label": "prior",
        "timestamp": "2026-01-01T00:00:00+00:00",
        "repeat": 1,
        "primary_metric_name": "compiled_rate",
        "primary_metric": {"mean": 0.5, "stdev": 0.1, "min": 0.5, "max": 0.5},
        "secondary_quality_p50_mean": None,
        "suites": [{"compiled_rate": 0.5, "quality_score_p50": 0.5, "n_tasks": 2, "n_compiled": 1}],
    }
    (output / "prior-2026-01-01T000000p0000.json").write_text(json.dumps(prior_payload))
    # A fictitious "current" path that doesn't exist on disk — just used
    # as the exclusion key.
    current_path = output / "nonexistent-current.json"
    prior = eval_cli._load_prior_result(output, current_path)
    assert prior is not None
    assert prior.label == "prior"
    assert prior.primary_metric_mean == 0.5


def test_load_prior_result_returns_none_when_only_current_present(tmp_path):
    output = tmp_path / "out"
    output.mkdir()
    only = output / "only.json"
    only.write_text("{}")
    assert eval_cli._load_prior_result(output, only) is None


def test_main_runs_with_planted_prior_without_error(tmp_path, monkeypatch):
    """End-to-end: prior file present + new run completes, returns 0.

    The actual delta-logging output is verified by the unit test on
    ``_load_prior_result`` above; here we just ensure the CLI path
    doesn't crash when there's something to compare against.
    """
    holdout = _write_holdout(tmp_path)
    output = tmp_path / "out"
    output.mkdir()
    prior_payload = {
        "label": "prior",
        "timestamp": "2026-01-01T00:00:00+00:00",
        "repeat": 1,
        "primary_metric_name": "compiled_rate",
        "primary_metric": {"mean": 0.5, "stdev": 0.1, "min": 0.5, "max": 0.5},
        "secondary_quality_p50_mean": None,
        "suites": [{"compiled_rate": 0.5, "quality_score_p50": 0.5, "n_tasks": 2, "n_compiled": 1}],
    }
    (output / "prior-2026-01-01T000000p0000.json").write_text(json.dumps(prior_payload))

    import eval_harness as eh

    def fake_runner(task: HoldoutTask, root: Path) -> TaskOutcome:
        return TaskOutcome(
            family=task.family,
            dataset_window=task.dataset_window,
            outcome="compiled",
            overall_score=1.0,
        )

    monkeypatch.setattr(eh, "_default_task_runner", fake_runner)

    rc = eval_cli.main(
        [
            "--label",
            "current",
            "--repeat",
            "1",
            "--holdout-path",
            str(holdout),
            "--output-dir",
            str(output),
        ]
    )
    assert rc == 0
    files = list(output.glob("*.json"))
    assert len(files) == 2


def test_main_no_prior_completes(tmp_path, monkeypatch):
    """Cold-start: no prior file, CLI runs and writes one result."""
    holdout = _write_holdout(tmp_path)
    output = tmp_path / "out"

    import eval_harness as eh

    def fake_runner(task, root):
        return TaskOutcome(
            family=task.family,
            dataset_window=task.dataset_window,
            outcome="compiled",
            overall_score=1.0,
        )

    monkeypatch.setattr(eh, "_default_task_runner", fake_runner)

    rc = eval_cli.main(
        [
            "--label",
            "first",
            "--repeat",
            "1",
            "--holdout-path",
            str(holdout),
            "--output-dir",
            str(output),
        ]
    )
    assert rc == 0
    files = list(output.glob("*.json"))
    assert len(files) == 1


def test_parser_unknown_metric_rejected():
    with pytest.raises(SystemExit):
        eval_cli._build_parser().parse_args(["--label", "x", "--primary-metric", "nope"])


def test_load_prior_result_skips_deleted_file(tmp_path, monkeypatch):
    """Verify _load_prior_result doesn't crash when a file disappears between glob and stat."""
    ghost = tmp_path / "ghost-2026-01-01.json"
    # ghost was never written — it's absent from disk.
    # Monkeypatch glob to return it as a candidate (simulates TOCTOU: file existed at glob time).
    monkeypatch.setattr(
        type(tmp_path),
        "glob",
        lambda self, pattern: (
            iter([ghost]) if self == tmp_path and pattern == "*.json" else iter([])
        ),
    )
    # safe_stat_mtime returns 0.0 for the ghost (OSError suppressed), max selects it as sole
    # candidate, then read_text raises OSError — the guarded try/except returns None.
    result = eval_cli._load_prior_result(tmp_path, current_path=None)
    assert result is None


def test_load_prior_result_returns_none_for_malformed_json(tmp_path):
    output = tmp_path / "out"
    output.mkdir()
    (output / "bad.json").write_text("{not json", encoding="utf-8")

    assert eval_cli._load_prior_result(output, current_path=None) is None


def test_load_prior_result_returns_none_for_invalid_schema(tmp_path):
    output = tmp_path / "out"
    output.mkdir()
    payload = {
        "label": "bad",
        "timestamp": "2026-01-01T00:00:00+00:00",
        "repeat": 1,
        "primary_metric_name": "compiled_rate",
        "primary_metric": {"mean": 0.5, "stdev": 0.0, "min": 0.5, "max": 0.5},
        "suites": [
            {"compiled_rate": 2.0, "quality_score_p50": None, "n_tasks": 1, "n_compiled": 1}
        ],
    }
    (output / "bad-schema.json").write_text(json.dumps(payload), encoding="utf-8")

    assert eval_cli._load_prior_result(output, current_path=None) is None


def test_load_prior_result_returns_none_for_non_object_json(tmp_path):
    output = tmp_path / "out"
    output.mkdir()
    (output / "bad.json").write_text("[]", encoding="utf-8")

    assert eval_cli._load_prior_result(output, current_path=None) is None
