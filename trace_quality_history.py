from __future__ import annotations

from typing import Any

from trace_sdk import record_event


class QualityHistory:
    def __init__(self) -> None:
        self._last_scores: dict[str, float] | None = None

    def append_run(
        self,
        *,
        summary: str,
        run_label: str,
        dimension_scores: dict[str, float],
        overall_score: float | None = None,
        artifact_paths: list[str] | None = None,
    ) -> dict[str, Any]:
        current_scores = dict(dimension_scores)
        if overall_score is not None:
            current_scores["overall"] = overall_score

        delta: dict[str, float] = {}
        regressions: list[str] = []
        trend = "initial"
        if self._last_scores is not None:
            for key, value in current_scores.items():
                if key in self._last_scores:
                    change = round(value - self._last_scores[key], 6)
                    delta[key] = change
                    if change < 0:
                        regressions.append(key)
            overall_delta = delta.get("overall", 0.0)
            if overall_delta < 0:
                trend = "down"
            elif overall_delta > 0:
                trend = "up"
            else:
                trend = "flat"

        payload = {
            "run_label": run_label,
            "dimension_scores": dict(dimension_scores),
            "overall_score": overall_score,
            "delta": delta,
            "regressions": regressions,
            "trend": trend,
        }
        record_event(
            source_module="trace_quality_history",
            category="quality",
            action="append_run",
            summary=summary,
            payload=payload,
            artifact_paths=artifact_paths,
        )
        self._last_scores = current_scores
        return payload
