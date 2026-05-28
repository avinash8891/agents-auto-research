"""Tests for the Stage 1 / Stage 2 validator split."""

from __future__ import annotations

from typing import Any

import pytest

from research_types import BacktestContract, DiagnosticRequirementSpec
from thesis_validator import (
    ALIGNMENT_THRESHOLD,
    ThesisValidationError,
)
from thesis_validator import validate_research_thesis as _validate_research_thesis
from thesis_validator import (
    validate_stage_1,
    validate_stage_2,
)
from thesis_validator import validate_thesis_dict as _validate_thesis_dict

from thesis_validator import VALID_PROCESS_TOOLS as _VALID_PROCESS_TOOLS


def validate_research_thesis(*args: Any, **kwargs: Any) -> Any:
    kwargs.setdefault("tools_called", _VALID_PROCESS_TOOLS)
    return _validate_research_thesis(*args, **kwargs)


def validate_thesis_dict(*args: Any, **kwargs: Any) -> Any:
    kwargs.setdefault("tools_called", _VALID_PROCESS_TOOLS)
    return _validate_thesis_dict(*args, **kwargs)


def _base_thesis() -> dict:
    return {
        "thesis_id": "stage1_test_thesis",
        "strategy_family": "ema",
        "hypothesis": "Filter setups by minimum opening volatility to avoid noise.",
        "mechanism": "Low-volatility opens have weaker microstructure signals.",
        "mechanism_dimension": "signal_quality",
        "dimension_novelty": (
            "This tests a volatility floor on the alert candle, a fundamentally "
            "different lever than entry-time gating which has been explored before."
        ),
        "config_changes": {"alert_min_atr_pct": 0.001},
        "disqualifiers": [
            {
                "name": "trade_count_collapse",
                "condition": "trade_count decreases by more than 50 percent",
                "severity": "hard_fail",
                "kind": "metric_threshold",
            },
            {
                "name": "no_volatility_separation",
                "condition": (
                    "If average bar-volatility quintile PF spread is below 0.2 in the data, "
                    "the volatility-quality mechanism does not hold."
                ),
                "severity": "hard_fail",
                "kind": "mechanism_evidence",
            },
        ],
        "closest_prior_theses_considered": ["prior_volatility_baseline"],
        "orthogonality_defense": (
            "Volatility floor on the alert candle is a different lever family than entry timing."
        ),
        "evidence_strength": "mixed",
        "alternatives_considered": [
            {
                "mechanism": "RVOL gate on stocks-in-play list",
                "why_rejected": "we lack the relative-volume data for the train period",
            },
            {
                "mechanism": "opening drive directional gate",
                "why_rejected": "weaker disconfirmer than the volatility-floor mechanism here",
            },
        ],
        "evidence_citations": [
            {"source": "web_search", "citation": "intraday volatility microstructure paper"},
            {"source": "analyst", "citation": "round-3 analyst: low-vol opens have weaker PF"},
        ],
        "source_code_verification": (
            "strategies/ema/signals.py:generate_signals_for_frame is where the volatility "
            "floor on the alert candle would gate entries by ATR-percent."
        ),
        "expected_effects": [
            {
                "metric": "profit_factor",
                "direction": "increase",
                "rationale": "Higher-volatility setups should follow through more reliably.",
            },
            {
                "metric": "trade_count",
                "direction": "decrease_or_same",
                "rationale": "Filtering low-volatility setups reduces but does not collapse counts.",
            },
        ],
        # Required field post-refactor: doctrine was always to require a
        # disconfirmer; the validator now enforces presence, not just length.
        "falsification_or_alternative": (
            "If high-volatility and low-volatility setups show the same PF distribution, "
            "the volatility-quality mechanism does not hold."
        ),
    }


def _make_contract(
    *,
    runtime_config: dict[str, Any],
    hypothesis: str = "",
    mechanism: str = "",
    required_diagnostics: list[str] | None = None,
    required_diagnostic_specs: list[DiagnosticRequirementSpec] | None = None,
) -> BacktestContract:
    return BacktestContract(
        contract_id="contract-test",
        thesis_id="stage2_test_thesis",
        strategy_family="ema",
        baseline_config_path="configs/ema_base.yaml",
        runtime_config=runtime_config,
        hypothesis=hypothesis,
        mechanism=mechanism,
        required_diagnostics=list(required_diagnostics or []),
        required_diagnostic_specs=list(required_diagnostic_specs or []),
    )


# ── Stage 1 sanity ────────────────────────────────────────────────────────


def test_validate_stage_1_accepts_well_formed_thesis() -> None:
    validated = validate_thesis_dict(_base_thesis())
    out = validate_stage_1(
        validated,
        prior_theses=[],
        tools_called=_VALID_PROCESS_TOOLS,
    )
    assert out.thesis_id == "stage1_test_thesis"


def test_validate_stage_1_rejects_missing_mechanism() -> None:
    raw = _base_thesis()
    raw["mechanism"] = ""
    with pytest.raises(ThesisValidationError, match="mechanism"):
        validate_thesis_dict(raw)


def test_validate_research_thesis_does_not_run_alignment_at_stage_1() -> None:
    """Alignment is now a Stage 2 rule. A thesis whose config_changes don't match
    its hypothesis must still pass Stage 1 — the gate fires post-compile."""
    raw = _base_thesis()
    # Force misalignment: keys we know are in KEY_CONCEPTS but the hypothesis
    # talks about volatility, not these.
    raw["config_changes"] = {
        "entry_cutoff_time": "10:00",
        "rr_ratio": 2.5,
        "trail_after_r": 1.0,
        "max_hold_bars": 30,
    }
    raw["hypothesis"] = "Filter setups by minimum opening volatility to avoid noise."
    raw["mechanism"] = "Low-volatility opens have weaker microstructure signals."

    # Stage 1 must accept this.
    validated = validate_thesis_dict(raw)
    assert validated.thesis_id == raw["thesis_id"]
    # validate_research_thesis must also accept (it's the Stage 1 alias).
    again = validate_research_thesis(validated, prior_theses=[])
    assert again.thesis_id == raw["thesis_id"]


