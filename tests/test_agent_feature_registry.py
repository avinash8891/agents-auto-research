from __future__ import annotations

from pathlib import Path

import pytest

from agent_feature_registry import (
    AgentFeatureRegistryError,
    active_agent_feature_columns,
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
