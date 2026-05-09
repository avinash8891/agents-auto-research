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
    _handle_needs_code,
    _handle_round_failure,
    _handle_success,
    _try_one_validation_attempt,
    accumulate_job_usage,
    execute_research_sdk,
    log_research_round,
    notify_discord,
    results_to_dicts,
)
from autoresearch_state import BacktestResultRecord, write_state
from backtest_run_db import BacktestRunDB
from thesis_validator import ThesisValidationError

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

    def fake_validate(raw, prior_theses=None):
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

    result, retry_feedback = _try_one_validation_attempt(
        _Controller(),
        2,
        0,
        {
            "reasoning": "code change thesis",
            "suggested_theses": [
                {
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
                }
            ],
            "should_stop": False,
        },
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
        "thesis_validator.validate_thesis_dict", lambda raw, prior_theses=None: _Validated()
    )
    monkeypatch.setattr(
        "compiler_pipeline.compile_research_thesis",
        lambda validated, root, artifact_root=None: _Contract(),
    )
    monkeypatch.setattr("autoresearch_research.load_baseline_config", lambda root, family: {})

    controller = _Controller()
    controller.backtest_run_db = type("DB", (), {"latest": lambda self, n: []})()

    parsed = {
        "reasoning": "candidate looks good",
        "suggested_theses": [
            {
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
            }
        ],
        "should_stop": False,
    }

    result, retry_feedback = _try_one_validation_attempt(
        controller,
        8,
        0,
        parsed,
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

        def log_research_round(self, *args, **kwargs):
            self.rejected.append((args, kwargs))

    def fake_operationalize(thesis):
        raise ValueError("missing_primitives must be a list of strings")

    monkeypatch.setattr("compiler_pipeline.operationalize_thesis", fake_operationalize)

    result, retry_feedback = _try_one_validation_attempt(
        _Controller(),
        2,
        0,
        {
            "reasoning": "code change thesis",
            "suggested_theses": [
                {
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
                }
            ],
            "should_stop": False,
        },
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

    def fake_validate(raw, prior_theses=None):
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

    def fake_validate(raw, prior_theses=None):
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
        "thesis_id": "buffered_trailing",
        "root": tmp_path,
        "artifact_root": tmp_path,
        "mechanism_dimension": "",
        "requested_primitives": ["buffered_trailing_stop_rule"],
    }


def test_execute_research_sdk_persists_research_activity_before_conductor_call(
    tmp_path: Path, monkeypatch
) -> None:
    writes: list[dict[str, object]] = []

    class _Controller:
        root = tmp_path
        family = type("Family", (), {"name": "ema"})()

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
        "agent_formatters.format_experiment_results_summary", lambda result_dicts: []
    )
    monkeypatch.setattr("thesis_validator.load_prior_theses", lambda _root: [])
    monkeypatch.setattr(
        "autoresearch_research._resolve_conductor_inputs",
        lambda *args, **kwargs: ("trades.csv", "events.parquet", "diagnostics.json", {}),
    )
    monkeypatch.setattr("improvement_flags.reflexion_enabled", lambda: False)
    monkeypatch.setattr(
        "autoresearch_research._call_conductor",
        lambda *args, **kwargs: {"status": "completed", "reasoning": "done"},
    )
    monkeypatch.setattr(
        "autoresearch_research._check_parsed_for_terminal",
        lambda parsed: {
            "status": "completed",
            "generated_config": "runtime/jobs/job-26/research/round-8/selected_config.json",
            "should_stop": False,
        },
    )

    result = execute_research_sdk(_Controller())

    assert result["generated_config"] == "runtime/jobs/job-26/research/round-8/selected_config.json"
    assert writes[0]["research_round_in_progress"] == 8
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
        "type": "experiment",
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
    result = {"rejection_reason": "no thesis generated"}

    updated = _handle_round_failure(_Controller(), state, result, research_round=8)

    assert updated["research_round"] == 8
    assert "activity" not in updated
