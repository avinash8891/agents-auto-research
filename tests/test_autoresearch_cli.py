from __future__ import annotations

import json
from types import SimpleNamespace

import autoresearch_cli


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
