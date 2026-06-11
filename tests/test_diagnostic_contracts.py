from __future__ import annotations

import pytest

from diagnostic_contracts import build_required_diagnostic_specs, enrich_required_diagnostics


def test_enrich_required_diagnostics_rejects_retired_window_metric_key() -> None:
    with pytest.raises(ValueError, match="retired diagnostic"):
        enrich_required_diagnostics(
            [
                {
                    "key": "max_drawdown_and_pct_profitable_windows_vs_base",
                    "surface": "experiment_evaluation",
                }
            ],
            baseline_metrics={"max_drawdown": 0.20},
            candidate_metrics={"max_drawdown": 0.25},
        )


@pytest.mark.parametrize(
    "diagnostic_key",
    [
        "max_drawdown_and_pct_profitable_windows_vs_base",
        "max_drawdown_and_pct_profitable_windows_versus_base",
        "pct_profitable_windows_and_max_drawdown_change_relative_to_base",
    ],
)
def test_build_required_diagnostic_specs_rejects_retired_window_metric_aliases(
    diagnostic_key: str,
) -> None:
    with pytest.raises(ValueError, match="retired diagnostic"):
        build_required_diagnostic_specs([diagnostic_key])


def test_enrich_required_diagnostics_supports_max_drawdown_key() -> None:
    enriched = enrich_required_diagnostics(
        [
            {
                "key": "max_drawdown_vs_base",
                "surface": "experiment_evaluation",
            }
        ],
        baseline_metrics={"max_drawdown": 0.20},
        candidate_metrics={"max_drawdown": 0.25},
    )

    assert enriched["max_drawdown_vs_base"] == {
        "candidate_max_drawdown": 0.25,
        "base_max_drawdown": 0.20,
        "delta_max_drawdown": 0.05,
    }


def test_max_drawdown_key_is_registered() -> None:
    specs = build_required_diagnostic_specs(["max_drawdown_vs_base"])

    assert len(specs) == 1
    assert specs[0].key == "max_drawdown_vs_base"
    assert specs[0].surface == "experiment_evaluation"
