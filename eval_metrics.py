"""Primary metric + variance summary for the held-out eval harness.

Two metrics, both computed over a list of per-task outcomes:

  - ``compiled_rate``: fraction of tasks classified as ``compiled``
    (the proxy for "the loop produced a runnable thesis"). Default
    primary metric.
  - ``quality_score_p50``: median ``overall_score`` from the
    QualityHistory events fired during the eval. Secondary
    tie-breaker.

Variance is stdev/min/max over `repeat` independent suite runs. We use
stdlib ``statistics`` to avoid pulling numpy in for simple stats.
"""

from __future__ import annotations

import math
import statistics
from dataclasses import dataclass, field
from typing import Iterable

# Round-outcome vocabulary. Producer is autoresearch_research._classify_round_outcome
# (kept stringly-typed at the producer for now; pre-existing code outside this
# loop's scope). These constants are the canonical home for downstream consumers
# in the improvement loop — same divergence-class as DECISION_KEEP vs "kept".
OUTCOME_COMPILED = "compiled"
OUTCOME_STOPPED = "stopped"
OUTCOME_REJECTED = "rejected"
OUTCOME_CONDUCTOR_ERROR = "conductor_error"
OUTCOME_NEEDS_CODE = "needs_code"
OUTCOME_COMPLETED = "completed"

# Outcomes that count as a successful round for compiled_rate.
COMPILED_OUTCOMES = frozenset({OUTCOME_COMPILED})

# Outcomes that score 1.0 in the eval harness — the round produced a
# usable artifact even if it wasn't a compiled config (stopped = the
# conductor explicitly halted with a clean state).
KEEP_OUTCOMES = frozenset({OUTCOME_COMPILED, OUTCOME_STOPPED})

# Ratchet/HALO-apply decision strings. Single source of truth so
# downstream consumers can switch on them without tense ambiguity
# ("kept" vs "keep" was a real divergence prior to consolidation).
DECISION_KEEP = "keep"
DECISION_REVERT = "revert_recommended"
DECISION_INCONCLUSIVE = "inconclusive_keep"
DECISION_SKIP = "skip"
DECISION_ABORTED = "aborted"


@dataclass
class TaskOutcome:
    """One held-out task's result for a single suite repetition."""

    family: str
    dataset_window: str
    outcome: str
    overall_score: float | None = None


@dataclass
class SuiteSummary:
    """Aggregate metrics across all tasks in one suite repetition."""

    compiled_rate: float
    quality_score_p50: float | None
    n_tasks: int
    n_compiled: int

    def __post_init__(self) -> None:
        if self.n_tasks < 0 or self.n_compiled < 0:
            raise ValueError(f"counts must be non-negative: {self}")
        if self.n_compiled > self.n_tasks:
            raise ValueError(f"n_compiled > n_tasks: {self}")
        if not 0.0 <= self.compiled_rate <= 1.0:
            raise ValueError(f"compiled_rate must be in [0, 1]: {self}")
        if self.n_tasks > 0:
            expected = self.n_compiled / self.n_tasks
            if not math.isclose(self.compiled_rate, expected, rel_tol=1e-9, abs_tol=1e-9):
                raise ValueError(
                    f"compiled_rate {self.compiled_rate} inconsistent with "
                    f"n_compiled/n_tasks={expected}: {self}"
                )


@dataclass
class EvalResult:
    """Multi-repetition eval summary."""

    label: str
    timestamp: str
    repeat: int
    primary_metric_name: str
    primary_metric_mean: float
    primary_metric_stdev: float
    primary_metric_min: float
    primary_metric_max: float
    suites: list[SuiteSummary] = field(default_factory=list)
    secondary_quality_p50_mean: float | None = None

    def __post_init__(self) -> None:
        if self.suites and self.repeat != len(self.suites):
            raise ValueError(f"repeat {self.repeat} != len(suites) {len(self.suites)}")
        # 1e-9 absolute tolerance to forgive float roundoff from statistics.fmean
        # (e.g. fmean([0.7, 0.7, 0.7]) returns 0.6999999999999998).
        eps = 1e-9
        if (
            self.primary_metric_min - eps > self.primary_metric_mean
            or self.primary_metric_mean > self.primary_metric_max + eps
        ):
            raise ValueError(
                f"primary_metric ordering broken: "
                f"min={self.primary_metric_min} mean={self.primary_metric_mean} "
                f"max={self.primary_metric_max}"
            )
        if self.primary_metric_stdev < 0:
            raise ValueError(f"stdev negative: {self.primary_metric_stdev}")

    def to_dict(self) -> dict:
        return {
            "label": self.label,
            "timestamp": self.timestamp,
            "repeat": self.repeat,
            "primary_metric_name": self.primary_metric_name,
            "primary_metric": {
                "mean": self.primary_metric_mean,
                "stdev": self.primary_metric_stdev,
                "min": self.primary_metric_min,
                "max": self.primary_metric_max,
            },
            "secondary_quality_p50_mean": self.secondary_quality_p50_mean,
            "suites": [
                {
                    "compiled_rate": s.compiled_rate,
                    "quality_score_p50": s.quality_score_p50,
                    "n_tasks": s.n_tasks,
                    "n_compiled": s.n_compiled,
                }
                for s in self.suites
            ],
        }

    @classmethod
    def from_dict(cls, payload: dict) -> "EvalResult":
        primary = payload.get("primary_metric") or {}
        suites_payload = payload.get("suites") or []
        suites = [
            SuiteSummary(
                compiled_rate=s.get("compiled_rate", 0.0),
                quality_score_p50=s.get("quality_score_p50"),
                n_tasks=s.get("n_tasks", 0),
                n_compiled=s.get("n_compiled", 0),
            )
            for s in suites_payload
            if isinstance(s, dict)
        ]
        return cls(
            label=payload.get("label", "?"),
            timestamp=payload.get("timestamp", ""),
            repeat=payload.get("repeat", len(suites)),
            primary_metric_name=payload.get("primary_metric_name", "compiled_rate"),
            primary_metric_mean=primary.get("mean", 0.0),
            primary_metric_stdev=primary.get("stdev", 0.0),
            primary_metric_min=primary.get("min", 0.0),
            primary_metric_max=primary.get("max", 0.0),
            suites=suites,
            secondary_quality_p50_mean=payload.get("secondary_quality_p50_mean"),
        )


