from __future__ import annotations

import json
import math
from pathlib import Path
from types import SimpleNamespace

import autoresearch_cli

REPO_ROOT = Path(__file__).resolve().parents[1]


def _base_args(tmp_path, **overrides):
    args = {
        "db": str(tmp_path / "cli.db"),
        "commit": "abcdef123456",
        "metric": 1.2,
        "metrics": json.dumps({"trade_count": 2}),
        "status": "keep",
        "description": "cli description",
        "asi": None,
        "direction": None,
    }
    args.update(overrides)
    return SimpleNamespace(**args)


def test_cli_docs_use_db_argument_not_removed_session_path() -> None:
    for doc_name in ("AGENTS.md", "CLAUDE.md"):
        body = (REPO_ROOT / doc_name).read_text()
        assert "python autoresearch_cli.py init   --db <path>" in body
        assert "--session-path" not in body


def test_cli_add_result_persists_timestamp_and_exports(monkeypatch, tmp_path) -> None:
    captured: list[object] = []

    class _FakeDB:
        def add(self, record):
            captured.append(record)

    monkeypatch.setattr(autoresearch_cli, "_db", lambda path: _FakeDB())
    monkeypatch.setattr(
        autoresearch_cli,
        "read_session",
        lambda path: (
            {"bestDirection": "lower", "_segment": 0, "metricName": "median_expectancy"},
            [],
        ),
    )
    monkeypatch.setattr(autoresearch_cli, "compute_confidence", lambda *args, **kwargs: None)
    monkeypatch.setattr(autoresearch_cli, "find_baseline", lambda *args, **kwargs: None)
    monkeypatch.setattr(autoresearch_cli, "find_best_kept", lambda *args, **kwargs: None)

    autoresearch_cli.cmd_log(_base_args(tmp_path))

    assert captured
    assert isinstance(captured[0].timestamp, str)
    assert captured[0].timestamp.endswith("+00:00") or captured[0].timestamp.endswith("Z")


def test_cli_add_result_preserves_description_and_asi_round_trip(tmp_path) -> None:
    db_path = tmp_path / "cli.db"
    autoresearch_cli.cmd_init(
        SimpleNamespace(
            db=str(db_path),
            name="sess",
            metric_name="median_expectancy",
            metric_unit="",
            direction="lower",
        )
    )

    autoresearch_cli.cmd_log(
        _base_args(
            tmp_path,
            asi=json.dumps({"note": "hello"}),
            description="cli description",
        )
    )

    config, results = autoresearch_cli.read_session(str(db_path))

    assert config is not None
    assert results[-1]["description"] == "cli description"
    assert results[-1]["asi"] == {"note": "hello"}


def test_read_session_falls_back_to_configured_primary_metric(tmp_path) -> None:
    class _FakeRecord:
        def __init__(self) -> None:
            self.code_commit = "abcdef123456"
            self.validation_metrics = {"trade_count": 1}
            self.train_metrics = {"calmar": 2.5}
            self.accepted = True
            self.config_path = "configs/ema_base.yaml"
            self.timestamp = "2026-05-01T00:00:00+00:00"

    class _FakeDB:
        def session_meta(self):
            return {"name": "sess", "metricName": "calmar", "bestDirection": "higher"}

        def all(self):
            return [_FakeRecord()]

    original_db = autoresearch_cli._db
    autoresearch_cli._db = lambda path: _FakeDB()
    try:
        config, results = autoresearch_cli.read_session(str(tmp_path / "cli.db"))
    finally:
        autoresearch_cli._db = original_db

    assert config is not None
    assert results[0]["metric"] == 2.5


def test_read_session_relaxes_infinity_metric_sentinels(tmp_path) -> None:
    db_path = tmp_path / "cli.db"
    autoresearch_cli.cmd_init(
        SimpleNamespace(
            db=str(db_path),
            name="sess",
            metric_name="median_expectancy",
            metric_unit="",
            direction="lower",
        )
    )

    autoresearch_cli.cmd_log(
        _base_args(
            tmp_path,
            metric=1.2,
            metrics=json.dumps({"trade_count": 2, "profit_factor": float("inf")}),
        )
    )

    config, results = autoresearch_cli.read_session(str(db_path))

    assert config is not None
    assert math.isinf(results[-1]["metrics"]["profit_factor"])
    assert math.isinf(results[-1]["metric"]) is False


