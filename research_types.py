"""Research types u2014 structured schema for the research pipeline.

ResearchThesis  u2192  BacktestContract  u2192  RuntimeConfig

The conductor produces a ResearchThesis (why, what should happen, what disproves it).
The compiler converts it to an BacktestContract (executable config + research metadata).
The evaluator checks registered predictions and produces a HarvestVerdict.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class DiagnosticRequirementSpec(BaseModel):
    """Executable diagnostic contract for builder + verifier.

    `required_diagnostics` remains as human-facing prose/rationale.
    This structured form is the canonical machine contract.
    """

    key: str
    surface: Literal[
        "any",
        "metrics",
        "strategy_diagnostics",
        "experiment_evaluation",
    ] = "any"
    payload_fields: list[str] = Field(default_factory=list)
    aliases: list[str] = Field(default_factory=list)
    description: str = ""


FACTOR_STATUS_DESCRIPTIONS = {
    "candidate": "screened rule awaiting registered prediction harvest",
    "supported": "validated by registered predictions but not yet harvested into the model",
    "refuted": "failed registered prediction harvest; excluded from prediction and duplicate screening",
    "harvested": "validated factor retained for audit history after harvest",
    "demoted": (
        "failed walk-forward survival after being harvested; excluded from prediction and "
        "duplicate screening"
    ),
}


class CausalFactor(BaseModel):
    """A candidate causal rule over entry-time feature-table columns."""

    factor_id: str
    story: str
    rule: str
    direction: Literal["loss", "win"]
    evidence_rounds: list[int] = Field(default_factory=list)
    status: Literal["candidate", "supported", "refuted", "harvested", "demoted"] = "candidate"
    created_at: str = Field(default_factory=_utc_now_iso)
    lesson: str = ""


class AccuracyPoint(BaseModel):
    """One holdout accuracy observation for a causal model version."""

    round_number: int
    model_version: int
    pnl_weighted_accuracy: float
    naive_accuracy: float
    skill: float
    holdout_trade_count: int


class CausalModel(BaseModel):
    """Persisted causal model state for one strategy family."""

    family: str
    version: int
    factors: list[CausalFactor] = Field(default_factory=list)
    accuracy_history: list[AccuracyPoint] = Field(default_factory=list)
    holdout_start: str = ""


class MetricName(str, Enum):
    profit_factor = "profit_factor"
    trade_count = "trade_count"
    max_drawdown = "max_drawdown"
    win_rate = "win_rate"
    median_expectancy = "median_expectancy"
    pnl_weighted_accuracy = "pnl_weighted_accuracy"


HARVEST_OBSERVABLE_METRIC_NAMES = (
    "profit_factor",
    "trade_count",
    "max_drawdown",
    "median_expectancy",
)
HARVEST_OBSERVABLE_METRICS = frozenset(MetricName(name) for name in HARVEST_OBSERVABLE_METRIC_NAMES)


class Prediction(BaseModel):
    metric: MetricName = Field(
        description=(
            "Registered-prediction metric. Harvest evaluation can observe only "
            f"{', '.join(HARVEST_OBSERVABLE_METRIC_NAMES)} from backtest outputs; "
            "win_rate and pnl_weighted_accuracy are MetricName values but unavailable "
            "to the harvest evaluator."
        )
    )
    direction: Literal[
        "increase",
        "decrease",
        "increase_or_same",
        "decrease_or_same",
        "not_worse_than",
    ]
    predicted: float | None = None
    rationale: str = ""


class MechanismProposal(BaseModel):
    """Conductor output for the causal-engine research path."""

    story: str
    # rule/competitor_rule are optional so the conductor can DECLINE a round
    # (actionable=false with no new testable rule). They are required when
    # actionable=true; the validator below enforces that.
    rule: str | None = None
    competitor_rule: str | None = None
    competitor_story: str | None = None
    actionable: bool
    proposed_change: dict[str, Any] | None = None
    # Name of a NEW strategy capability to build when the rule cannot be expressed
    # by existing config levers; routes the thesis to the primitive builder, which
    # implements `rule` as that primitive. Mutually sufficient with proposed_change.
    requested_primitive: str | None = None
    predictions: list[Prediction] | None = None

    @model_validator(mode="after")
    def _validate_actionable_contract(self) -> "MechanismProposal":
        if not self.actionable:
            return self
        if not (self.rule or "").strip():
            raise ValueError("rule is required when actionable is true")
        if not (self.competitor_rule or "").strip():
            raise ValueError("competitor_rule is required when actionable is true")
        if not self.proposed_change and not (self.requested_primitive or "").strip():
            raise ValueError("an actionable proposal needs proposed_change or requested_primitive")
        if self.predictions is None or len(self.predictions) < 2:
            raise ValueError(
                "predictions must contain at least two entries when actionable is true"
            )
        metrics = [prediction.metric for prediction in self.predictions]
        if len(set(metrics)) != len(metrics):
            raise ValueError("predictions must use distinct MetricName values")
        missing_predicted = [
            prediction.metric.value
            for prediction in self.predictions
            if prediction.predicted is None
        ]
        if missing_predicted:
            raise ValueError(
                "predictions must include predicted values for: "
                + ", ".join(sorted(missing_predicted))
            )
        unsupported_metrics = sorted(
            {
                prediction.metric.value
                for prediction in self.predictions
                if prediction.metric not in HARVEST_OBSERVABLE_METRICS
            }
        )
        if unsupported_metrics:
            raise ValueError(
                "predictions use metrics unavailable to harvest evaluator: "
                + ", ".join(unsupported_metrics)
            )
        return self


class ResearchThesis(BaseModel):
    """What the conductor produces. Research-grade, not just config changes."""

    # assigned post-validation; LLM output must not pre-populate this
    thesis_id: str
    proposal_label: str = Field(
        default="",
        max_length=40,
        description="Optional LLM-supplied human label; never used as an identifier.",
    )
    strategy_family: str

    hypothesis: str
    mechanism: str

    # Research theses are baseline-first. These fields remain for compatibility
    # but must stay empty (or explicitly reference the family baseline path).
    base_contract_id: str = ""
    base_config_path: str = ""

    config_changes: dict[str, Any] = Field(default_factory=dict)

    required_diagnostics: list[str] = Field(default_factory=list)
    required_diagnostic_specs: list[DiagnosticRequirementSpec] = Field(default_factory=list)

    requires_code_change: bool = False
    requested_primitives: list[str] = Field(default_factory=list)

    # The conductor's df.query rule (and its competitor), carried so the builder
    # can implement the EXACT entry condition as the requested primitive. Without
    # a dedicated field the rule is dropped at the model boundary (extras ignored)
    # and the builder only sees the prose mechanism.
    mechanism_rule: str = ""
    mechanism_competitor_rule: str = ""


class BacktestContract(BaseModel):
    """Connects a research thesis to an executable backtest.

    The runtime_config is for the backtester.
    Everything else is for the evaluator.
    """

    contract_id: str  # config content hash
    thesis_id: str
    strategy_family: str

    baseline_config_path: str
    base_contract_id: str = ""
    base_config_hash: str = ""
    runtime_config: dict[str, Any]
    config_changes: dict[str, Any] = Field(default_factory=dict)

    hypothesis: str
    mechanism: str

    required_diagnostics: list[str] = Field(default_factory=list)
    required_diagnostic_specs: list[DiagnosticRequirementSpec] = Field(default_factory=list)
    missing_primitives: list[str] = Field(default_factory=list)

    status: Literal["ready_to_run", "needs_code", "rejected_at_compile"] = "ready_to_run"

    @classmethod
    def from_sidecar(
        cls,
        *,
        contract_id: str,
        strategy_family: str,
        baseline_config_path: str,
        runtime_config: dict[str, Any],
        sidecar: dict[str, Any],
        status: Literal["ready_to_run", "needs_code", "rejected_at_compile"] = "ready_to_run",
        missing_primitives: list[str] | None = None,
    ) -> "BacktestContract":
        from diagnostic_contracts import build_required_diagnostic_specs

        return cls(
            contract_id=contract_id,
            thesis_id=str(sidecar.get("thesis_id") or contract_id),
            strategy_family=str(sidecar.get("strategy_family") or strategy_family),
            baseline_config_path=baseline_config_path,
            runtime_config=runtime_config,
            config_changes=sidecar.get("config_changes") or {},
            hypothesis=str(sidecar.get("hypothesis") or ""),
            mechanism=str(sidecar.get("mechanism") or ""),
            required_diagnostics=sidecar.get("required_diagnostics") or [],
            required_diagnostic_specs=build_required_diagnostic_specs(
                sidecar.get("required_diagnostics") or [],
                sidecar.get("required_diagnostic_specs") or [],
            ),
            missing_primitives=list(missing_primitives or []),
            status=status,
        )


class StructuredRejection(BaseModel):
    """Machine-readable record of a validator/compile rejection for one thesis.

    Persisted to `runtime/jobs/job-N/research/round-M/theses/<thesis_id>/rejection.json`.
    Read back by the conductor's per-round prompt and by the rejection-pattern tools.
    """

    rejected_at: str  # ISO-8601 UTC, set by writer
    round: int
    thesis_id: str
    stage: Literal["stage_1", "stage_2", "compile"]
    rejection_code: (
        str  # short machine-readable category, e.g. "thesis_quality_theme_cluster_fixation"
    )
    rule_violated: str = ""  # one-line summary of the rule
    evidence: dict[str, Any] = Field(default_factory=dict)
    remediation_hint: str = ""
    validator_version: str = ""


@dataclass
class ConductorResult:
    """Typed return from run_research_conductor.

    The conductor returns one proposed thesis envelope; the system assigns thesis_id.
    status values: "ok" — thesis ready; "conductor_error" — timeout, parse failure,
    gate violation, or validation failure.
    """

    status: Literal["ok", "conductor_error"]
    thesis: dict[str, Any] | None = None
    error: str = ""
    validation_reason: str = ""
    reasoning: str = ""
    # Tools the conductor invoked during this attempt. Surfaced so the outer
    # validator can enforce process-tier gates (e.g. web_search required) on
    # a retry-eligible basis instead of short-circuiting through conductor_error.
    # None = caller did not observe tools (e.g. tests constructing the result
    # by hand); the outer validator should skip the process gate. An empty
    # frozenset = the conductor ran and called no tools — gate must fire.
    tools_called: frozenset[str] | None = None


class HarvestVerdict(BaseModel):
    """Result of evaluating registered v2 harvest predictions."""

    thesis_id: str
    status: Literal["supported", "refuted", "inconclusive", "degenerate"]
    prediction_results: list[dict[str, Any]] = Field(default_factory=list)
    summary: str = ""
    lesson: str = ""
