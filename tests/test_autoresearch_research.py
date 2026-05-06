"""Unit tests for autoresearch_research helpers.

The orchestrator functions (execute_research_sdk, run_research) are exercised
end-to-end by the characterization tests; this module covers the pure helpers
(notify_discord, accumulate_job_usage, log_research_round, results_to_dicts).

Project rule G: real outcome strings ("compiled", "needs_code", "rejected",
"stopped"), real status keys, behavioral assertions on token totals and
exported entry shape.
"""

from __future__ import annotations

import json
from pathlib import Path

from autoresearch_research import (
    _check_parsed_for_terminal,
    accumulate_job_usage,
    log_research_round,
    notify_discord,
    results_to_dicts,
)
from autoresearch_state import ExperimentRecord, write_state
from experiment_db import ExperimentDB

# ── notify_discord fail-open contract ────────────────────────────


def test_notify_discord_no_op_when_webhook_empty() -> None:
    # Should not raise even with empty webhook (the fail-open contract).
    notify_discord("title", "body", webhook="")


def test_notify_discord_swallows_exceptions(monkeypatch) -> None:
    # If urllib raises a network error, notify_discord must NOT propagate (fail-open).
    import urllib.error
    import urllib.request

    def boom(*a, **kw):
        raise urllib.error.URLError("simulated network failure")

    monkeypatch.setattr(urllib.request, "urlopen", boom)
    # Must complete cleanly even though urllib raised.
    notify_discord("title", "body", webhook="https://example.invalid/hook")


# ── accumulate_job_usage ────────────────────────────────────────


def test_accumulate_job_usage_initializes_state_when_absent(tmp_path: Path) -> None:
    state_path = tmp_path / "state.json"
    write_state(state_path, {"state": "running"})
    accumulate_job_usage(
        state_path,
        {
            "total": {
                "input_tokens": 100,
                "output_tokens": 50,
                "total_tokens": 150,
                "cached_input_tokens": 25,
                "estimated_input_tokens": 10,
                "estimated_output_tokens": 5,
                "estimated_total_tokens": 15,
                "cost_usd": 0.01,
            }
        },
    )
    state = json.loads(state_path.read_text())
    usage = state["job_usage"]
    assert usage["input_tokens"] == 100
    assert usage["output_tokens"] == 50
    assert usage["total_tokens"] == 150
    assert usage["cached_input_tokens"] == 25
    assert usage["estimated_input_tokens"] == 10
    assert usage["estimated_output_tokens"] == 5
    assert usage["estimated_total_tokens"] == 15
    assert usage["cost_usd"] == 0.01
    assert usage["rounds"] == 1


def test_accumulate_job_usage_sums_across_rounds(tmp_path: Path) -> None:
    state_path = tmp_path / "state.json"
    write_state(state_path, {"state": "running"})
    accumulate_job_usage(
        state_path,
        {
            "total": {
                "input_tokens": 100,
                "output_tokens": 50,
                "total_tokens": 150,
                "cached_input_tokens": 25,
                "estimated_total_tokens": 15,
                "cost_usd": 0.01,
            }
        },
    )
    accumulate_job_usage(
        state_path,
        {
            "total": {
                "input_tokens": 200,
                "output_tokens": 75,
                "total_tokens": 275,
                "cached_input_tokens": 40,
                "estimated_total_tokens": 30,
                "cost_usd": 0.02,
            }
        },
    )
    usage = json.loads(state_path.read_text())["job_usage"]
    assert usage["input_tokens"] == 300
    assert usage["output_tokens"] == 125
    assert usage["total_tokens"] == 425
    assert usage["cached_input_tokens"] == 65
    assert usage["estimated_total_tokens"] == 45
    assert usage["cost_usd"] == 0.03
    assert usage["rounds"] == 2


def test_accumulate_job_usage_handles_missing_total_block(tmp_path: Path) -> None:
    state_path = tmp_path / "state.json"
    write_state(state_path, {"state": "running"})
    accumulate_job_usage(state_path, {})  # no "total" key at all
    usage = json.loads(state_path.read_text())["job_usage"]
    assert usage["rounds"] == 1
    assert usage["total_tokens"] == 0
    assert usage["cached_input_tokens"] == 0
    assert usage["estimated_total_tokens"] == 0


# ── log_research_round SQLite persistence ───────────────────────


def test_check_parsed_for_terminal_preserves_conductor_validation_reason() -> None:
    result = _check_parsed_for_terminal(
        {
            "status": "conductor_error",
            "error": "validation_failed",
            "validation_reason": "expected exactly one thesis, got 2",
            "suggested_theses": [],
            "should_stop": False,
        }
    )

    assert result == {
        "status": "conductor_error",
        "generated_config": None,
        "should_stop": False,
        "rejection_reason": (
            "research conductor failed: validation_failed: expected exactly one thesis, got 2"
        ),
        "validation_reason": "expected exactly one thesis, got 2",
    }