def summarize_suite(outcomes: Iterable[TaskOutcome]) -> SuiteSummary:
    """Roll up one suite repetition's task list into a SuiteSummary."""
    items = list(outcomes)
    n = len(items)
    if n == 0:
        return SuiteSummary(compiled_rate=0.0, quality_score_p50=None, n_tasks=0, n_compiled=0)
    n_compiled = sum(1 for o in items if o.outcome in COMPILED_OUTCOMES)
    rate = n_compiled / n
    scores = [o.overall_score for o in items if o.overall_score is not None]
    p50 = statistics.median(scores) if scores else None
    return SuiteSummary(compiled_rate=rate, quality_score_p50=p50, n_tasks=n, n_compiled=n_compiled)


def summarize_eval(
    *,
    label: str,
    timestamp: str,
    suites: list[SuiteSummary],
    primary_metric_name: str = "compiled_rate",
) -> EvalResult:
    """Combine per-repetition SuiteSummary objects into an EvalResult."""
    if not suites:
        raise ValueError("summarize_eval requires at least one suite summary")
    if primary_metric_name == "compiled_rate":
        primary_values = [s.compiled_rate for s in suites]
    elif primary_metric_name == "quality_score_p50":
        primary_values = [s.quality_score_p50 for s in suites if s.quality_score_p50 is not None]
        if not primary_values:
            raise ValueError("quality_score_p50 has no defined samples across suites")
    else:
        raise ValueError(f"unknown primary_metric_name: {primary_metric_name!r}")
    finite_primary = [v for v in primary_values if math.isfinite(v)]
    if not finite_primary:
        raise ValueError(
            f"all {len(primary_values)} sample(s) for {primary_metric_name!r} are non-finite"
        )
    primary_values = finite_primary
    mean = statistics.fmean(primary_values)
    stdev = statistics.stdev(primary_values) if len(primary_values) > 1 else 0.0
    p50_values = [s.quality_score_p50 for s in suites if s.quality_score_p50 is not None]
    secondary_mean = statistics.fmean(p50_values) if p50_values else None
    return EvalResult(
        label=label,
        timestamp=timestamp,
        repeat=len(suites),
        primary_metric_name=primary_metric_name,
        primary_metric_mean=mean,
        primary_metric_stdev=stdev,
        primary_metric_min=min(primary_values),
        primary_metric_max=max(primary_values),
        suites=suites,
        secondary_quality_p50_mean=secondary_mean,
    )


def compare_eval_results(current: EvalResult, prior: EvalResult) -> dict:
    """Produce a delta summary between two eval results.

    Used by Ratchet to decide keep/revert. The delta is in
    units of the primary metric; ``delta_in_stdevs`` is normalized
    by the prior result's stdev (or the current result's, if prior
    had no variance).
    """
    if current.primary_metric_name != prior.primary_metric_name:
        raise ValueError(
            f"primary metric mismatch: current={current.primary_metric_name!r} "
            f"prior={prior.primary_metric_name!r}"
        )
    delta = current.primary_metric_mean - prior.primary_metric_mean
    ref_stdev = prior.primary_metric_stdev or current.primary_metric_stdev or 0.0
    if ref_stdev > 0.0:
        delta_in_stdevs: float | None = delta / ref_stdev
    else:
        delta_in_stdevs = None
    if delta > 0:
        sign = "+"
    elif delta < 0:
        sign = "-"
    else:
        sign = "0"
    return {
        "primary_metric_name": current.primary_metric_name,
        "current": current.primary_metric_mean,
        "prior": prior.primary_metric_mean,
        "delta": delta,
        "delta_sign": sign,
        "delta_in_stdevs": delta_in_stdevs,
        "ref_stdev": ref_stdev,
    }


def classify_delta_in_stdevs(
    delta_in_stdevs: float | None, *, delta: float | None = None
) -> tuple[str, str]:
    """Map a stdev-normalized delta to a Ratchet decision + rationale.

    Centralizes the threshold so HALO-apply and Ratchet stay in lockstep.
    """
    if delta_in_stdevs is None:
        return DECISION_INCONCLUSIVE, "no variance in baseline"
    if delta_in_stdevs >= 1.0:
        return DECISION_KEEP, (
            f"delta={delta:.4f} >= 1 stdev" if delta is not None else "delta >= 1 stdev"
        )
    if delta_in_stdevs <= -1.0:
        return DECISION_REVERT, (
            f"delta={delta:.4f} <= -1 stdev" if delta is not None else "delta <= -1 stdev"
        )
    return DECISION_INCONCLUSIVE, (
        f"delta={delta:.4f} within 1 stdev" if delta is not None else "delta within 1 stdev"
    )
