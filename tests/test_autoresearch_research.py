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
import sqlite3
from pathlib import Path

import pandas as pd
import pytest

from autoresearch_artifacts import round_number_from_path
from autoresearch_constants import MAX_RESEARCH_ROUNDS
from autoresearch_controller import AutoresearchController
from autoresearch_research import (
    _check_parsed_for_terminal,  # keep for backward-compat callers in integration tests
)
from autoresearch_research import (
    _attempt_context_from_result,
    _exhausted_retries_result,
    _handle_needs_code,
    _handle_round_failure,
    _handle_success,
    _mechanism_proposal_to_research_thesis,
    _on_ready_to_run,
    _quality_score_from_skill_delta,
    _record_round_quality_and_bridges,
    _research_feedback_from_verdict,
    _resolve_conductor_inputs,
    _round_number_from_artifact_path,
    _round_reflexio_facts,
    _screen_mechanism_proposal,
    _try_one_validation_attempt,
    accumulate_job_usage,
    execute_research_sdk,
    log_research_round,
    notify_discord,
    results_to_dicts,
    run_research,
)
from autoresearch_state import BacktestResultRecord, write_state
from backtest_run_db import BacktestRunDB
from causal_harvest import PendingCausalModelArtifact
from causal_model import load_model, save_model
from feature_table import feature_table_path
from research_types import CausalModel, ConductorResult
from screening import ScreeningResult, write_screenings
from strategies import STRATEGIES
from strategy_family import load_family
from thesis_validator import ThesisValidationError

# ── notify_discord fail-open contract ────────────────────────────


def test_research_round_number_parser_reuses_artifact_helper() -> None:
    assert _round_number_from_artifact_path is round_number_from_path


def _real_controller(tmp_path: Path) -> AutoresearchController:
    family = load_family("ema")
    return AutoresearchController(
        root=tmp_path,
        runtime_root=tmp_path,
        family=family,
        state_path=tmp_path / "ema_autoresearch.next.json",
        current_md_path=tmp_path / "ema_autoresearch.current.md",
        jobs_root=tmp_path / "runtime" / "jobs",
    )


def _real_family_controller(tmp_path: Path, family_name: str) -> AutoresearchController:
    family = load_family(family_name)
    return AutoresearchController(
        root=tmp_path,
        runtime_root=tmp_path,
        family=family,
        state_path=tmp_path / f"{family_name}_autoresearch.next.json",
        current_md_path=tmp_path / f"{family_name}_autoresearch.current.md",
        jobs_root=tmp_path / "runtime" / "jobs",
    )