def test_log_research_round_persists_required_fields_to_sqlite(tmp_path: Path) -> None:
    db = ExperimentDB(tmp_path / "experiments.db")
    state_path = tmp_path / "state.json"
    write_state(state_path, {"state": "running", "job": 5})

    db.log_research_round(
        state_path,
        round_number=3,
        thesis_id="trailing_stop",
        outcome="compiled",
        usage={"total": {"total_tokens": 1234}},
    )
    rows = db.list_research_rounds()
    assert len(rows) == 1
    row = rows[0]
    assert row["job_id"] == 5
    assert row["round_number"] == 3
    assert row["selected_thesis_id"] == "trailing_stop"
    assert row["outcome"] == "compiled"
    assert row["usage_json"] == {"total": {"total_tokens": 1234}}
    assert row["created_at_utc"].endswith("+00:00") or row["created_at_utc"].endswith("Z")


def test_log_research_round_persists_explicit_hypothesis_id_without_trace_context(
    tmp_path: Path,
) -> None:
    db = ExperimentDB(tmp_path / "experiments.db")
    state_path = tmp_path / "state.json"
    write_state(state_path, {"state": "running", "job": 5})

    db.log_research_round(
        state_path,
        round_number=3,
        thesis_id="trailing_stop",
        hypothesis_id="hyp-123",
        outcome="compiled",
        usage={"total": {"total_tokens": 1234}},
    )
    rows = db.list_research_rounds()

    assert rows[0]["hypothesis_id"] == "hyp-123"


def test_log_research_round_persists_full_thesis_details_to_attempt(tmp_path: Path) -> None:
    db_path = tmp_path / "experiments.db"
    ExperimentDB(db_path)
    state_path = tmp_path / "state.json"
    write_state(state_path, {"state": "running", "job": 5, "family": "ema"})
    details = {
        "dimension_novelty": "new opening-liquidity mechanism",
        "evidence": ["web", "analyst"],
        "expected_effects": [{"metric": "profit_factor", "direction": "increase"}],
        "disqualifiers": [{"name": "trade_count_collapse", "condition": "trades < 100"}],
        "why_not_overfit": "market microstructure mechanism",
        "requires_code_change": True,
        "required_diagnostics": ["margin_per_order"],
    }

    log_research_round(
        db_path,
        state_path,
        round_number=4,
        thesis_id="opening_liquidity",
        outcome="compiled",
        config_changes={"requires_engine_change": True},
        hypothesis="opening liquidity imbalance",
        mechanism="auction imbalance decay",
        mechanism_dimension="market_microstructure",
        thesis_details=details,
    )

    attempts = ExperimentDB(db_path).list_research_thesis_attempts(
        job_id=5, thesis_id="opening_liquidity"
    )

    assert attempts[0]["thesis_details"] == details


# ── results_to_dicts ────────────────────────────────────────────


def test_results_to_dicts_copies_core_fields() -> None:
    record = ExperimentRecord(
        config="configs/ema_base.yaml",
        metric=1.42,
        status="keep",
        description="strict-native loop: ema_base",
        timestamp=100,
        asi={},
    )
    out = results_to_dicts([record])
    assert out == [
        {
            "config": "configs/ema_base.yaml",
            "metric": 1.42,
            "status": "keep",
            "description": "strict-native loop: ema_base",
            "job": 0,
        }
    ]


def test_results_to_dicts_includes_trade_analysis_subkeys_when_present() -> None:
    record = ExperimentRecord(
        config="configs/variants/ema_aggressive.yaml",
        metric=1.5,
        status="keep",
        description="",
        timestamp=200,
        asi={
            "trade_analysis": {
                "trade_count": 287,
                "profit_factor": 1.61,
                "max_drawdown": 0.18,
                "exit_mix": {"target": 102, "stop": 78, "close": 107},
                "regime_insight": "best in trending sessions",
            },
            "thesis_id": "ema_aggressive",
            "config_changes": {"ema_length": 3},
            "insights": ["metric=1.5", "decision=keep"],
            "next_thesis_suggestion": "test ema_length=4",
        },
    )
    out = results_to_dicts([record])[0]
    assert out["trade_count"] == 287
    assert out["profit_factor"] == 1.61
    assert out["max_drawdown"] == 0.18
    assert out["exit_mix"] == {"target": 102, "stop": 78, "close": 107}
    assert out["regime_insight"] == "best in trending sessions"
    assert out["thesis_id"] == "ema_aggressive"
    assert out["config_changes"] == {"ema_length": 3}
    assert out["insights"] == ["metric=1.5", "decision=keep"]
    assert out["next_thesis_suggestion"] == "test ema_length=4"


def test_results_to_dicts_uses_insight_brief_from_either_layer() -> None:
    """insight_brief on asi takes precedence; falls back to trade_analysis."""
    record_top = ExperimentRecord(
        "c.yaml",
        1.0,
        "keep",
        "",
        100,
        asi={"insight_brief": "from-top", "trade_analysis": {"insight_brief": "from-ta"}},
    )
    record_ta = ExperimentRecord(
        "c.yaml",
        1.0,
        "keep",
        "",
        100,
        asi={"trade_analysis": {"insight_brief": "from-ta-only"}},
    )
    assert results_to_dicts([record_top])[0]["insight_brief"] == "from-top"
    assert results_to_dicts([record_ta])[0]["insight_brief"] == "from-ta-only"


def test_results_to_dicts_handles_empty_input() -> None:
    assert results_to_dicts([]) == []
