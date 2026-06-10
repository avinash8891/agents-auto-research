from __future__ import annotations

import json
from pathlib import Path

from scripts.token_audit import _iter_usage_records
from trace_improvement_report import analyze_trace_events


def test_token_audit_excludes_budget_warning_events(tmp_path: Path) -> None:
    path = tmp_path / "trace-events.jsonl"
    path.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "timestamp": "2026-06-10T00:00:00Z",
                        "category": "usage",
                        "action": "accumulate",
                        "payload": {"agent": "builder", "total_tokens": 100},
                    }
                ),
                json.dumps(
                    {
                        "timestamp": "2026-06-10T00:00:01Z",
                        "category": "telemetry",
                        "action": "token_budget_warning",
                        "payload": {"agent_type": "builder", "total_tokens": 100},
                    }
                ),
            ]
        )
        + "\n"
    )

    records = list(_iter_usage_records([path], since=None))

    assert len(records) == 1
    assert records[0]["action"] == "accumulate"


def test_trace_improvement_report_does_not_count_budget_warnings_as_usage() -> None:
    analysis = analyze_trace_events(
        [
            {
                "category": "usage",
                "action": "accumulate",
                "payload": {"agent": "builder", "total_tokens": 100},
            },
            {
                "category": "telemetry",
                "action": "token_budget_warning",
                "payload": {"agent_type": "builder", "total_tokens": 100},
            },
        ]
    )

    assert analysis["roles"]["builder"].usage["total_tokens"] == 100
