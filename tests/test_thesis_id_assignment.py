from __future__ import annotations

from backtest_run_db import BacktestRunDB, research_thesis_attempt_id
from thesis_validator import validate_thesis_dict


def _valid_thesis_payload() -> dict:
    return {
        "strategy_family": "ema",
        "hypothesis": "Skipping opening auction noise improves entry quality.",
        "mechanism": "The first minutes have thin liquidity and noisy fills.",
        "mechanism_dimension": "entry_timing",
        "dimension_novelty": "Tests session timing instead of threshold tuning.",
        "config_changes": {"opening_skip_minutes": 5},
        "expected_effects": [
            {
                "metric": "profit_factor",
                "direction": "increase",
                "rationale": "Skipping noisy opening entries should improve realized edge.",
            },
            {
                "metric": "trade_count",
                "direction": "decrease_or_same",
                "rationale": "The entry filter should reduce but not collapse trade activity.",
            },
        ],
        "disqualifiers": [
            {
                "name": "opening_noise_not_concentrated",
                "condition": "Opening-window losses are not concentrated in the first five minutes.",
                "kind": "mechanism_evidence",
            }
        ],
        "falsification_or_alternative": (
            "If first-five-minute losses are not worse than later-session losses, "
            "the auction-noise mechanism is false."
        ),
        "evidence_strength": "mixed",
        "alternatives_considered": [
            {
                "mechanism": "wider stop-distance cap",
                "why_rejected": (
                    "This would tune risk after entry rather than test whether opening "
                    "entry timing is the noisy mechanism."
                ),
            },
            {
                "mechanism": "trend-strength filter",
                "why_rejected": (
                    "This would test directional continuation rather than isolate the "
                    "opening liquidity mechanism."
                ),
            },
        ],
        "evidence_citations": [
            {"source": "web_search", "citation": "Opening auctions can have wider spreads."},
            {"source": "analyst", "citation": "round-1 analyst found opening loss clustering."},
        ],
        "source_code_verification": (
            "strategies/ema/signals.py:generate_signals_for_frame builds the EMA entries."
        ),
    }


def _validate(raw: dict, *, attempt_number: int):
    return validate_thesis_dict(
        raw,
        research_round_id="job-1-round-1",
        attempt_number=attempt_number,
        assign_thesis_id=research_thesis_attempt_id,
        tools_called={"list_experiment_results", "web_search"},
    )


def test_validate_thesis_dict_assigns_id_when_llm_omits_thesis_id() -> None:
    thesis = _validate(_valid_thesis_payload(), attempt_number=1)

    assert thesis.thesis_id == "job-1-round-1-attempt-1"


def test_validate_thesis_dict_overrides_llm_emitted_thesis_id() -> None:
    raw = _valid_thesis_payload()
    raw["thesis_id"] = "completely_made_up"

    thesis = _validate(raw, attempt_number=1)

    assert thesis.thesis_id == "job-1-round-1-attempt-1"


def test_validate_thesis_dict_assigns_distinct_attempt_ids_for_same_content() -> None:
    first = _validate(_valid_thesis_payload(), attempt_number=1)
    second = _validate(_valid_thesis_payload(), attempt_number=2)

    assert first.thesis_id == "job-1-round-1-attempt-1"
    assert second.thesis_id == "job-1-round-1-attempt-2"


def test_rejected_attempt_row_derives_thesis_id_from_round_and_attempt(tmp_path) -> None:
    db = BacktestRunDB(tmp_path / "runs.db")
    db.add_research_thesis_attempt(
        {
            "research_round_id": "job-9-round-4",
            "attempt_number": 3,
            "strategy_family": "ema",
            "validator_status": "rejected",
            "validation_failure_reason": "Missing hypothesis",
        }
    )

    rows = db.list_research_thesis_attempts()

    assert rows[0]["thesis_id"] == "job-9-round-4-attempt-3"
