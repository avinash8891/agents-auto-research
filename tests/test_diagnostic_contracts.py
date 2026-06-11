from __future__ import annotations

from diagnostic_contracts import build_required_diagnostic_specs, enrich_required_diagnostics


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


def test_legacy_max_drawdown_key_is_registered_alias() -> None:
    specs = build_required_diagnostic_specs(["max_drawdown_and_pct_profitable_windows_vs_base"])

    assert len(specs) == 1
    assert specs[0].key == "max_drawdown_vs_base"
    assert specs[0].surface == "experiment_evaluation"
