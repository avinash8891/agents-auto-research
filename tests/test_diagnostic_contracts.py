from __future__ import annotations

from diagnostic_contracts import enrich_required_diagnostics


def test_enrich_required_diagnostics_supports_legacy_max_drawdown_key() -> None:
    enriched = enrich_required_diagnostics(
        [
            {
                "key": "max_drawdown_and_pct_profitable_windows_vs_base",
                "surface": "experiment_evaluation",
            }
        ],
        baseline_metrics={"max_drawdown": 0.20},
        candidate_metrics={"max_drawdown": 0.25},
    )

    assert enriched["max_drawdown_and_pct_profitable_windows_vs_base"] == {
        "candidate_max_drawdown": 0.25,
        "base_max_drawdown": 0.20,
        "delta_max_drawdown": 0.05,
    }
