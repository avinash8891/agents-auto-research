"""Unit tests for autoresearch_research helpers.

The orchestrator functions (execute_research_sdk, run_research) are exercised
end-to-end by the characterization tests; this module covers the pure helpers
(notify_discord, accumulate_job_usage, log_research_round, results_to_dicts).

Project rule G: real outcome strings ("compiled", "needs_code", "rejected",
"stopped"), real status keys, behavioral assertions on token totals and
JSONL shape.
"""

from __future__ import annotations

import json
from pathlib import Path

from autoresearch_research import (
    accumulate_job_usage,
    log_research_round,
    notify_discord,
    results_to_dicts,
)
from autoresearch_state import ExperimentRecord, write_state

# ── notify_discord fail-open contract ────────────────────────────


def test_notify_discord_no_op_when_webhook_empty() -> None:
    # Should not raise even with empty webhook (the fail-open contract).
    notify_discord("title", "body", webhook="")


def test_notify_discord_swallows_exceptions(monkeypatch) -> None:
    # If urllib raises, notify_discord must NOT propagate (fail-open).
    import urllib.request

    def boom(*a, **kw):
        raise RuntimeError("simulated network failure")

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
                "cost_usd": 0.01,
            }
        },
    )
    state = json.loads(state_path.read_text())
    usage = state["job_usage"]
    assert usage["input_tokens"] == 100
    assert usage["output_tokens"] == 50
    assert usage["total_tokens"] == 150
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
                "cost_usd": 0.02,
            }
        },
    )
    usage = json.loads(state_path.read_text())["job_usage"]
    assert usage["input_tokens"] == 300
    assert usage["output_tokens"] == 125
    assert usage["total_tokens"] == 425
    assert usage["cost_usd"] == 0.03
    assert usage["rounds"] == 2


def test_accumulate_job_usage_handles_missing_total_block(tmp_path: Path) -> None:
    state_path = tmp_path / "state.json"
    write_state(state_path, {"state": "running"})
    accumulate_job_usage(state_path, {})  # no "total" key at all
    usage = json.loads(state_path.read_text())["job_usage"]
    assert usage["rounds"] == 1
    assert usage["total_tokens"] == 0


# ── log_research_round JSONL shape ──────────────────────────────


def test_log_research_round_appends_entry_with_required_fields(tmp_path: Path) -> None:
    jsonl = tmp_path / "log.jsonl"
    state_path = tmp_path / "state.json"
    write_state(state_path, {"state": "running", "job": 5})

    log_research_round(
        jsonl,
        state_path,
        round_number=3,
        thesis_id="trailing_stop",
        outcome="compiled",
        config_changes={"trailing_stop": 0.5},
        hypothesis="add a trailing stop",
        mechanism="protect winners",
        mechanism_dimension="exit",
        rejection_reason="",
        usage={"total": {"total_tokens": 1234}},
    )
    rows = [json.loads(line) for line in jsonl.read_text().splitlines() if line]
    assert len(rows) == 1
    row = rows[0]
    assert row["type"] == "research_round"
    assert row["job"] == 5
    assert row["round"] == 3
    assert row["thesis_id"] == "trailing_stop"
    assert row["outcome"] == "compiled"
    assert row["config_changes"] == {"trailing_stop": 0.5}
    assert row["hypothesis"] == "add a trailing stop"
    assert row["usage"] == {"total": {"total_tokens": 1234}}
    assert "timestamp" in row
    assert isinstance(row["timestamp"], int)


def test_log_research_round_appends_to_existing_jsonl(tmp_path: Path) -> None:
    jsonl = tmp_path / "log.jsonl"
    state_path = tmp_path / "state.json"
    write_state(state_path, {"state": "running", "job": 1})

    # Pre-existing entry should be preserved.
    jsonl.write_text(
        json.dumps(
            {"run": 1, "metric": 1.0, "status": "keep", "asi": {"config": "configs/ema_base.yaml"}}
        )
        + "\n"
    )
    log_research_round(
        jsonl,
        state_path,
        round_number=1,
        thesis_id="x",
        outcome="rejected",
        rejection_reason="overlap with prior thesis",
    )
    rows = [json.loads(line) for line in jsonl.read_text().splitlines() if line]
    assert len(rows) == 2
    assert rows[0].get("type") != "research_round"
    assert rows[1]["outcome"] == "rejected"


def test_log_research_round_uses_real_outcome_strings(tmp_path: Path) -> None:
    """Verify each known outcome string round-trips. Project rule G: realistic
    production names."""
    jsonl = tmp_path / "log.jsonl"
    state_path = tmp_path / "state.json"
    write_state(state_path, {"state": "running", "job": 1})

    for outcome in ("compiled", "needs_code", "rejected", "stopped", "conductor_error"):
        log_research_round(jsonl, state_path, round_number=1, thesis_id="t", outcome=outcome)

    rows = [json.loads(line) for line in jsonl.read_text().splitlines() if line]
    outcomes = [r["outcome"] for r in rows]
    assert outcomes == ["compiled", "needs_code", "rejected", "stopped", "conductor_error"]


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