def test_read_session_coerces_string_metric_values_before_stats(tmp_path) -> None:
    class _FakeRecord:
        def __init__(self) -> None:
            self.code_commit = "abcdef123456"
            self.validation_metrics = {"trade_count": 2, "profit_factor": "Infinity"}
            self.train_metrics = {"median_expectancy": "1.2"}
            self.accepted = True
            self.config_path = "configs/ema_base.yaml"
            self.timestamp = "2026-05-01T00:00:00+00:00"

    class _FakeDB:
        def session_meta(self):
            return {"name": "sess", "metricName": "median_expectancy", "bestDirection": "lower"}

        def all(self):
            return [_FakeRecord()]

    original_db = autoresearch_cli._db
    autoresearch_cli._db = lambda path: _FakeDB()
    try:
        config, results = autoresearch_cli.read_session(str(tmp_path / "cli.db"))
    finally:
        autoresearch_cli._db = original_db

    assert config is not None
    assert results[0]["metric"] == 1.2
    assert math.isinf(results[0]["metrics"]["profit_factor"])


def test_compute_confidence_requires_three_numeric_metrics() -> None:
    results = [
        {"segment": 0, "status": "keep", "metric": 1.0},
        {"segment": 0, "status": "keep", "metric": None},
        {"segment": 0, "status": "keep", "metric": 2.0},
    ]

    confidence = autoresearch_cli.compute_confidence(results, 0, "higher")

    assert confidence is None


def test_compute_confidence_ignores_non_finite_metrics() -> None:
    results = [
        {"segment": 0, "status": "keep", "metric": 1.0},
        {"segment": 0, "status": "keep", "metric": float("inf")},
        {"segment": 0, "status": "keep", "metric": 2.0},
    ]

    confidence = autoresearch_cli.compute_confidence(results, 0, "higher")

    assert confidence is None


def test_find_best_kept_ignores_non_finite_metrics() -> None:
    results = [
        {"segment": 0, "status": "keep", "metric": float("nan")},
        {"segment": 0, "status": "keep", "metric": 1.0},
        {"segment": 0, "status": "keep", "metric": 2.0},
    ]

    best = autoresearch_cli.find_best_kept(results, 0, "higher")

    assert best == 2.0


def test_find_baseline_rejects_non_finite_metric() -> None:
    results = [{"segment": 0, "status": "keep", "metric": float("nan")}]

    baseline = autoresearch_cli.find_baseline(results, 0)

    assert baseline is None


def test_compute_confidence_returns_none_when_baseline_is_unusable() -> None:
    results = [
        {"segment": 0, "status": "keep", "metric": None},
        {"segment": 0, "status": "keep", "metric": 1.0},
        {"segment": 0, "status": "keep", "metric": 2.0},
        {"segment": 0, "status": "keep", "metric": 3.0},
    ]

    confidence = autoresearch_cli.compute_confidence(results, 0, "higher")

    assert confidence is None


def test_cli_log_persists_primary_metric_even_when_extra_metrics_are_sparse(monkeypatch, tmp_path):
    captured: list[object] = []

    class _FakeDB:
        def add(self, record):
            captured.append(record)

    monkeypatch.setattr(autoresearch_cli, "_db", lambda path: _FakeDB())
    monkeypatch.setattr(
        autoresearch_cli,
        "read_session",
        lambda path: (
            {"bestDirection": "lower", "_segment": 0, "metricName": "calmar"},
            [],
        ),
    )
    monkeypatch.setattr(autoresearch_cli, "compute_confidence", lambda *args, **kwargs: None)
    monkeypatch.setattr(autoresearch_cli, "find_baseline", lambda *args, **kwargs: None)
    monkeypatch.setattr(autoresearch_cli, "find_best_kept", lambda *args, **kwargs: None)

    autoresearch_cli.cmd_log(
        _base_args(tmp_path, metric=3.14, metrics=json.dumps({"trade_count": 2}))
    )

    assert captured
    record = captured[0]
    assert record.validation_metrics["calmar"] == 3.14
    assert record.train_metrics["calmar"] == 3.14
