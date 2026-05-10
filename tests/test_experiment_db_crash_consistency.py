"""Regression tests for controller persistence through BacktestRunDB."""

from pathlib import Path

from autoresearch_controller import AutoresearchController
from strategy_family import load_family

REPO_ROOT = Path(__file__).parent.parent


def test_controller_write_entries_persists_experiment_to_sqlite(tmp_path, monkeypatch):
    """Controller write_entries uses BacktestRunDB as the durable source of truth."""
    family = load_family("ema")
    state_path = tmp_path / "ema_autoresearch.next.json"

    monkeypatch.setattr("autoresearch_research.notify_discord", lambda *a, **k: None)

    controller = AutoresearchController(
        root=tmp_path,
        state_path=state_path,
        current_md_path=tmp_path / "ema_autoresearch.current.md",
        family=family,
    )

    controller.write_entries(
        [
            {"type": "config", "bestDirection": "higher", "primaryMetric": "median_expectancy"},
            {
                "run": 1,
                "run_id": "test-run-id-001",
                "commit": "abc123",
                "metric": 0.05,
                "status": "keep",
                "description": "strict-native loop: ema_base",
                "timestamp": "2024-01-01T00:00:00+00:00",
                "segment": 0,
                "confidence": None,
                "asi": {
                    "config": "configs/ema_base.yaml",
                    "thesis_id": "ema_base",
                    "trade_analysis": {},
                    "hypothesis": "ema crossover",
                },
            },
        ],
    )

    all_records = controller.backtest_run_db.all()
    assert len(all_records) == 1, f"expected 1 record, got {len(all_records)}"
    assert all_records[0].run_id == "test-run-id-001"


def test_controller_write_entries_replaces_existing_experiment_without_duplicates(
    tmp_path, monkeypatch
):
    """Repeated imports of the same BacktestRunDB entry do not create duplicates."""
    family = load_family("ema")
    state_path = tmp_path / "ema_autoresearch.next.json"

    monkeypatch.setattr("autoresearch_research.notify_discord", lambda *a, **k: None)

    controller = AutoresearchController(
        root=tmp_path,
        state_path=state_path,
        current_md_path=tmp_path / "ema_autoresearch.current.md",
        family=family,
    )

    entries = [
        {"type": "config", "bestDirection": "higher", "primaryMetric": "median_expectancy"},
        {
            "run": 1,
            "run_id": "test-run-id-002",
            "commit": "abc123",
            "metric": 0.05,
            "status": "keep",
            "description": "strict-native loop: ema_base",
            "timestamp": "2024-01-01T00:00:00+00:00",
            "segment": 0,
            "confidence": None,
            "asi": {
                "config": "configs/ema_base.yaml",
                "thesis_id": "ema_base",
                "trade_analysis": {},
                "hypothesis": "",
            },
        },
    ]

    controller.write_entries(entries)
    controller.write_entries(entries)

    assert len(controller.backtest_run_db.all()) == 1, "reimport must not create duplicate"