# ── Stage 2: hypothesis-config alignment ──────────────────────────────────


def test_validate_stage_2_rejects_when_alignment_below_threshold() -> None:
    """All known-but-misaligned keys → score 0/N < threshold → reject."""
    runtime_config = {
        "entry_cutoff_time": "10:00",
        "rr_ratio": 2.5,
        "gap_filter": True,
        "gap_pct": 0.01,
        "direction_bias": "long_only",
    }
    contract = _make_contract(
        runtime_config=runtime_config,
        hypothesis="Filter setups by minimum opening volatility to avoid noise.",
        mechanism="Low-volatility opens have weaker microstructure signals.",
    )
    with pytest.raises(ThesisValidationError) as excinfo:
        validate_stage_2(contract)

    assert excinfo.value.rejection_code == "hypothesis_config_misalignment"
    assert "score" in str(excinfo.value).lower()


def test_validate_stage_2_accepts_when_alignment_at_or_above_threshold() -> None:
    """When config keys match the hypothesis concepts, score ≥ threshold → accept."""
    runtime_config = {
        "entry_cutoff_time": "10:00",
        "max_trades_per_day": 1,
    }
    contract = _make_contract(
        runtime_config=runtime_config,
        hypothesis="Restrict entries to the morning entry window and keep only the first trade per day.",
        mechanism="Capture the opening dislocation only and avoid lower-quality setups.",
    )
    out = validate_stage_2(contract)
    assert out is contract


def test_validate_stage_2_uses_runtime_config_not_thesis_config_changes() -> None:
    """Even with an empty `config_changes` worth of misalignment, Stage 2 evaluates
    the resolved runtime_config — confirming the input source moved."""
    # runtime_config carries inherited baseline keys; alignment should reflect those.
    runtime_config = {
        "entry_cutoff_time": "10:00",
        "max_trades_per_day": 1,
    }
    contract = _make_contract(
        runtime_config=runtime_config,
        hypothesis="Restrict entries to the morning entry window and keep only the first trade per day.",
        mechanism="Capture the opening dislocation only.",
    )
    # Score should be high; no exception raised.
    validate_stage_2(contract)


# ── Stage 2: required-diagnostic resolution ───────────────────────────────


def test_validate_stage_2_rejects_when_required_diagnostic_unresolved() -> None:
    """A required diagnostic that doesn't appear in required_diagnostic_specs
    or in runtime_config is a contract-build bug — Stage 2 catches it."""
    contract = _make_contract(
        runtime_config={
            "entry_cutoff_time": "10:00",
            "max_trades_per_day": 1,
        },
        hypothesis="Restrict entries to the morning entry window and keep only the first trade per day.",
        mechanism="Capture the opening dislocation only.",
        required_diagnostics=["volatility_quintile_pf_spread"],
        required_diagnostic_specs=[],  # compiler dropped it
    )
    with pytest.raises(ThesisValidationError) as excinfo:
        validate_stage_2(contract)

    assert excinfo.value.rejection_code == "required_diagnostic_missing_post_compile"
    assert "volatility_quintile_pf_spread" in str(excinfo.value)


def test_validate_stage_2_accepts_when_required_diagnostic_resolves_via_spec() -> None:
    spec = DiagnosticRequirementSpec(
        key="volatility_quintile_pf_spread",
        surface="strategy_diagnostics",
    )
    contract = _make_contract(
        runtime_config={
            "entry_cutoff_time": "10:00",
            "max_trades_per_day": 1,
        },
        hypothesis="Restrict entries to the morning entry window and keep only the first trade per day.",
        mechanism="Capture the opening dislocation only.",
        required_diagnostics=["volatility_quintile_pf_spread"],
        required_diagnostic_specs=[spec],
    )
    validate_stage_2(contract)  # no raise


def test_validate_stage_2_accepts_when_required_diagnostic_resolves_via_alias() -> None:
    spec = DiagnosticRequirementSpec(
        key="canonical_diag_key",
        surface="strategy_diagnostics",
        aliases=["volatility_quintile_pf_spread"],
    )
    contract = _make_contract(
        runtime_config={
            "entry_cutoff_time": "10:00",
            "max_trades_per_day": 1,
        },
        hypothesis="Restrict entries to the morning entry window and keep only the first trade per day.",
        mechanism="Capture the opening dislocation only.",
        required_diagnostics=["volatility_quintile_pf_spread"],
        required_diagnostic_specs=[spec],
    )
    validate_stage_2(contract)


def test_validate_stage_2_accepts_when_required_diagnostic_appears_in_runtime_config() -> None:
    contract = _make_contract(
        runtime_config={
            "entry_cutoff_time": "10:00",
            "max_trades_per_day": 1,
            "diagnostics": {
                "regime_breakdown": {"by": "session"},
            },
        },
        hypothesis="Restrict entries to the morning entry window and keep only the first trade per day.",
        mechanism="Capture the opening dislocation only.",
        required_diagnostics=["regime_breakdown"],
        required_diagnostic_specs=[],
    )
    validate_stage_2(contract)


def test_validate_stage_2_alignment_constant_unchanged() -> None:
    """Sanity: ALIGNMENT_THRESHOLD remains 0.4 — the gate logic is unchanged,
    only the input source moved."""
    assert ALIGNMENT_THRESHOLD == 0.4
