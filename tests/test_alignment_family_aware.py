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


def test_orb_research_spec_carries_shared_concepts() -> None:
    """ORB declares concept patterns for keys it shares with other families
    (rr_ratio, max_hold_bars). Family-specific ORB keys (universe_mode,
    or_minutes, etc.) remain absent until the strategy author populates them;
    those keys fall through to the unknown-key benefit-of-doubt branch in
    `check_hypothesis_alignment`."""
    spec = get_family_research_spec("orb")
    assert "rr_ratio" in spec.key_concepts
    assert "max_hold_bars" in spec.key_concepts


# ── Cross-family non-bleed + shared-pattern consistency ──────────────────


def test_key_concepts_scoped_to_owning_family() -> None:
    """A key in EMA's concept map is scored under EMA but treated as unknown
    under any other family. Ensures concept maps stay family-scoped: changing
    EMA's gap_exclude pattern must never affect ORB's alignment decisions."""
    config_changes = {"gap_exclude": True}
    # Hypothesis + mechanism deliberately avoid the words "gap" and "overnight"
    # so EMA's gap_exclude regex cannot match (otherwise the cross-family
    # comparison wouldn't isolate the family-scoping behavior).
    hypothesis = "Reduce false breaks at the open by tightening regime filters."
    mechanism = "Regime filters reduce noise from low-quality session prints."

    ema_score, _ = check_hypothesis_alignment(
        hypothesis=hypothesis,
        mechanism=mechanism,
        config_changes=config_changes,
        family_name="ema",
    )
    orb_score, _ = check_hypothesis_alignment(
        hypothesis=hypothesis,
        mechanism=mechanism,
        config_changes=config_changes,
        family_name="orb",
    )
    # EMA recognizes gap_exclude; the hypothesis doesn't say "gap" → misaligned.
    assert ema_score == 0.0
    # ORB does not catalog gap_exclude → unknown key → benefit of doubt.
    assert orb_score == 1.0


def test_shared_concept_pattern_consistency_across_families() -> None:
    """rr_ratio and max_hold_bars patterns are intentionally duplicated across
    EMA and ORB. This test makes the # SHARED with X comment markers
    enforceable: if someone updates one family's pattern and forgets the other,
    this test catches the drift before merge.

    If a future change intentionally diverges a pattern, remove that key from
    one family's key_concepts (so it falls through to family-specific) rather
    than letting two truths-of-record live in parallel.
    """
    ema = get_family_research_spec("ema")
    orb = get_family_research_spec("orb")
    for shared_key in ("rr_ratio", "max_hold_bars"):
        assert ema.key_concepts[shared_key] == orb.key_concepts[shared_key], (
            f"shared concept '{shared_key}' has drifted between EMA and ORB; "
            f"if you intentionally diverged, remove it from one family's "
            f"key_concepts instead of keeping two parallel definitions."
        )


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


def test_check_hypothesis_alignment_unknown_family_now_fails_loud() -> None:
    """An unregistered family has no concept map → previously fell open silently
    (returning score 1.0). That let entire families skip Stage 2 alignment
    enforcement undetected. The validator now raises
    `MissingFamilyKeyConceptsError` so operators see the gap rather than ignore it.
    """
    from thesis_validator import MissingFamilyKeyConceptsError

    with pytest.raises(MissingFamilyKeyConceptsError, match="nonexistent_family"):
        check_hypothesis_alignment(
            hypothesis="Anything",
            mechanism="Anything",
            config_changes={"some_key": 1, "other_key": 2},
            family_name="nonexistent_family",
        )


def test_check_hypothesis_alignment_orb_family_fails_open_for_uncatalogued_keys() -> None:
    """ORB's key_concepts only declares patterns for shared keys today
    (rr_ratio, max_hold_bars). ORB-specific keys like universe_mode and
    stocks_in_play_top_n fall through to the unknown-key benefit-of-doubt
    branch — alignment must not punish ORB theses for keys the strategy
    author hasn't catalogued yet."""
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
    # Unregistered family used to fall open silently; the harness now raises
    # `hypothesis_config_alignment_unconfigured` so operators MUST populate
    # FamilyResearchSpec.key_concepts for each family before Stage 2 runs.
    with pytest.raises(ThesisValidationError) as unwired_exc:
        validate_stage_2(unwired_contract)
    assert unwired_exc.value.rejection_code == "hypothesis_config_alignment_unconfigured"