def _write_valid_ema_config(path: Path, **overrides: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    config = {**STRATEGIES["ema"].get_defaults(), **overrides}
    path.write_text(json.dumps(config) + "\n")


def _screening_result(rule: str, verdict: str) -> ScreeningResult:
    return ScreeningResult(
        rule=rule,
        verdict=verdict,
        sample_count=40,
        flagged_loss_rate=0.8,
        base_loss_rate=0.56,
        lift=0.24,
        p_value=0.004,
        overlap_with=None,
    )


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


def test_record_round_quality_sends_round_facts_only_to_reflexio(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_ema_base_config(tmp_path)
    controller = _real_controller(tmp_path)
    controller.write_state({"state": "running", "job": 4, "research_round": 1})
    emitted: list[tuple[str, dict]] = []
    monkeypatch.setattr(
        "autoresearch_research._round_reflexio_facts",
        lambda *_args, **_kwargs: {
            "registered_predictions": [
                {"metric": "profit_factor", "magnitude_gap": -0.4, "direction_passed": False}
            ]
        },
    )
    monkeypatch.setattr(
        "autoresearch_research.emit_halo_event",
        lambda **kwargs: emitted.append(("halo", kwargs["payload"])),
    )
    monkeypatch.setattr(
        "autoresearch_research.emit_recursive_improve_event",
        lambda **kwargs: emitted.append(("recursive", kwargs["payload"])),
    )
    monkeypatch.setattr(
        "autoresearch_research.emit_reflexio_event",
        lambda **kwargs: emitted.append(("reflexio", kwargs["payload"])),
    )

    _record_round_quality_and_bridges(
        controller,
        1,
        {"generated_config": "runtime/jobs/job-4/research/round-1/selected_config.json"},
        {"total": {"total_tokens": 1}},
    )

    by_name = dict(emitted)
    assert "round_facts" not in by_name["halo"]
    assert "round_facts" not in by_name["recursive"]
    assert (
        by_name["reflexio"]["reflection"]["round_facts"]["registered_predictions"][0]["metric"]
        == "profit_factor"
    )


def test_round_reflexio_facts_scopes_screening_counts_to_active_job(tmp_path: Path) -> None:
    controller = _real_controller(tmp_path)
    controller.write_state({"state": "running", "job": 4, "research_round": 2})
    write_screenings(
        controller.backtest_run_db.path,
        [_screening_result("own_rule", "pass")],
        round_number=2,
        job_id=4,
    )
    write_screenings(
        controller.backtest_run_db.path,
        [_screening_result("other_rule", "kill_no_lift")],
        round_number=2,
        job_id=5,
    )

    facts = _round_reflexio_facts(controller, 2)

    assert facts["screening_verdict_counts"] == {"pass": 1}


def test_screen_mechanism_proposal_reads_feature_table_from_runtime_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    code_root = tmp_path / "code"
    runtime_root = tmp_path / "runtime-root"
    code_root.mkdir()
    runtime_root.mkdir()
    monkeypatch.setenv("AUTORESEARCH_RUNTIME_ROOT", str(runtime_root))
    family = load_family("ema")
    controller = AutoresearchController(
        root=code_root,
        runtime_root=runtime_root,
        family=family,
        state_path=runtime_root / "ema_autoresearch.next.json",
        current_md_path=runtime_root / "ema_autoresearch.current.md",
        jobs_root=runtime_root / "runtime" / "jobs",
    )
    completed_round_root = runtime_root / "runtime/jobs/job-9/research/round-0-baseline"
    _write_screening_feature_table(completed_round_root)
    save_model(
        CausalModel(
            family="ema",
            version=1,
            holdout_start="2023-01-01T00:00:00+00:00",
            factors=[],
            accuracy_history=[],
        ),
        runtime_root=runtime_root,
        code_root=code_root,
    )

    passed, reason, pending_model = _screen_mechanism_proposal(
        controller,
        1,
        {
            "rule": "gap_pct < 0",
            "competitor_rule": "gap_pct > 0",
            "story": "gap-down losses",
        },
        "thesis-001",
        job_id=9,
    )

    assert passed is True
    assert reason is None
    assert pending_model is not None


def test_screen_mechanism_proposal_uses_latest_completed_feature_table(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    controller = _real_controller(tmp_path)
    _write_ema_base_config(tmp_path)
    monkeypatch.delenv("AUTORESEARCH_RUNTIME_ROOT", raising=False)
    controller.write_state({"state": "blocked", "job": 12, "research_round": 0})
    completed_round_root = tmp_path / "runtime/jobs/job-12/research/round-0-baseline"
    proposed_round_root = tmp_path / "runtime/jobs/job-12/research/round-1"
    _write_screening_feature_table(completed_round_root)
    proposed_round_root.mkdir(parents=True)
    save_model(
        CausalModel(
            family="ema",
            version=1,
            holdout_start="2023-01-01T00:00:00+00:00",
            factors=[],
            accuracy_history=[],
        ),
        runtime_root=tmp_path,
        code_root=tmp_path,
    )

    passed, reason, pending_model = _screen_mechanism_proposal(
        controller,
        1,
        {
            "rule": "gap_pct < 0",
            "competitor_rule": "gap_pct > 0",
            "story": "gap-down losses",
        },
        "thesis-001",
        job_id=12,
    )

    assert passed is True
    assert reason is None
    assert pending_model is not None


def test_mechanism_proposal_rejects_undeclared_multi_key_change_before_compile(
    tmp_path: Path,
) -> None:
    controller = _real_controller(tmp_path)
    controller.write_state({"state": "blocked", "job": 12, "research_round": 0})
    round_root = tmp_path / "runtime" / "jobs" / "job-12" / "research" / "round-1"

    result, retry_feedback, stage = _try_one_validation_attempt(
        controller,
        1,
        0,
        ConductorResult(
            status="ok",
            thesis={
                "story": "Two independent EMA changes should be rejected as one proposal.",
                "rule": "gap_pct < 0",
                "competitor_rule": "gap_pct > 0",
                "competitor_story": "Gap-up trades may explain the residual instead.",
                "actionable": True,
                "proposed_change": {"ema_length": 8, "risk_reward": 2.5},
                "predictions": [
                    {"metric": "profit_factor", "direction": "increase", "predicted": 2.2},
                    {"metric": "trade_count", "direction": "decrease", "predicted": 80},
                ],
            },
            reasoning="invalid multi-key proposal",
        ),
        prior_theses=[],
    )

    assert result is None
    assert stage == "stage_1"
    assert retry_feedback is not None
    assert "exactly one top-level key" in retry_feedback
    assert not (round_root / "selected_config.json").exists()
    assert not (round_root / "pending_causal_model.json").exists()


def test_orb_mechanism_proposal_rejects_unsupported_config_key_before_compile(
    tmp_path: Path,
) -> None:
    controller = _real_family_controller(tmp_path, "orb")
    controller.write_state({"state": "blocked", "job": 12, "research_round": 0})
    round_root = tmp_path / "runtime" / "jobs" / "job-12" / "research" / "round-1"

    result, retry_feedback, stage = _try_one_validation_attempt(
        controller,
        1,
        0,
        ConductorResult(
            status="ok",
            thesis={
                "story": "Unsupported ORB typo should not dispatch a no-op backtest.",
                "rule": "gap_pct < 0",
                "competitor_rule": "gap_pct > 0",
                "competitor_story": "Gap-up trades may explain the residual instead.",
                "actionable": True,
                "proposed_change": {"unsupported_orb_filter": True},
                "predictions": [
                    {"metric": "profit_factor", "direction": "increase", "predicted": 2.2},
                    {"metric": "trade_count", "direction": "decrease", "predicted": 80},
                ],
            },
            reasoning="invalid unsupported key proposal",
        ),
        prior_theses=[],
    )

    assert result is None
    assert stage == "stage_1"
    assert retry_feedback is not None
    assert "unsupported config key" in retry_feedback
    assert not (round_root / "selected_config.json").exists()
    assert not (round_root / "pending_causal_model.json").exists()


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
    """A conductor_error without an attached thesis is unrecoverable and must
    short-circuit so the round records the failure with the original reason."""
    result = _check_parsed_for_terminal(
        ConductorResult(
            status="conductor_error",
            error="validation_failed",
            validation_reason="expected exactly one thesis, got 2",
        )
    )

    assert result == {
        "status": "conductor_error",
        "generated_config": None,
        "should_stop": False,
        "validation_failure_reason": (
            "research conductor failed: validation_failed: expected exactly one thesis, got 2"
        ),
        "validation_reason": "expected exactly one thesis, got 2",
    }


def test_check_parsed_for_terminal_defers_validation_failed_when_thesis_attached() -> None:
    """When the conductor parsed a thesis but the validator raised (e.g. process
    gate), the outer loop must re-run Stage 1 through _try_one_validation_attempt
    so the failure consumes the normal Stage 1 retry budget instead of aborting
    the round on a single missing-tool mistake."""
    result = _check_parsed_for_terminal(
        ConductorResult(
            status="conductor_error",
            error="validation_failed",
            validation_reason="Process gate failed: required tools not called: ['web_search']",
            thesis={"thesis_id": "open_alert_regime_filter"},
            tools_called=frozenset({"list_round_results"}),
        )
    )

    assert result is None


def test_exhausted_retries_result_preserves_assigned_attempt_id() -> None:
    result = _exhausted_retries_result(
        ConductorResult(
            status="ok",
            thesis={"proposal_label": "opening skip"},
            reasoning="candidate failed validation",
        ),
        "mechanism evidence missing",
        research_round_id="job-5-round-3",
        attempt_number=3,
    )

    assert result["generated_thesis_id"] == "job-5-round-3-attempt-3"
    assert result["research_round_id"] == "job-5-round-3"
    assert result["attempt_number"] == 3
    assert result["thesis"]["thesis_id"] == "job-5-round-3-attempt-3"


def test_attempt_context_fallback_prefers_result_round_over_stale_state_round() -> None:
    research_round_id, attempt_number = _attempt_context_from_result(
        {"job": 5, "research_round": 2},
        {"research_round": 3},
    )

    assert research_round_id == "job-5-round-3"
    assert attempt_number == 1


def test_log_research_round_persists_required_fields_to_sqlite(tmp_path: Path) -> None:
    db = BacktestRunDB(tmp_path / "backtest_runs.db")
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


def test_log_research_round_does_not_create_attempt_for_round_level_rejection(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "backtest_runs.db"
    BacktestRunDB(db_path)
    state_path = tmp_path / "state.json"
    write_state(state_path, {"state": "running", "job": 5, "family": "ema"})

    log_research_round(
        db_path,
        state_path,
        round_number=3,
        thesis_id="exhausted-thesis",
        outcome="rejected",
        config_changes={"ema_length": 12},
    )

    db = BacktestRunDB(db_path)
    assert len(db.list_research_rounds()) == 1
    assert db.list_research_thesis_attempts(job_id=5) == []


def test_log_research_round_persists_explicit_hypothesis_id_without_trace_context(
    tmp_path: Path,
) -> None:
    db = BacktestRunDB(tmp_path / "backtest_runs.db")
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
    db_path = tmp_path / "backtest_runs.db"
    BacktestRunDB(db_path)
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
        "required_diagnostic_specs": [
            {
                "key": "margin_per_order",
                "surface": "strategy_diagnostics",
                "payload_fields": [],
                "aliases": [],
                "description": "per-order margin diagnostic",
            }
        ],
        "new_dimension_name": "liquidity_decay",
        "why_existing_dimensions_do_not_fit": (
            "This studies edge decay after recent activity, not entry timing alone."
        ),
        "mechanism_family_definition": (
            "Liquidity decay mechanisms evaluate whether recent activity changes "
            "future fill quality and trade expectancy."
        ),
        "expected_reuse_across_future_theses": (
            "Future theses can reuse this dimension for decay windows and liquidity recovery."
        ),
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

    attempts = BacktestRunDB(db_path).list_research_thesis_attempts(
        job_id=5, thesis_id="opening_liquidity"
    )

    assert attempts[0]["thesis_details"] == details


# ── results_to_dicts ────────────────────────────────────────────


def test_results_to_dicts_copies_core_fields() -> None:
    record = BacktestResultRecord(
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
    record = BacktestResultRecord(
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
    record_top = BacktestResultRecord(
        "c.yaml",
        1.0,
        "keep",
        "",
        100,
        asi={"insight_brief": "from-top", "trade_analysis": {"insight_brief": "from-ta"}},
    )
    record_ta = BacktestResultRecord(
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


def test_resolve_conductor_inputs_uses_persisted_artifacts_and_verdict_feedback(
    tmp_path: Path,
) -> None:
    controller = _real_controller(tmp_path)
    config_path = tmp_path / "runtime" / "jobs" / "job-4" / "research" / "round-1"
    _write_valid_ema_config(config_path / "selected_config.json", ema_length=8)
    trades = tmp_path / "runtime" / "jobs" / "job-4" / "research" / "round-1" / "trades.csv"
    events = (
        tmp_path / "runtime" / "jobs" / "job-4" / "research" / "round-1" / "strategy_events.csv"
    )
    diagnostics = (
        tmp_path / "runtime" / "jobs" / "job-4" / "research" / "round-1" / "diagnostics.json"
    )
    trades.write_text("entry_date,pnl_pct\n2026-01-01,1.0\n")
    events.write_text("timestamp,event\n")
    diagnostics.write_text('{"accepted": 3}\n')
    latest = BacktestResultRecord(
        config="runtime/jobs/job-4/research/round-1/selected_config.json",
        metric=1.7,
        status="discard",
        description="invalid duplicate",
        timestamp=100,
        asi={
            "thesis_id": "ema-noop",
            "trades_file": trades.relative_to(tmp_path).as_posix(),
            "strategy_events_file": events.relative_to(tmp_path).as_posix(),
            "diagnostics_file": diagnostics.relative_to(tmp_path).as_posix(),
            "trade_analysis": {
                "trade_count": 3,
                "profit_factor": 1.7,
                "max_drawdown": -0.2,
                "verdict": {
                    "status": "invalid_noop_config",
                    "summary": "trade_count and diagnostics matched a prior run",
                },
            },
        },
        job=4,
    )
    older_other_job = BacktestResultRecord(
        config="configs/variants/other.yaml",
        metric=2.0,
        status="keep",
        description="other job",
        timestamp=200,
        asi={"thesis_id": "other"},
        job=3,
    )

    trades_file, events_file, diagnostics_file, latest_outcome = _resolve_conductor_inputs(
        controller,
        [older_other_job, latest],
        current_job=4,
    )

    assert trades_file == str(trades.resolve())
    assert events_file == str(events.resolve())
    assert diagnostics_file == str(diagnostics.resolve())
    assert latest_outcome["thesis_id"] == "ema-noop"
    assert latest_outcome["metric"] == 1.7
    assert latest_outcome["trade_count"] == 3
    assert latest_outcome["verdict_status"] == "invalid_noop_config"
    assert "revise the threshold" in latest_outcome["research_feedback"]


def test_resolve_conductor_inputs_injects_previous_thesis_from_db(tmp_path: Path) -> None:
    """previous_thesis in latest_outcome carries full thesis_details from the thesis attempt DB."""
    from autoresearch_state import write_state
    from backtest_run_db import BacktestRunDB
    from persistence_utils import utc_now_iso8601

    controller = _real_controller(tmp_path)
    db = BacktestRunDB(controller.backtest_run_db.path)
    write_state(controller.state_path, {"job": 1, "research_round": 2, "family": "ema"})

    # Needed for the job-scoped lookup: latest_thesis_details JOINs to
    # research_rounds for job_id, so the row must exist or the WHERE
    # r.job_id=1 filter excludes the attempt.
    with db._connect() as conn:  # noqa: SLF001 — test fixture, direct SQL is intentional
        conn.execute(
            """
            INSERT OR REPLACE INTO research_rounds (
                research_round_id, job_id, round_number, run_id, hypothesis_id,
                selected_thesis_id, outcome, created_at_utc, usage_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "job-1-round-2",
                1,
                2,
                "run-1-2",
                "ema-momentum-breakout",
                "ema-momentum-breakout",
                "thesis_accepted",
                utc_now_iso8601(),
                "{}",
            ),
        )
        conn.commit()

    db.add_research_thesis_attempt(
        {
            "research_round_id": "job-1-round-2",
            "attempt_number": 1,
            "thesis_id": "ema-momentum-breakout",
            "strategy_family": "ema",
            "config_changes": {"ema_length": 12},
            "validator_status": "compiled",
            "mechanism_dimension": "entry_timing",
            "hypothesis": "EMA crossover accelerates after news",
            "mechanism": "Momentum buildup drives short-term trend persistence",
            "thesis_details": {
                "expected_effects": [{"metric": "profit_factor", "direction": "increase"}],
                "evidence": ["backtested on 2024 data"],
                "evidence_strength": "proxy",
                "closest_prior_theses_considered": ["ema-baseline"],
                "orthogonality_defense": "Different timing layer",
                "falsification_or_alternative": "If ATR-based stop dominates, mechanism is wrong",
                "why_not_overfit": "Validated on out-of-sample window",
            },
            "validation_failure_reason": "",
            "selected_for_execution": 1,
            "created_at_utc": utc_now_iso8601(),
        }
    )
    config_path = tmp_path / "runtime" / "jobs" / "job-1" / "research" / "round-2"
    _write_valid_ema_config(config_path / "selected_config.json", ema_length=12)
    latest = BacktestResultRecord(
        config="runtime/jobs/job-1/research/round-2/selected_config.json",
        metric=1.5,
        status="discard",
        description="rejected",
        timestamp="2026-05-01T00:00:00+00:00",
        asi={"thesis_id": "ema-momentum-breakout", "trade_analysis": {}},
        job=1,
    )

    _, _, _, latest_outcome = _resolve_conductor_inputs(controller, [latest], current_job=1)

    assert "previous_thesis" in latest_outcome
    prev = latest_outcome["previous_thesis"]
    assert prev["hypothesis"] == "EMA crossover accelerates after news"
    assert prev["mechanism"] == "Momentum buildup drives short-term trend persistence"
    assert prev["config_changes"] == {"ema_length": 12}
    assert prev["evidence_strength"] == "proxy"
    assert prev["closest_prior_theses_considered"] == ["ema-baseline"]
    assert prev["falsification_or_alternative"] == "If ATR-based stop dominates, mechanism is wrong"
    assert prev["why_not_overfit"] == "Validated on out-of-sample window"
    assert len(prev["expected_effects"]) == 1


def test_resolve_conductor_inputs_no_previous_thesis_when_no_attempt_exists(
    tmp_path: Path,
) -> None:
    """When the thesis_id has no saved attempt, previous_thesis is absent from latest_outcome."""
    controller = _real_controller(tmp_path)
    latest = BacktestResultRecord(
        config="runtime/jobs/job-1/research/round-1/selected_config.json",
        metric=1.0,
        status="discard",
        description="rejected",
        timestamp="2026-05-01T00:00:00+00:00",
        asi={"thesis_id": "ema-unknown-thesis", "trade_analysis": {}},
        job=1,
    )

    _, _, _, latest_outcome = _resolve_conductor_inputs(controller, [latest], current_job=1)

    assert "previous_thesis" not in latest_outcome


def test_research_feedback_from_verdict_preserves_prefixed_summary() -> None:
    feedback = _research_feedback_from_verdict(
        "rejected",
        "rejected: trade count collapsed?",
    )

    assert feedback == "Previous candidate was rejected: trade count collapsed?"


def test_quality_score_uses_documented_skill_delta_formula() -> None:
    assert _quality_score_from_skill_delta(0.02) == pytest.approx(0.60)
    assert _quality_score_from_skill_delta(0.0) == pytest.approx(0.50)
    assert _quality_score_from_skill_delta(-0.20) == pytest.approx(0.0)
    assert _quality_score_from_skill_delta(0.20) == pytest.approx(1.0)


def _write_ema_base_config(root: Path) -> None:
    (root / "configs").mkdir(exist_ok=True)
    (root / "configs" / "ema_base.yaml").write_text(
        "ema_length: 5\nvalidation_start: '2020-01-01'\nvalidation_end: '2024-01-01'\n"
    )


def _write_screening_feature_table(round_root: Path) -> None:
    rows = []
    for index in range(80):
        is_holdout = index >= 60
        flagged = index % 2 == 0
        if is_holdout:
            is_loss = flagged
        else:
            is_loss = flagged and index < 56
        rows.append(
            {
                "trade_id": f"trade-{index}",
                "symbol": "SPY",
                "side": "long",
                "entry_ts": (
                    pd.Timestamp("2022-01-03", tz="UTC") + pd.Timedelta(days=index * 5)
                    if not is_holdout
                    else pd.Timestamp("2023-01-03", tz="UTC") + pd.Timedelta(days=(index - 60) * 5)
                ),
                "time_of_day_min": 15,
                "day_of_week": 1,
                "bars_since_open": 3,
                "gap_pct": -1.0 if flagged else 1.0,
                "prior_day_range_pct": 2.0,
                "overnight_move_pct": -1.0 if flagged else 1.0,
                "or_width_pctile": 0.5,
                "dist_to_ema_pct": -0.2,
                "vol_pctile_20d": 0.7,
                "regime_label": "trend",
                "stop_distance_pct": 0.4,
                "entry_bar_range_pct": 0.1,
                "out_pnl": -100.0 if is_loss else 100.0,
                "out_pnl_pct": -1.0 if is_loss else 1.0,
                "out_mae": 1.0,
                "out_mfe": 1.0,
                "out_exit_reason": "target" if not is_loss else "stop",
                "out_hold_bars": 5,
                "out_is_loss": is_loss,
            }
        )
    round_root.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_parquet(feature_table_path(round_root))


def _compiled_ema_thesis(thesis_id: str, *, ema_length: int = 8) -> dict:
    return {
        "thesis_id": thesis_id,
        "hypothesis": (
            f"Increasing ema_length to {ema_length} should smooth weak EMA crosses "
            "and improve profit_factor."
        ),
        "mechanism": (
            f"A slower ema_length={ema_length} signal filters noisy pullback crosses before entry."
        ),
        "mechanism_dimension": "signal_quality",
        "dimension_novelty": (
            "Tests EMA smoothing as a signal-quality gate with explicit analyst and "
            "web evidence rather than a neighboring threshold retry."
        ),
        "causal_cluster": "ema smoothing signal quality",
        "dominant_cluster_overlap": "low",
        "underexplored_dimensions_considered": [
            "entry_timing",
            "regime_conditioning",
        ],
        "novel_connection": (
            "Connects noisy pullback-cross failures to signal smoothing instead of "
            "session timing or exit changes."
        ),
        "closest_prior_theses_considered": ["ema-baseline"],
        "orthogonality_defense": "Changes signal smoothness, not exit logic or session timing.",
        "evidence_strength": "mixed",
        "thesis_role": "orthogonal_discovery",
        "falsification_or_alternative": (
            "Reject this mechanism if trade count collapses by more than half, "
            "profit_factor does not improve versus baseline, or losses are not "
            "concentrated in noisy EMA cross events."
        ),
        "alternatives_considered": [
            {
                "mechanism": "Delay entries after open",
                "why_rejected": "This changes session timing instead of the noisy-cross root cause.",
            },
            {
                "mechanism": "Tighten stop distance",
                "why_rejected": "This changes risk structure instead of filtering weak EMA signals.",
            },
        ],
        "evidence_citations": [
            {"source": "web_search", "citation": "EMA smoothing reduces noisy signals."},
            {"source": "analyst", "citation": "Analyst found weak crosses cluster in losses."},
            {
                "source": "round_result",
                "citation": "Latest experiment result showed weak-cross filtering needs research.",
            },
        ],
        "source_code_verification": (
            "strategies/ema/signals.py:generate_signals_for_frame uses ema_length "
            "in EMA signal construction."
        ),
        "config_changes": {"ema_length": ema_length},
        "expected_effects": [
            {
                "metric": "profit_factor",
                "direction": "increase",
                "rationale": "Filtering weak crosses should improve realized edge.",
            },
            {
                "metric": "trade_count",
                "direction": "decrease",
                "rationale": "A smoother EMA should reject some marginal cross signals.",
            },
        ],
        "disqualifiers": [
            {
                "name": "trade_count_collapse",
                "condition": "trade_count falls by more than half",
                "severity": "hard_fail",
                "kind": "metric_threshold",
            },
            {
                "name": "no_noisy_cross_reduction",
                "condition": (
                    "Losing trades are not concentrated in weak EMA cross events, "
                    "or slower EMA smoothing does not reduce those weak-cross losses."
                ),
                "severity": "hard_fail",
                "kind": "mechanism_evidence",
            },
        ],
        "why_not_overfit": "Mechanism is signal quality and is tested against full train history.",
    }


def _dict_to_conductor_result(d: dict) -> ConductorResult:
    """Convert legacy test dict (suggested_theses wrapper) to ConductorResult."""
    if d.get("should_stop"):
        return ConductorResult(
            status="should_stop", should_stop=True, reasoning=d.get("reasoning", "")
        )
    theses = d.get("suggested_theses") or []
    return ConductorResult(
        status="ok",
        thesis=theses[0] if theses else None,
        reasoning=d.get("reasoning", ""),
        tools_called=frozenset(d.get("tools_called", {"list_round_results", "web_search"})),
    )


def _patch_conductor_round(monkeypatch: pytest.MonkeyPatch, *outputs: dict) -> None:
    queue = list(outputs)

    def _run_conductor(*args, **kwargs):
        raw = queue.pop(0) if len(queue) > 1 else dict(queue[0])
        return _dict_to_conductor_result(raw)

    monkeypatch.setattr("research_conductor.run_research_conductor_sync", _run_conductor)


def test_execute_research_sdk_passes_rendered_corpus_to_conductor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    controller = _real_controller(tmp_path)
    controller.write_state(
        {
            "state": "blocked",
            "job": 12,
            "research_round": 0,
            "blockers": [{"kind": "research_required"}],
            "next_action": {"type": "research"},
        }
    )
    captured: dict[str, object] = {}
    corpus_calls: list[dict[str, object]] = []

    def fake_build_corpus(
        family: str,
        round_number: int,
        job: int | None = None,
        *,
        runtime_root: Path | None = None,
        code_root: Path | None = None,
    ) -> object:
        corpus_calls.append(
            {
                "family": family,
                "round_number": round_number,
                "job": job,
                "runtime_root": runtime_root,
                "code_root": code_root,
            }
        )
        return object()

    monkeypatch.setattr("evidence_pack.build_corpus", fake_build_corpus)
    monkeypatch.setattr("evidence_pack.render_corpus", lambda corpus: "RENDERED-CORPUS")

    def _run_conductor(*args, **kwargs):
        captured.update(kwargs)
        return ConductorResult(status="should_stop", should_stop=True, reasoning="stop")

    monkeypatch.setattr("research_conductor.run_research_conductor_sync", _run_conductor)

    result = execute_research_sdk(controller)

    assert result["status"] == "completed"
    assert result["should_stop"] is True
    assert captured["rendered_corpus"] == "RENDERED-CORPUS"
    assert captured["conductor_mode"] == "mechanism"
    assert corpus_calls == [
        {
            "family": "ema",
            "round_number": 0,
            "job": 12,
            "runtime_root": tmp_path,
            "code_root": tmp_path,
        }
    ]


def test_validation_attempt_dispatches_to_named_mechanism_flow(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    controller = _real_controller(tmp_path)
    controller.write_state({"state": "blocked", "job": 12, "research_round": 0})
    calls: list[str] = []

    def fake_mechanism(*args, **kwargs):
        calls.append("mechanism")
        return {"status": "completed"}, None, ""

    def fake_legacy(*args, **kwargs):
        calls.append("legacy")
        return {"status": "legacy"}, None, ""

    monkeypatch.setattr("autoresearch_research._try_mechanism_validation_attempt", fake_mechanism)
    monkeypatch.setattr("autoresearch_research._try_legacy_validation_attempt", fake_legacy)

    result, retry_feedback, stage = _try_one_validation_attempt(
        controller,
        1,
        0,
        ConductorResult(
            status="ok",
            thesis={
                "story": "A smoother EMA filters weak pullback crosses.",
                "rule": "gap_pct < 0",
                "competitor_rule": "gap_pct > 0",
                "competitor_story": "Gap-up continuation dominates.",
                "actionable": False,
                "proposed_change": None,
                "predictions": None,
            },
        ),
        [],
    )

    assert result == {"status": "completed"}
    assert retry_feedback is None
    assert stage == ""
    assert calls == ["mechanism"]


def test_partial_mechanism_proposal_does_not_fall_back_to_legacy_validation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    controller = _real_controller(tmp_path)
    _write_ema_base_config(tmp_path)
    controller.write_state({"state": "blocked", "job": 12, "research_round": 0})

    def fake_legacy(*args, **kwargs):
        raise AssertionError("partial mechanism proposal must not use legacy validator")

    monkeypatch.setattr("autoresearch_research._try_legacy_validation_attempt", fake_legacy)

    result, retry_feedback, stage = _try_one_validation_attempt(
        controller,
        1,
        0,
        ConductorResult(
            status="ok",
            thesis={
                "story": "Gap-down entries explain residual losses.",
                "rule": "gap_pct < 0",
                "competitor_rule": "gap_pct > 0",
                "competitor_story": "Gap-up entries may explain residual wins.",
                "actionable": True,
                "proposed_change": {"ema_length": 8},
            },
            reasoning="malformed mechanism proposal",
        ),
        prior_theses=[],
    )

    assert result is None
    assert stage == "stage_1"
    assert "predictions" in str(retry_feedback)


def test_execute_research_sdk_builds_corpus_from_latest_completed_round_for_job(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    controller = _real_controller(tmp_path)
    controller.write_state(
        {
            "state": "blocked",
            "job": 12,
            "research_round": 0,
            "blockers": [{"kind": "research_required"}],
            "next_action": {"type": "research"},
        }
    )
    calls: list[tuple[str, int, int | None, Path | None, Path | None]] = []

    def fake_build_corpus(
        family: str,
        round_number: int,
        job: int | None = None,
        *,
        runtime_root: Path | None = None,
        code_root: Path | None = None,
    ) -> object:
        calls.append((family, round_number, job, runtime_root, code_root))
        return object()

    monkeypatch.setattr("evidence_pack.build_corpus", fake_build_corpus)
    monkeypatch.setattr("evidence_pack.render_corpus", lambda corpus: "RENDERED-CORPUS")
    monkeypatch.setattr(
        "research_conductor.run_research_conductor_sync",
        lambda *args, **kwargs: ConductorResult(
            status="should_stop", should_stop=True, reasoning="stop"
        ),
    )

    execute_research_sdk(controller)

    assert calls == [("ema", 0, 12, tmp_path, tmp_path)]


def test_execute_research_sdk_persists_retry_feedback_for_corpus(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_ema_base_config(tmp_path)
    controller = _real_controller(tmp_path)
    controller.write_state(
        {
            "state": "blocked",
            "job": 12,
            "research_round": 0,
            "blockers": [{"kind": "research_required"}],
            "next_action": {"type": "research"},
        }
    )
    calls = [
        ConductorResult(
            status="ok",
            thesis={
                "story": "A smoother EMA filters weak pullback crosses.",
                "rule": "gap_pct < 0",
                "competitor_rule": "gap_pct > 0",
                "competitor_story": "Gap-up trades might be the actual source of edge.",
                "actionable": True,
                "proposed_change": {"ema_length": 8},
                "predictions": [
                    {"metric": "profit_factor", "direction": "increase", "predicted": 2.2},
                    {"metric": "trade_count", "direction": "decrease"},
                ],
            },
            reasoning="invalid first proposal",
        ),
        ConductorResult(status="should_stop", should_stop=True, reasoning="stop after feedback"),
    ]
    monkeypatch.setattr("thesis_validator.load_prior_theses", lambda _root, **kwargs: [])
    monkeypatch.setattr(
        "autoresearch_research._resolve_conductor_inputs",
        lambda *args, **kwargs: ("trades.csv", "events.parquet", "diagnostics.json", {}),
    )
    monkeypatch.setattr("improvement_flags.reflexion_enabled", lambda: False)
    monkeypatch.setattr(
        "research_conductor.run_research_conductor_sync",
        lambda *args, **kwargs: calls.pop(0),
    )

    result = execute_research_sdk(controller)

    feedback_path = (
        tmp_path / "runtime" / "jobs" / "job-12" / "research" / "round-1" / "rejection_feedback.txt"
    )
    assert result["should_stop"] is True
    assert feedback_path.exists()
    assert "predicted" in feedback_path.read_text(encoding="utf-8")


def test_mechanism_proposal_compiles_without_legacy_thesis_validator(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    controller = _real_controller(tmp_path)
    _write_ema_base_config(tmp_path)
    monkeypatch.setenv("AUTORESEARCH_RUNTIME_ROOT", str(tmp_path))
    controller.write_state({"state": "blocked", "job": 12, "research_round": 0})
    round_root = tmp_path / "runtime" / "jobs" / "job-12" / "research" / "round-1"
    _write_screening_feature_table(
        tmp_path / "runtime" / "jobs" / "job-12" / "research" / "round-0-baseline"
    )

    def _legacy_validator_called(*args, **kwargs):
        raise AssertionError("legacy thesis validator must not run for MechanismProposal")

    monkeypatch.setattr("thesis_validator.validate_thesis_dict", _legacy_validator_called)
    monkeypatch.setattr("thesis_validator.validate_stage_2", _legacy_validator_called)

    result, retry_feedback, stage = _try_one_validation_attempt(
        controller,
        1,
        0,
        ConductorResult(
            status="ok",
            thesis={
                "story": "A smoother EMA filters weak pullback crosses.",
                "rule": "gap_pct < 0",
                "competitor_rule": "gap_pct > 0",
                "competitor_story": "Gap-up trades might be the actual source of edge.",
                "actionable": True,
                "proposed_change": {"ema_length": 8},
                "predictions": [
                    {
                        "metric": "profit_factor",
                        "direction": "increase",
                        "predicted": 2.2,
                        "rationale": "Fewer weak crosses should raise PF.",
                    },
                    {
                        "metric": "trade_count",
                        "direction": "decrease",
                        "predicted": 80,
                        "rationale": "Filter removes marginal entries.",
                    },
                ],
            },
            reasoning="proposal from corpus",
        ),
        prior_theses=[],
    )

    selected_thesis = json.loads((round_root / "selected_thesis.json").read_text())
    registered = json.loads((round_root / "registered_predictions.json").read_text())
    selected_config = json.loads((round_root / "selected_config.json").read_text())
    model = load_model("ema", runtime_root=tmp_path, code_root=tmp_path)
    pending_model = PendingCausalModelArtifact(round_root).load()
    assert retry_feedback is None
    assert stage == ""
    assert result is not None
    assert result["generated_config"] == "runtime/jobs/job-12/research/round-1/selected_config.json"
    assert selected_config["ema_length"] == 8
    assert selected_thesis["rule"] == "gap_pct < 0"
    assert selected_thesis["proposed_change"] == {"ema_length": 8}
    assert registered["predictions"][0]["metric"] == "profit_factor"
    assert model.factors == []
    assert model.accuracy_history == []
    assert pending_model is not None
    assert pending_model.factors[-1].rule == "gap_pct < 0"
    assert pending_model.factors[-1].status == "candidate"
    assert pending_model.accuracy_history[-1].round_number == 1
    with sqlite3.connect(controller.backtest_run_db.path) as conn:
        rows = conn.execute("""
            SELECT rule, competitor_rule, verdict
            FROM screenings
            ORDER BY rule
            """).fetchall()
    assert rows == [
        ("gap_pct < 0", "gap_pct > 0", "pass"),
        ("gap_pct > 0", "gap_pct < 0", "pass"),
    ]


def test_mechanism_proposal_retry_revalidates_schema_before_screening(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    controller = _real_controller(tmp_path)
    _write_ema_base_config(tmp_path)
    monkeypatch.setenv("AUTORESEARCH_RUNTIME_ROOT", str(tmp_path))
    controller.write_state({"state": "blocked", "job": 12, "research_round": 0})
    round_root = tmp_path / "runtime" / "jobs" / "job-12" / "research" / "round-1"
    _write_screening_feature_table(round_root)

    result, retry_feedback, stage = _try_one_validation_attempt(
        controller,
        1,
        0,
        ConductorResult(
            status="ok",
            thesis={
                "story": "A smoother EMA filters weak pullback crosses.",
                "rule": "gap_pct < 0",
                "competitor_rule": "gap_pct > 0",
                "competitor_story": "Gap-up trades might be the actual source of edge.",
                "actionable": True,
                "proposed_change": {"ema_length": 8},
                "predictions": [
                    {"metric": "profit_factor", "direction": "increase", "predicted": 2.2},
                    {"metric": "trade_count", "direction": "decrease"},
                ],
            },
            reasoning="proposal from parsed retry path",
        ),
        prior_theses=[],
    )

    assert result is None
    assert stage == "stage_1"
    assert retry_feedback is not None
    assert "predicted" in retry_feedback
    assert not (round_root / "selected_config.json").exists()
    with sqlite3.connect(controller.backtest_run_db.path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM screenings").fetchone()[0] == 0


def test_mechanism_proposal_rejects_neighboring_prior_config_change_before_screening(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    controller = _real_controller(tmp_path)
    _write_ema_base_config(tmp_path)
    monkeypatch.setenv("AUTORESEARCH_RUNTIME_ROOT", str(tmp_path))
    controller.write_state({"state": "blocked", "job": 12, "research_round": 0})
    _write_screening_feature_table(
        tmp_path / "runtime" / "jobs" / "job-12" / "research" / "round-0-baseline"
    )

    result, retry_feedback, stage = _try_one_validation_attempt(
        controller,
        1,
        0,
        ConductorResult(
            status="ok",
            thesis={
                "story": "A smoother EMA filters weak pullback crosses.",
                "rule": "gap_pct < 0",
                "competitor_rule": "gap_pct > 0",
                "competitor_story": "Gap-up trades might be the actual source of edge.",
                "actionable": True,
                "proposed_change": {"ema_length": 8},
                "predictions": [
                    {"metric": "profit_factor", "direction": "increase", "predicted": 2.2},
                    {"metric": "trade_count", "direction": "decrease", "predicted": 80},
                ],
            },
            reasoning="proposal from parsed retry path",
        ),
        prior_theses=[{"thesis_id": "ema-prior-neighbor", "config_changes": {"ema_length": 5}}],
    )

    assert result is None
    assert stage == "stage_1"
    assert retry_feedback is not None
    assert "Neighboring threshold" in retry_feedback
    with sqlite3.connect(controller.backtest_run_db.path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM screenings").fetchone()[0] == 0


def test_mechanism_proposal_compiles_under_runtime_root_when_code_root_is_separate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    code_root = tmp_path / "code"
    runtime_root = tmp_path / "runtime-root"
    code_root.mkdir()
    runtime_root.mkdir()
    _write_ema_base_config(code_root)
    monkeypatch.setenv("AUTORESEARCH_RUNTIME_ROOT", str(runtime_root))
    controller = AutoresearchController(
        root=code_root,
        runtime_root=runtime_root,
        family=load_family("ema"),
        state_path=runtime_root / "ema_autoresearch.next.json",
        current_md_path=runtime_root / "ema_autoresearch.current.md",
        jobs_root=runtime_root / "runtime" / "jobs",
    )
    controller.write_state({"state": "blocked", "job": 12, "research_round": 0})
    round_root = runtime_root / "runtime" / "jobs" / "job-12" / "research" / "round-1"
    _write_screening_feature_table(
        runtime_root / "runtime" / "jobs" / "job-12" / "research" / "round-0-baseline"
    )

    result, retry_feedback, stage = _try_one_validation_attempt(
        controller,
        1,
        0,
        ConductorResult(
            status="ok",
            thesis={
                "story": "A smoother EMA filters weak pullback crosses.",
                "rule": "gap_pct < 0",
                "competitor_rule": "gap_pct > 0",
                "competitor_story": "Gap-up trades might be the actual source of edge.",
                "actionable": True,
                "proposed_change": {"ema_length": 8},
                "predictions": [
                    {"metric": "profit_factor", "direction": "increase", "predicted": 2.2},
                    {"metric": "trade_count", "direction": "decrease", "predicted": 80},
                ],
            },
            reasoning="proposal from corpus",
        ),
        prior_theses=[],
    )

    assert retry_feedback is None
    assert stage == ""
    assert result is not None
    assert (round_root / "selected_config.json").exists()
    assert not (
        code_root / "runtime" / "jobs" / "job-12" / "research" / "round-1" / "selected_config.json"
    ).exists()


def test_mechanism_proposal_does_not_populate_legacy_expected_effects() -> None:
    thesis = _mechanism_proposal_to_research_thesis(
        {
            "story": "Gap-down entries improve PF.",
            "rule": "gap_pct < 0",
            "actionable": True,
            "proposed_change": {"ema_length": 8},
            "predictions": [{"metric": "profit_factor", "direction": "increase", "predicted": 2.4}],
        },
        strategy_family="ema",
        thesis_id="job-1-round-1-attempt-1",
    )

    assert thesis["expected_effects"] == []
    assert thesis["predictions"] == [
        {"metric": "profit_factor", "direction": "increase", "predicted": 2.4}
    ]


def test_mechanism_proposal_does_not_update_model_when_compile_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AUTORESEARCH_RUNTIME_ROOT", str(tmp_path))
    controller = _real_controller(tmp_path)
    _write_ema_base_config(tmp_path)
    controller.write_state({"job": 12, "research_round": 1})
    _write_screening_feature_table(
        tmp_path / "runtime" / "jobs" / "job-12" / "research" / "round-0-baseline"
    )

    def fail_compile(*args, **kwargs):
        raise ValueError("compiled config is invalid")

    monkeypatch.setattr("compiler_pipeline.compile_research_thesis", fail_compile)

    result, retry_feedback, stage = _try_one_validation_attempt(
        controller,
        1,
        0,
        ConductorResult(
            status="ok",
            thesis={
                "story": "A smoother EMA filters weak pullback crosses.",
                "rule": "gap_pct < 0",
                "competitor_rule": "gap_pct > 0",
                "competitor_story": "Gap-up entries might be the actual source.",
                "actionable": True,
                "proposed_change": {"ema_length": 8},
                "predictions": [
                    {
                        "metric": "profit_factor",
                        "direction": "increase",
                        "predicted": 2.2,
                        "rationale": "Fewer weak crosses should raise PF.",
                    },
                    {
                        "metric": "trade_count",
                        "direction": "decrease",
                        "predicted": 80,
                        "rationale": "Filter removes marginal entries.",
                    },
                ],
            },
            reasoning="proposal from corpus",
        ),
        prior_theses=[],
    )

    model = load_model("ema", runtime_root=tmp_path, code_root=tmp_path)
    assert result is None
    assert stage == "compile"
    assert "compiled config is invalid" in str(retry_feedback)
    assert model.factors == []
    assert model.accuracy_history == []


def test_run_research_non_actionable_mechanism_continues_without_interrupt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    controller = _real_controller(tmp_path)
    _write_ema_base_config(tmp_path)
    controller.write_state(
        {
            "state": "blocked",
            "job": 12,
            "research_round": 0,
            "blockers": [{"kind": "research_required"}],
            "next_action": {"type": "research"},
        }
    )
    round_root = tmp_path / "runtime" / "jobs" / "job-12" / "research" / "round-0-baseline"
    _write_screening_feature_table(round_root)
    _patch_conductor_round(
        monkeypatch,
        {
            "reasoning": "Screened but not actionable.",
            "suggested_theses": [
                {
                    "story": "Gap-down entries explain residual losses.",
                    "rule": "gap_pct < 0",
                    "competitor_rule": "gap_pct > 0",
                    "competitor_story": "Gap-up entries may explain the residual instead.",
                    "actionable": False,
                    "proposed_change": {},
                    "predictions": [],
                }
            ],
            "should_stop": False,
        },
    )
    monkeypatch.setattr("research_conductor.reset_round_usage", lambda: None)
    monkeypatch.setattr("research_conductor.get_round_usage", lambda: {})

    updated = run_research(controller, controller.read_state())

    model = load_model("ema", runtime_root=tmp_path, code_root=tmp_path)
    with sqlite3.connect(controller.backtest_run_db.path) as conn:
        screenings = conn.execute("""
            SELECT rule, competitor_rule, verdict
            FROM screenings
            WHERE job_id = 12 AND round_number = 1
            ORDER BY rule
            """).fetchall()
    assert updated["state"] == "blocked"
    assert updated["research_round"] == 1
    assert updated["next_action"]["type"] == "research"
    assert updated["blockers"][0]["kind"] == "research_required"
    assert screenings == [
        ("gap_pct < 0", "gap_pct > 0", "pass"),
        ("gap_pct > 0", "gap_pct < 0", "pass"),
    ]
    assert model.version == 1
    assert model.factors[0].rule == "gap_pct < 0"
    assert model.factors[0].status == "candidate"
    assert len(model.accuracy_history) == 1


def test_run_research_success_persists_round_artifacts_and_next_action(
    tmp_path: Path, monkeypatch
) -> None:
    controller = _real_controller(tmp_path)
    _write_ema_base_config(tmp_path)
    controller.write_state(
        {
            "state": "blocked",
            "job": 12,
            "research_round": 0,
            "current_best": {"config": "configs/ema_base.yaml", "metric": 1.2},
            "blockers": [{"kind": "research_required"}],
            "next_action": {"type": "research"},
        }
    )
    generated_config = "runtime/jobs/job-12/research/round-1/selected_config.json"
    thesis = _compiled_ema_thesis("ema-tight-entry", ema_length=8)
    _patch_conductor_round(
        monkeypatch,
        {
            "reasoning": "Tighter entry filter should reduce weak crosses.",
            "suggested_theses": [thesis],
            "should_stop": False,
        },
    )
    monkeypatch.setattr("research_conductor.reset_round_usage", lambda: None)
    monkeypatch.setattr(
        "research_conductor.get_round_usage",
        lambda: {"total": {"total_tokens": 321, "input_tokens": 200, "output_tokens": 121}},
    )

    updated = run_research(controller, controller.read_state())

    round_root = tmp_path / "runtime" / "jobs" / "job-12" / "research" / "round-1"
    assert updated["state"] == "running"
    assert updated["next_action"]["config"] == generated_config
    assert updated["activity"]["phase"] == "pending_backtest"
    assert json.loads((round_root / "round.json").read_text())["outcome"] == "compiled"
    links = json.loads((round_root / "links.json").read_text())
    assert links["generated_config_path"] == generated_config
    attempts = controller.backtest_run_db.list_research_thesis_attempts(
        job_id=12,
        thesis_id="job-12-round-1-attempt-1",
    )
    assert attempts[0]["validator_status"] == "compiled"
    assert attempts[0]["thesis_details"]["closest_prior_theses_considered"] == ["ema-baseline"]
    assert controller.read_state()["job_usage"]["total_tokens"] == 321


def test_ready_to_run_writes_registered_predictions_before_backtest_dispatch(
    tmp_path: Path,
) -> None:
    controller = _real_controller(tmp_path)

    class Contract:
        contract_id = "contract-001"

    result = _on_ready_to_run(
        controller,
        research_round=2,
        contract=Contract(),
        raw_thesis={
            "predictions": [
                {"metric": "profit_factor", "direction": "increase", "predicted": 2.4},
                {
                    "metric": "pnl_weighted_accuracy",
                    "direction": "increase",
                    "predicted": 0.65,
                },
            ]
        },
        thesis_id="thesis-001",
        conductor_result=ConductorResult(status="ok", reasoning="harvest now"),
        should_stop=False,
        job_id=12,
    )

    registered_path = (
        tmp_path
        / "runtime"
        / "jobs"
        / "job-12"
        / "research"
        / "round-2"
        / "registered_predictions.json"
    )
    registered = json.loads(registered_path.read_text(encoding="utf-8"))
    assert result["generated_config"] == "runtime/jobs/job-12/research/round-2/selected_config.json"
    assert registered["thesis_id"] == "thesis-001"
    assert registered["predictions"][0]["metric"] == "profit_factor"
    assert registered["registered_at_utc"].endswith("+00:00")


def test_ready_to_run_rejects_undeclared_multi_key_proposed_change(tmp_path: Path) -> None:
    controller = _real_controller(tmp_path)

    class Contract:
        contract_id = "contract-001"

    with pytest.raises(ValueError, match="exactly one top-level key"):
        _on_ready_to_run(
            controller,
            research_round=2,
            contract=Contract(),
            raw_thesis={"proposed_change": {"gap_filter": True, "gap_pct": 0.01}},
            thesis_id="thesis-001",
            conductor_result=ConductorResult(status="ok", reasoning="harvest now"),
            should_stop=False,
            job_id=12,
        )


def test_run_research_should_stop_closes_job_with_finished_state(
    tmp_path: Path, monkeypatch
) -> None:
    controller = _real_controller(tmp_path)
    _write_ema_base_config(tmp_path)
    controller.write_state(
        {
            "state": "blocked",
            "job": 13,
            "research_round": 1,
            "current_best": {"config": "configs/ema_base.yaml", "metric": 1.4},
            "blockers": [{"kind": "research_required"}],
            "next_action": {"type": "research"},
        }
    )
    _patch_conductor_round(
        monkeypatch,
        {
            "status": "completed",
            "suggested_theses": [],
            "should_stop": True,
            "reasoning": "No remaining orthogonal mechanism has enough evidence.",
        },
    )
    monkeypatch.setattr("research_conductor.reset_round_usage", lambda: None)
    monkeypatch.setattr(
        "research_conductor.get_round_usage",
        lambda: {"total": {"total_tokens": 10}},
    )

    updated = run_research(controller, controller.read_state())

    assert updated["state"] == "finished"
    assert updated["finished_reason"] == "research_recommends_stop"
    assert updated["research_stop_reasoning"] == (
        "No remaining orthogonal mechanism has enough evidence."
    )
    assert "activity" not in updated
    assert (tmp_path / "ema_autoresearch.current.md").exists()


def test_run_research_rejected_round_blocks_with_persisted_rejection(
    tmp_path: Path, monkeypatch
) -> None:
    controller = _real_controller(tmp_path)
    _write_ema_base_config(tmp_path)
    controller.write_state(
        {
            "state": "blocked",
            "job": 14,
            "research_round": 2,
            "blockers": [{"kind": "research_required"}],
            "next_action": {"type": "research"},
        }
    )
    invalid_thesis = _compiled_ema_thesis("ema-duplicate", ema_length=8)
    invalid_thesis["config_changes"] = {"requires_code_change": True}
    _patch_conductor_round(
        monkeypatch,
        {
            "reasoning": "Duplicate EMA idea.",
            "suggested_theses": [invalid_thesis],
            "should_stop": False,
        },
    )
    monkeypatch.setattr("research_conductor.reset_round_usage", lambda: None)
    monkeypatch.setattr(
        "research_conductor.get_round_usage",
        lambda: {"total": {"total_tokens": 44}},
    )

    updated = run_research(controller, controller.read_state())

    round_root = tmp_path / "runtime" / "jobs" / "job-14" / "research" / "round-3"
    assert updated["state"] == "interrupted"
    assert updated["research_round"] == 3
    assert updated["blockers"][0]["kind"] == "research_failed"
    assert "activity" not in updated
    assert json.loads((round_root / "round.json").read_text())["outcome"] == "rejected"
    attempts = controller.backtest_run_db.list_research_thesis_attempts(
        job_id=14,
        thesis_id="job-14-round-3-attempt-1",
    )
    assert attempts[0]["validator_status"] == "rejected_attempt_1"
    assert (
        "config_changes contains thesis metadata key" in attempts[-1]["validation_failure_reason"]
    )


def test_run_research_max_rounds_finishes_without_conductor_call(tmp_path: Path) -> None:
    controller = _real_controller(tmp_path)
    controller.write_state(
        {
            "state": "blocked",
            "job": 15,
            "research_round": MAX_RESEARCH_ROUNDS,
            "current_best": {"config": "configs/ema_base.yaml", "metric": 1.6},
            "activity": {"type": "research", "phase": "conductor_running"},
        }
    )

    def fail_if_called():
        raise AssertionError("conductor should not run once max rounds is exceeded")

    controller.execute_research_one = fail_if_called  # type: ignore[method-assign]

    updated = run_research(controller, controller.read_state())

    assert updated["state"] == "finished"
    assert updated["finished_reason"] == "max_research_rounds_reached"
    assert "activity" not in updated


def test_try_one_validation_attempt_operationalizes_code_change_before_validation(
    tmp_path: Path, monkeypatch
) -> None:
    captured: dict[str, object] = {}

    class _Controller:
        root = tmp_path
        job_runtime_root = tmp_path
        family = type("Family", (), {"name": "ema"})()

        def read_state(self):
            return {"job": 2}

        def log_research_round(self, *args, **kwargs):
            raise AssertionError("should not reject after operationalization")

    def fake_operationalize(thesis):
        updated = dict(thesis)
        updated["requested_primitives"] = ["opening_range_resolution_gate_enabled"]
        updated["config_changes"] = {"opening_range_resolution_gate_enabled": True}
        return updated

    class _Validated:
        thesis_id = "opening_range_gate"

    class _Contract:
        status = "needs_code"
        contract_id = "opening_range_gate"

    def fake_validate(raw, prior_theses=None, tools_called=None, **kwargs):
        captured["validated_raw"] = dict(raw)
        return _Validated()

    def fake_compile(validated, root, artifact_root=None):
        captured["compiled"] = {
            "thesis_id": validated.thesis_id,
            "root": root,
            "artifact_root": artifact_root,
        }
        return _Contract()

    monkeypatch.setattr("compiler_pipeline.operationalize_thesis", fake_operationalize)
    monkeypatch.setattr("thesis_validator.validate_thesis_dict", fake_validate)
    monkeypatch.setattr("compiler_pipeline.compile_research_thesis", fake_compile)

    result, retry_feedback, _stage = _try_one_validation_attempt(
        _Controller(),
        2,
        0,
        ConductorResult(
            status="ok",
            thesis={
                "thesis_id": "opening_range_gate",
                "hypothesis": "need a new opening range gate",
                "mechanism": "gate post-open entries on opening range resolution",
                "mechanism_dimension": "signal_quality",
                "dimension_novelty": "new gate",
                "expected_effects": [
                    {
                        "metric": "profit_factor",
                        "direction": "increase",
                        "rationale": "better quality",
                    }
                ],
                "disqualifiers": [
                    {
                        "name": "trade_count_collapse",
                        "condition": "trade_count falls too much",
                        "severity": "hard_fail",
                    }
                ],
                "requires_code_change": True,
                "requested_primitives": [],
            },
            reasoning="code change thesis",
        ),
        prior_theses=[],
    )

    assert retry_feedback is None
    assert result is not None
    assert captured["validated_raw"]["requested_primitives"] == [  # type: ignore[index]
        "opening_range_resolution_gate_enabled"
    ]
    assert captured["validated_raw"]["config_changes"] == {  # type: ignore[index]
        "opening_range_resolution_gate_enabled": True
    }


def test_try_one_validation_attempt_preserves_thesis_metadata_on_ready_to_run(
    tmp_path: Path, monkeypatch
) -> None:
    class _Controller:
        root = tmp_path
        job_runtime_root = tmp_path
        family = type("Family", (), {"name": "ema"})()
        ctx = type("Ctx", (), {"current_contract": None, "parent_backtest_run_id": ""})()

        def read_state(self):
            return {"job": 8}

        def _queue_variants(self, variants, validated, contract, baseline_config):
            raise AssertionError("no variants expected")

        def backtest_run_db(self):
            raise AssertionError("not used")

    class _Validated:
        thesis_id = "symbol_day_gate"

    class _Contract:
        status = "ready_to_run"
        contract_id = "exp-001"

    monkeypatch.setattr(
        "thesis_validator.validate_thesis_dict",
        lambda raw, prior_theses=None, tools_called=None, **kwargs: _Validated(),
    )
    monkeypatch.setattr(
        "compiler_pipeline.compile_research_thesis",
        lambda validated, root, artifact_root=None: _Contract(),
    )
    monkeypatch.setattr("autoresearch_research.load_baseline_config", lambda root, family: {})

    controller = _Controller()
    controller.backtest_run_db = type("DB", (), {"latest": lambda self, n: []})()

    result, retry_feedback, _stage = _try_one_validation_attempt(
        controller,
        8,
        0,
        ConductorResult(
            status="ok",
            thesis={
                "thesis_id": "symbol_day_gate",
                "hypothesis": "gate later shorts on 09:30 raw setup presence",
                "mechanism": "symbol-day setup gate",
                "mechanism_dimension": "regime_conditioning",
                "dimension_novelty": "uses symbol-day acceptance history",
                "closest_prior_theses_considered": [
                    "block_shorts_on_early_impulse_trend_open_days"
                ],
                "orthogonality_defense": "uses acceptance provenance rather than price impulse",
                "evidence_strength": "mixed",
                "thesis_role": "orthogonal_discovery",
                "falsification_or_alternative": "if 09:30 setup days still fail, trend-open narrative is weak",
                "config_changes": {"symbol_day_opening_setup_gate_enabled": True},
                "expected_effects": [],
                "disqualifiers": [],
            },
            reasoning="candidate looks good",
        ),
        prior_theses=[],
    )

    assert retry_feedback is None
    assert result is not None
    assert result["generated_config"] == "runtime/jobs/job-8/research/round-8/selected_config.json"
    assert result["thesis"]["closest_prior_theses_considered"] == [
        "block_shorts_on_early_impulse_trend_open_days"
    ]
    assert result["thesis"]["falsification_or_alternative"].startswith("if 09:30 setup days")


def test_try_one_validation_attempt_treats_operationalize_value_error_as_retry_feedback(
    tmp_path: Path, monkeypatch
) -> None:
    class _Controller:
        root = tmp_path
        job_runtime_root = tmp_path
        family = type("Family", (), {"name": "ema"})()
        rejected: list[tuple[str, str]] = []

        def read_state(self):
            return {"job": 0}

        def log_research_round(self, *args, **kwargs):
            self.rejected.append((args, kwargs))

    def fake_operationalize(thesis):
        raise ValueError("missing_primitives must be a list of strings")

    monkeypatch.setattr("compiler_pipeline.operationalize_thesis", fake_operationalize)

    result, retry_feedback, _stage = _try_one_validation_attempt(
        _Controller(),
        2,
        0,
        ConductorResult(
            status="ok",
            thesis={
                "thesis_id": "opening_range_gate",
                "hypothesis": "need a new opening range gate",
                "mechanism": "gate post-open entries on opening range resolution",
                "mechanism_dimension": "signal_quality",
                "dimension_novelty": "new gate",
                "expected_effects": [
                    {
                        "metric": "profit_factor",
                        "direction": "increase",
                        "rationale": "better quality",
                    }
                ],
                "disqualifiers": [
                    {
                        "name": "trade_count_collapse",
                        "condition": "trade_count falls too much",
                        "severity": "hard_fail",
                    }
                ],
                "requires_code_change": True,
                "requested_primitives": [],
            },
            reasoning="code change thesis",
        ),
        prior_theses=[],
    )

    assert result is None
    assert retry_feedback is not None
    assert "rejected by validator" in retry_feedback
    assert "missing_primitives must be a list of strings" in retry_feedback


def test_handle_needs_code_uses_full_validation_contract_before_compile(
    tmp_path: Path, monkeypatch
) -> None:
    captured: dict[str, object] = {}

    class _Controller:
        root = tmp_path
        job_runtime_root = tmp_path
        family = type("Family", (), {"name": "ema", "discord_webhook": ""})()

    def fake_operationalize(thesis):
        updated = dict(thesis)
        updated["requested_primitives"] = ["buffered_trailing_stop_rule"]
        updated["config_changes"] = {"buffered_trailing_stop_rule": True}
        return updated

    class _Validated:
        thesis_id = "buffered_trailing"

    def fake_validate(raw, prior_theses=None, tools_called=None, **kwargs):
        captured["validated_raw"] = dict(raw)
        return _Validated()

    def fake_compile(validated, root, artifact_root=None):
        captured["compiled"] = {
            "thesis_id": validated.thesis_id,
            "root": root,
            "artifact_root": artifact_root,
        }
        return None

    monkeypatch.setattr("compiler_pipeline.operationalize_thesis", fake_operationalize)
    monkeypatch.setattr("thesis_validator.validate_thesis_dict", fake_validate)
    monkeypatch.setattr("compiler_pipeline.compile_research_thesis", fake_compile)
    monkeypatch.setattr("autoresearch_research._close_run", lambda *args, **kwargs: None)

    state = {"state": "running", "job": 26, "research_round_in_progress": 6}
    result = {
        "generated_thesis_id": "buffered_trailing",
        "research_round": 6,
        "thesis": {
            "thesis_id": "buffered_trailing",
            "hypothesis": "buffered trailing should reduce premature exits",
            "mechanism": "trailing rule needs a code primitive",
            "mechanism_dimension": "exit_mechanism",
            "dimension_novelty": "new trailing rule shape",
            "expected_effects": [
                {
                    "metric": "profit_factor",
                    "direction": "increase",
                    "rationale": "fewer premature trail exits",
                }
            ],
            "disqualifiers": [
                {
                    "name": "trade_count_collapse",
                    "condition": "trade_count falls too much",
                    "severity": "hard_fail",
                }
            ],
            "requires_code_change": True,
            "requested_primitives": [],
        },
    }

    updated = _handle_needs_code(_Controller(), state, result)

    assert updated["state"] == "halted"
    assert captured["validated_raw"]["requested_primitives"] == [  # type: ignore[index]
        "buffered_trailing_stop_rule"
    ]
    assert captured["validated_raw"]["config_changes"] == {  # type: ignore[index]
        "buffered_trailing_stop_rule": True
    }


def test_handle_needs_code_materializes_builder_artifacts_on_schema_only_code_change_fallback(
    tmp_path: Path, monkeypatch
) -> None:
    captured: dict[str, object] = {}

    class _Controller:
        root = tmp_path
        job_runtime_root = tmp_path
        family = type("Family", (), {"name": "ema", "discord_webhook": ""})()

    def fake_operationalize(thesis):
        updated = dict(thesis)
        updated["requested_primitives"] = ["buffered_trailing_stop_rule"]
        updated["config_changes"] = {"buffered_trailing_stop_rule": True}
        return updated

    def fake_validate(raw, prior_theses=None, tools_called=None, **kwargs):
        captured["validated_raw"] = dict(raw)
        raise ThesisValidationError("Missing mechanism_dimension")

    def fake_compile(validated, root, artifact_root=None):
        captured["compiled"] = {
            "thesis_id": validated.thesis_id,
            "root": root,
            "artifact_root": artifact_root,
            "mechanism_dimension": validated.mechanism_dimension,
            "requested_primitives": list(validated.requested_primitives),
        }
        return None

    monkeypatch.setattr("compiler_pipeline.operationalize_thesis", fake_operationalize)
    monkeypatch.setattr("thesis_validator.validate_thesis_dict", fake_validate)
    monkeypatch.setattr("compiler_pipeline.compile_research_thesis", fake_compile)
    monkeypatch.setattr("autoresearch_research._close_run", lambda *args, **kwargs: None)

    state = {"state": "running", "job": 26, "research_round_in_progress": 6}
    result = {
        "generated_thesis_id": "buffered_trailing",
        "research_round": 6,
        "thesis": {
            "thesis_id": "buffered_trailing",
            "hypothesis": "buffered trailing should reduce premature exits",
            "mechanism": "trailing rule needs a code primitive",
            "dimension_novelty": "new trailing rule shape",
            "expected_effects": [
                {
                    "metric": "profit_factor",
                    "direction": "increase",
                    "rationale": "fewer premature trail exits",
                }
            ],
            "disqualifiers": [
                {
                    "name": "trade_count_collapse",
                    "condition": "trade_count falls too much",
                    "severity": "hard_fail",
                }
            ],
            "requires_code_change": True,
            "requested_primitives": [],
        },
    }

    updated = _handle_needs_code(_Controller(), state, result)

    assert updated["state"] == "halted"
    assert captured["validated_raw"]["requested_primitives"] == [  # type: ignore[index]
        "buffered_trailing_stop_rule"
    ]
    assert captured["compiled"] == {
        "thesis_id": "job-26-round-6-attempt-1",
        "root": tmp_path,
        "artifact_root": tmp_path / "research" / "round-6",
        "mechanism_dimension": "",
        "requested_primitives": ["buffered_trailing_stop_rule"],
    }


def test_handle_needs_code_preserves_retry_attempt_id_for_schema_only_fallback(
    tmp_path: Path, monkeypatch
) -> None:
    captured: dict[str, object] = {}

    class _Controller:
        root = tmp_path
        job_runtime_root = tmp_path
        family = type("Family", (), {"name": "ema", "discord_webhook": ""})()

    def fake_operationalize(thesis):
        updated = dict(thesis)
        updated["requested_primitives"] = ["buffered_trailing_stop_rule"]
        updated["config_changes"] = {"buffered_trailing_stop_rule": True}
        return updated

    def fake_validate(raw, prior_theses=None, tools_called=None, **kwargs):
        raise ThesisValidationError("Missing mechanism_dimension")

    def fake_compile(validated, root, artifact_root=None):
        captured["thesis_id"] = validated.thesis_id
        return None

    monkeypatch.setattr("compiler_pipeline.operationalize_thesis", fake_operationalize)
    monkeypatch.setattr("thesis_validator.validate_thesis_dict", fake_validate)
    monkeypatch.setattr("compiler_pipeline.compile_research_thesis", fake_compile)
    monkeypatch.setattr("autoresearch_research._close_run", lambda *args, **kwargs: None)

    state = {"state": "running", "job": 26, "research_round_in_progress": 6}
    result = {
        "generated_thesis_id": "job-26-round-6-attempt-2",
        "research_round": 6,
        "thesis": {
            "hypothesis": "buffered trailing should reduce premature exits",
            "mechanism": "trailing rule needs a code primitive",
            "dimension_novelty": "new trailing rule shape",
            "expected_effects": [
                {
                    "metric": "profit_factor",
                    "direction": "increase",
                    "rationale": "fewer premature trail exits",
                }
            ],
            "disqualifiers": [
                {
                    "name": "trade_count_collapse",
                    "condition": "trade_count falls too much",
                    "severity": "hard_fail",
                }
            ],
            "requires_code_change": True,
            "requested_primitives": [],
        },
    }

    _handle_needs_code(_Controller(), state, result)

    assert captured["thesis_id"] == "job-26-round-6-attempt-2"


def test_handle_needs_code_close_run_called_when_prepare_thesis_raises_type_error(
    tmp_path: Path, monkeypatch
) -> None:
    """Widened except-guard: TypeError in enrichment must not skip _close_run."""
    close_run_calls: list[object] = []

    class _Controller:
        root = tmp_path
        job_runtime_root = tmp_path
        family = type("Family", (), {"name": "ema", "discord_webhook": ""})()

    def fake_operationalize(thesis):
        raise TypeError("unexpected NoneType in operationalize")

    monkeypatch.setattr("compiler_pipeline.operationalize_thesis", fake_operationalize)
    monkeypatch.setattr(
        "autoresearch_research._close_run",
        lambda *args, **kwargs: close_run_calls.append(args),
    )

    state = {"state": "running", "job": 26, "research_round_in_progress": 6}
    result = {
        "generated_thesis_id": "buffered_trailing",
        "research_round": 6,
        "thesis": {
            "thesis_id": "buffered_trailing",
            "hypothesis": "buffered trailing should reduce premature exits",
            "mechanism": "trailing rule needs a code primitive",
            "mechanism_dimension": "exit_mechanism",
            "dimension_novelty": "new trailing rule shape",
            "expected_effects": [
                {
                    "metric": "profit_factor",
                    "direction": "increase",
                    "rationale": "fewer premature trail exits",
                }
            ],
            "disqualifiers": [
                {
                    "name": "trade_count_collapse",
                    "condition": "trade_count falls too much",
                    "severity": "hard_fail",
                }
            ],
            "requires_code_change": True,
            "requested_primitives": [],
        },
    }

    updated = _handle_needs_code(_Controller(), state, result)

    assert updated["state"] == "halted"
    assert len(close_run_calls) == 1, "_close_run must be called even when TypeError is raised"


def test_execute_research_sdk_persists_research_activity_before_conductor_call(
    tmp_path: Path, monkeypatch
) -> None:
    _write_ema_base_config(tmp_path)
    writes: list[dict[str, object]] = []
    started_rounds: list[dict[str, object]] = []

    class _DB:
        def ensure_round_started(self, **kwargs):
            started_rounds.append(dict(kwargs))

    class _Controller:
        root = tmp_path
        family = type("Family", (), {"name": "ema"})()
        backtest_run_db = _DB()

        def __init__(self) -> None:
            self.state = {"state": "blocked", "job": 26, "research_round": 7}

        def read_state(self):
            return dict(self.state)

        def write_state(self, state):
            self.state = dict(state)
            writes.append(dict(state))

        def read_results(self):
            return []

    monkeypatch.setattr(
        "agent_formatters.format_round_results_summary",
        lambda result_dicts, **kwargs: [],
    )
    monkeypatch.setattr("thesis_validator.load_prior_theses", lambda _root, **kwargs: [])
    monkeypatch.setattr(
        "autoresearch_research._resolve_conductor_inputs",
        lambda *args, **kwargs: ("trades.csv", "events.parquet", "diagnostics.json", {}),
    )
    monkeypatch.setattr("improvement_flags.reflexion_enabled", lambda: False)
    monkeypatch.setattr(
        "autoresearch_research._call_conductor",
        lambda *args, **kwargs: ConductorResult(
            status="should_stop", should_stop=True, reasoning="done"
        ),
    )
    monkeypatch.setattr(
        "autoresearch_research._check_parsed_for_terminal",
        lambda result: {
            "status": "completed",
            "generated_config": "runtime/jobs/job-26/research/round-8/selected_config.json",
            "should_stop": False,
        },
    )

    result = execute_research_sdk(_Controller())

    assert result["generated_config"] == "runtime/jobs/job-26/research/round-8/selected_config.json"
    assert writes[0]["research_round_in_progress"] == 8
    assert len(started_rounds) == 1
    assert started_rounds[0]["research_round_id"] == "job-26-round-8"
    assert started_rounds[0]["job_id"] == 26
    assert started_rounds[0]["round_number"] == 8
    assert str(started_rounds[0]["run_id"]).startswith("R-")
    assert writes[0]["activity"] == {
        "type": "research",
        "phase": "conductor_running",
        "round": 8,
    }


def test_handle_success_sets_pending_experiment_activity(tmp_path: Path) -> None:
    class _Family:
        @staticmethod
        def benchmark_command(config: str) -> str:
            return f"python bench.py --config {config}"

    class _Controller:
        family = _Family()

        def write_state(self, state):
            self.state = dict(state)

    state = {
        "state": "blocked",
        "job": 26,
        "research_round": 7,
        "research_round_in_progress": 8,
        "activity": {"type": "research", "phase": "conductor_running", "round": 8},
        "blockers": [{"kind": "research_required"}],
        "rejection_feedback": "prior",
    }
    result = {
        "generated_config": "runtime/jobs/job-26/research/round-8/selected_config.json",
        "thesis_id": "abc",
    }

    updated = _handle_success(_Controller(), state, result, research_round=8)

    assert updated["state"] == "running"
    assert updated["activity"] == {
        "type": "round",
        "phase": "pending_backtest",
        "round": 8,
        "config": "runtime/jobs/job-26/research/round-8/selected_config.json",
        "thesis_id": "abc",
    }


def test_handle_round_failure_clears_activity(tmp_path: Path) -> None:
    class _Controller:
        root = tmp_path
        research_dir = tmp_path / "research"

        def write_state(self, state):
            self.state = dict(state)

    state = {
        "state": "blocked",
        "job": 26,
        "research_round": 7,
        "research_round_in_progress": 8,
        "activity": {"type": "research", "phase": "conductor_running", "round": 8},
    }
    result = {"validation_failure_reason": "no thesis generated"}

    updated = _handle_round_failure(_Controller(), state, result, research_round=8)

    assert updated["research_round"] == 8
    assert "activity" not in updated
