from __future__ import annotations

import json

from reflexio_agent_reflections import build_agent_reflections


def test_agent_reflections_use_structured_error_fields_not_substrings() -> None:
    reflections = build_agent_reflections(
        [
            {
                "agent": "analyst",
                "action": "tool_result",
                "summary": "FileNotFoundError appears in a fixture name only",
            },
            {
                "agent": "builder",
                "action": "tool_result",
                "summary": "The word failed is quoted from source data, not status",
            },
        ]
    )

    assert reflections["analyst"]["error_evidence"] == []
    assert reflections["builder"]["error_evidence"] == []


def test_agent_reflections_include_computed_screening_and_prediction_facts() -> None:
    reflections = build_agent_reflections(
        [
            {
                "agent": "conductor",
                "action": "response",
                "summary": "proposed the next mechanism",
            }
        ],
        round_facts={
            "screening_verdict_counts": {"pass": 2, "kill_duplicate": 1},
            "prediction_gaps": [
                {
                    "metric": "profit_factor",
                    "magnitude_gap": -0.23,
                    "direction_passed": False,
                }
            ],
        },
    )

    serialized = json.dumps(reflections["conductor"], sort_keys=True)
    assert "screening verdicts: kill_duplicate=1, pass=2" in serialized
    assert "prediction gaps: profit_factor gap=-0.23 direction=fail" in serialized


def test_agent_reflections_create_conductor_entry_for_facts_without_trajectory() -> None:
    reflections = build_agent_reflections(
        [],
        round_facts={"screening_verdict_counts": {"pass": 1}},
    )

    assert "conductor" in reflections
    assert "screening verdicts: pass=1" in json.dumps(reflections["conductor"])
