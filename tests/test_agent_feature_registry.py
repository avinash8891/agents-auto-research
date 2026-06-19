from __future__ import annotations

from pathlib import Path

import pytest

from agent_feature_registry import (
    AgentFeatureRegistryError,
    active_agent_feature_columns,
    mark_agent_features_validated,
    prune_agent_feature,
    register_agent_feature,
)


def test_register_feature_adds_family_status(tmp_path: Path) -> None:
    register_agent_feature(
        tmp_path,
        column="rvol_spike",
        formula="rvol / rolling_mean(rvol, 20)",
        required_data=["ohlcv"],
        family_name="ema",
        thesis_id="ema-1",
    )
    register_agent_feature(
        tmp_path,
        column="rvol_spike",
        formula="rvol / rolling_mean(rvol, 20)",
        required_data=["ohlcv"],
        family_name="orb",
        thesis_id="orb-1",
    )

    assert active_agent_feature_columns(tmp_path, "ema") == frozenset({"rvol_spike"})
    assert active_agent_feature_columns(tmp_path, "orb") == frozenset({"rvol_spike"})


def test_register_feature_rejects_formula_conflict(tmp_path: Path) -> None:
    register_agent_feature(
        tmp_path,
        column="rvol_spike",
        formula="rvol / rolling_mean(rvol, 20)",
        required_data=["ohlcv"],
        family_name="ema",
        thesis_id="ema-1",
    )

    with pytest.raises(AgentFeatureRegistryError, match="formula conflict"):
        register_agent_feature(
            tmp_path,
            column="rvol_spike",
            formula="rvol > 2",
            required_data=["ohlcv"],
            family_name="ema",
            thesis_id="ema-2",
        )


def test_register_feature_rejects_unknown_dependency(tmp_path: Path) -> None:
    with pytest.raises(AgentFeatureRegistryError, match="unknown dependency"):
        register_agent_feature(
            tmp_path,
            column="signed_volume_spike",
            formula="signed_volume_z / rolling_mean(signed_volume_z, 20)",
            required_data=["ohlcv"],
            family_name="ema",
            thesis_id="ema-1",
        )


def test_register_feature_rejects_inactive_and_cross_family_dependencies(tmp_path: Path) -> None:
    register_agent_feature(
        tmp_path,
        column="rvol_spike",
        formula="rvol / rolling_mean(rvol, 20)",
        required_data=["ohlcv"],
        family_name="orb",
        thesis_id="orb-1",
    )
    with pytest.raises(AgentFeatureRegistryError, match="unknown dependency"):
        register_agent_feature(
            tmp_path,
            column="rvol_spike_2",
            formula="rvol_spike / rolling_mean(rvol_spike, 20)",
            required_data=["ohlcv"],
            family_name="ema",
            thesis_id="ema-1",
        )
    register_agent_feature(
        tmp_path,
        column="rvol_spike",
        formula="rvol / rolling_mean(rvol, 20)",
        required_data=["ohlcv"],
        family_name="ema",
        thesis_id="ema-1",
    )
    prune_agent_feature(tmp_path, column="rvol_spike", family_name="ema")
    with pytest.raises(AgentFeatureRegistryError, match="unknown dependency"):
        register_agent_feature(
            tmp_path,
            column="rvol_spike_2",
            formula="rvol_spike / rolling_mean(rvol_spike, 20)",
            required_data=["ohlcv"],
            family_name="ema",
            thesis_id="ema-2",
        )


def test_register_feature_rejects_cycles(tmp_path: Path) -> None:
    with pytest.raises(AgentFeatureRegistryError, match="cyclic"):
        register_agent_feature(
            tmp_path,
            column="rvol_spike",
            formula="rvol_spike / rolling_mean(rvol_spike, 20)",
            required_data=["ohlcv"],
            family_name="ema",
            thesis_id="ema-1",
        )


def test_inactive_feature_reactivates_only_with_same_formula(tmp_path: Path) -> None:
    register_agent_feature(
        tmp_path,
        column="rvol_spike",
        formula="rvol / rolling_mean(rvol, 20)",
        required_data=["ohlcv"],
        family_name="ema",
        thesis_id="ema-1",
    )
    prune_agent_feature(tmp_path, column="rvol_spike", family_name="ema")

    register_agent_feature(
        tmp_path,
        column="rvol_spike",
        formula="rvol / rolling_mean(rvol, 20)",
        required_data=["ohlcv"],
        family_name="ema",
        thesis_id="ema-2",
    )

    assert active_agent_feature_columns(tmp_path, "ema") == frozenset({"rvol_spike"})


def test_pruning_cascades_to_dependents(tmp_path: Path) -> None:
    register_agent_feature(
        tmp_path,
        column="rvol_spike",
        formula="rvol / rolling_mean(rvol, 20)",
        required_data=["ohlcv"],
        family_name="ema",
        thesis_id="ema-1",
    )
    register_agent_feature(
        tmp_path,
        column="rvol_spike_rank",
        formula="rolling_rank(rvol_spike, 20)",
        required_data=["ohlcv"],
        family_name="ema",
        thesis_id="ema-2",
    )

    prune_agent_feature(tmp_path, column="rvol_spike", family_name="ema")

    assert active_agent_feature_columns(tmp_path, "ema") == frozenset()


def test_walkforward_graduation_marks_requested_features_validated(tmp_path: Path) -> None:
    register_agent_feature(
        tmp_path,
        column="rvol_spike",
        formula="rvol / rolling_mean(rvol, 20)",
        required_data=["ohlcv"],
        family_name="ema",
        thesis_id="ema-1",
    )

    mark_agent_features_validated(tmp_path, family_name="ema", thesis_id="ema-1")

    payload = (tmp_path / "runtime" / "agent_features.jsonl").read_text()
    assert '"status": "validated"' in payload
