from __future__ import annotations

import json
from types import SimpleNamespace

import autoresearch_cli


def test_cli_add_result_persists_iso8601_timestamp(monkeypatch, tmp_path) -> None:
    captured: list[object] = []

    class _FakeDB:
        def add(self, record):
            captured.append(record)

    monkeypatch.setattr(autoresearch_cli, "_db", lambda path: _FakeDB())
    monkeypatch.setattr(
        autoresearch_cli,
        "read_session",
        lambda path: ({"bestDirection": "lower", "_segment": 0, "metricName": "median_expectancy"}, []),
    )
    monkeypatch.setattr(autoresearch_cli, "compute_confidence", lambda *args, **kwargs: None)
    monkeypatch.setattr(autoresearch_cli, "find_baseline", lambda *args, **kwargs: None)
    monkeypatch.setattr(autoresearch_cli, "find_best_kept", lambda *args, **kwargs: None)

    args = SimpleNamespace(
        db=str(tmp_path / "cli.db"),
        commit="abcdef123456",
        metric=1.2,
        metrics=json.dumps({"trade_count": 2}),
        status="keep",
        description="cli description",
        asi=None,
        direction=None,
    )

    autoresearch_cli.cmd_log(args)

    assert captured
    assert isinstance(captured[0].timestamp, str)
    assert captured[0].timestamp.endswith("+00:00") or captured[0].timestamp.endswith("Z")
