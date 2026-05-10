"""Tests that hypothesis-config alignment is strategy-aware.

The validator must be strategy-agnostic: rules that need strategy-specific
data MUST read it from FamilyResearchSpec, not hardcode it. Previously, the
KEY_CONCEPTS regex map was EMA-flavored and lived inside the validator,
which meant ORB and any future family scored 1.0 against an empty intersect
(silently disabling the rule). This test file enforces the new contract:
each FamilyResearchSpec carries its own key_concepts; the validator looks
them up by family.
"""

from __future__ import annotations

from typing import Any

import pytest

from family_research_spec import FamilyResearchSpec, get_family_research_spec
from research_types import BacktestContract
from thesis_validator import (
    ThesisValidationError,
    check_hypothesis_alignment,
    validate_stage_2,
)


def _make_contract(
    *,
    strategy_family: str,
    runtime_config: dict[str, Any],
    hypothesis: str = "",
    mechanism: str = "",
) -> BacktestContract:
    return BacktestContract(
        contract_id="contract-test",
        thesis_id="alignment_family_aware_test",
        strategy_family=strategy_family,
        baseline_config_path=f"configs/{strategy_family}_base.yaml",
        runtime_config=runtime_config,
        hypothesis=hypothesis,
        mechanism=mechanism,
    )


# ── FamilyResearchSpec carries key_concepts ──────────────────────────────


def test_family_research_spec_has_key_concepts_field() -> None:
    """key_concepts is a first-class field on FamilyResearchSpec."""
    assert "key_concepts" in {f.name for f in FamilyResearchSpec.__dataclass_fields__.values()}


def test_ema_research_spec_populates_key_concepts() -> None:
    """The EMA strategy ships its own concept map (the historical hardcoded subset)."""
    spec = get_family_research_spec("ema")
    assert spec.key_concepts, "EMA family must populate key_concepts"
    # Sanity: concepts cover keys that appear in EMA's allowed_config_keys.
    assert "entry_cutoff_time" in spec.key_concepts
    assert "rr_ratio" in spec.key_concepts


def test_orb_research_spec_key_concepts_default_empty() -> None:
    """ORB ships without alignment scoring (rule fails open) until the strategy
    author opts in by populating key_concepts on its spec."""
    spec = get_family_research_spec("orb")
    assert spec.key_concepts == {}


# ── Alignment scoring honors the family parameter ────────────────────────


def test_check_hypothesis_alignment_uses_family_specific_concepts() -> None:
    """An EMA-themed config + EMA-themed hypothesis scores 1.0 under family='ema'."""
    score, _ = check_hypothesis_alignment(
        hypothesis="Restrict entries to the morning entry window and keep only the first trade per day.",
        mechanism="Capture the opening dislocation only.",
        config_changes={"entry_cutoff_time": "10:00", "max_trades_per_day": 1},
        family_name="ema",
    )
    assert score == 1.0


def test_check_hypothesis_alignment_unknown_family_fails_open() -> None:
    """An unregistered family has no concept map → every key is unknown →
    score is 1.0 (benefit of doubt)."""
    score, explanation = check_hypothesis_alignment(
        hypothesis="Anything",
        mechanism="Anything",
        config_changes={"some_key": 1, "other_key": 2},
        family_name="nonexistent_family",
    )
    assert score == 1.0
    # Surface the fail-open behavior explicitly so operators notice unwired families.
    assert "concept" in explanation.lower() or "no" in explanation.lower()


def test_check_hypothesis_alignment_orb_family_fails_open() -> None:
    """ORB ships with empty key_concepts; alignment must not punish ORB theses."""
    score, _ = check_hypothesis_alignment(
        hypothesis="Filter ORB entries by stocks-in-play universe.",
        mechanism="Restrict the universe to high-liquidity names.",
        config_changes={"stocks_in_play_top_n": 20, "universe_mode": "stocks_in_play"},
        family_name="orb",
    )
    assert score == 1.0


def test_check_hypothesis_alignment_ema_misaligned_still_rejects() -> None:
    """When a family has concepts and the config keys are in the map but don't
    match the hypothesis, score drops below threshold — EMA rule still bites."""
    score, _ = check_hypothesis_alignment(
        hypothesis="Filter setups by minimum opening volatility to avoid noise.",
        mechanism="Low-volatility opens have weaker microstructure signals.",
        config_changes={
            "entry_cutoff_time": "10:00",
            "rr_ratio": 2.5,
            "gap_filter": True,
            "gap_pct": 0.01,
        },
        family_name="ema",
    )
    assert score < 0.4


# ── Stage 2 routes the strategy_family to the alignment check ────────────


def test_validate_stage_2_passes_strategy_family_to_alignment() -> None:
    """Stage 2 reads contract.strategy_family and uses the family's concept map.
    A misaligned EMA contract is rejected; the same misaligned config under an
    unregistered family fails open."""
    misaligned_runtime = {
        "entry_cutoff_time": "10:00",
        "rr_ratio": 2.5,
        "gap_filter": True,
        "gap_pct": 0.01,
    }
    ema_contract = _make_contract(
        strategy_family="ema",
        runtime_config=misaligned_runtime,
        hypothesis="Filter setups by minimum opening volatility to avoid noise.",
        mechanism="Low-volatility opens have weaker microstructure signals.",
    )
    with pytest.raises(ThesisValidationError) as excinfo:
        validate_stage_2(ema_contract)
    assert excinfo.value.rejection_code == "hypothesis_config_misalignment"

    unwired_contract = _make_contract(
        strategy_family="nonexistent_family",
        runtime_config=misaligned_runtime,
        hypothesis="Filter setups by minimum opening volatility to avoid noise.",
        mechanism="Low-volatility opens have weaker microstructure signals.",
    )
    # No raise: unregistered family has no concept map and the rule fails open.
    validate_stage_2(unwired_contract)
