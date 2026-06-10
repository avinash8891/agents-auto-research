"""Research types u2014 structured schema for the research pipeline.

ResearchThesis  u2192  BacktestContract  u2192  RuntimeConfig

The conductor produces a ResearchThesis (why, what should happen, what disproves it).
The compiler converts it to an BacktestContract (executable config + research metadata).
The evaluator checks the result against predictions and produces an BacktestVerdict.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, Field


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class ExpectedEffect(BaseModel):
    """A measurable prediction about what a config change should do."""

    metric: str
    direction: Literal[
        "increase",
        "decrease",
        "increase_or_same",
        "decrease_or_same",
        "not_worse_than",
    ]
    threshold: float | None = None
    unit: str | None = None
    rationale: str | None = None


class Disqualifier(BaseModel):
    """A condition that, if triggered, invalidates the thesis regardless of metric improvement."""

    name: str
    condition: str
    severity: Literal["hard_fail", "soft_fail"] = "hard_fail"
    # `kind` distinguishes pure metric-threshold disqualifiers ("PF must improve
    # by 5%") from mechanism-evidence disqualifiers ("if up-drive mornings are
    # NOT a bad regime in the data"). At least one mechanism_evidence
    # disqualifier may be required by future validator rules (B5).
    kind: Literal["metric_threshold", "mechanism_evidence"] = "metric_threshold"


class Alternative(BaseModel):
    """One mechanism direction the conductor considered but did not choose.

    Required (>=2) per ResearchThesis when alternatives_considered is enforced
    by the validator. Forces multi-candidate consideration before final pick
    (legacy prompt §4 / D1 principle).
    """

    mechanism: str
    why_rejected: str = Field(min_length=40)


class EvidenceCitation(BaseModel):
    """One typed evidence citation used to justify the thesis.

    Replaces the legacy `evidence: list[str]` blob with source-tagged citations
    so the validator can require minimum coverage by source (e.g. >=1 web_search,
    >=1 analyst).
    """

    source: Literal[
        "web_search",
        "analyst",
        "source_code",
        "round_result",
        "memory",
    ]
    citation: str


class PriorLeverOutcome(BaseModel):
    """Citation that the proposed thesis reuses a config-key concept already tested.

    Required when reusing a lever in a different direction or with a similar
    descriptor — forces the agent to confront whipsawing instead of silently
    flipping levers across rounds.
    """

    prior_thesis_id: str
    lever: str  # e.g. "min_stop_distance_pct" or "opening_window_end"
    direction_then: str  # "tightened", "loosened", "filtered_in", etc.
    outcome: str  # one-line summary of what the prior thesis produced
    why_retry: str = Field(
        min_length=40,
        description="Why the lever is worth re-testing despite the prior outcome.",
    )


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


class CausalFactor(BaseModel):
    """A candidate causal rule over entry-time feature-table columns."""

    factor_id: str
    story: str
    rule: str
    direction: Literal["loss", "win"]
    evidence_rounds: list[int] = Field(default_factory=list)
    status: Literal["candidate", "supported", "refuted", "harvested"] = "candidate"
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


class MechanismProposal(BaseModel):
    """Conductor output for the causal-engine research path."""

    hypothesis: str
    mechanism: str
    causal_factors: list[CausalFactor] = Field(default_factory=list)
    reasoning: str = ""
    should_stop: bool = False


EMERGENT_MECHANISM_DIMENSION = "emergent"

# Stable built-in mechanism dimensions for thesis classification.
CORE_MECHANISM_DIMENSIONS = {
    "entry_timing",
    "exit_mechanism",
    "signal_quality",
    "regime_conditioning",
    "portfolio_construction",
    "risk_structure",
    "market_microstructure",
    # Emerged from wiki ingestion of foundational literature:
    # Almgren-Chriss/LOB practice, Lopez de Prado ML fund failures,
    # news sentiment literature, and Grinold-Kahn FLAM.
    "execution_costs",
    "universe_selection",
    "alternative_data",
    "alpha_decay",
}

# Valid mechanism dimensions for thesis classification. The emergent path is
# intentionally explicit so autonomous discovery is gated by validator evidence.
MECHANISM_DIMENSIONS = CORE_MECHANISM_DIMENSIONS | {EMERGENT_MECHANISM_DIMENSION}


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

    # Mechanism discovery fields — forces structural thinking
    mechanism_dimension: str = ""  # core dimension, emergent, or a prior emergent name
    dimension_novelty: str = ""  # why this is not a parameter variation of prior work
    causal_cluster: str = ""  # causal family this thesis belongs to, for diversity audits
    dominant_cluster_overlap: Literal["", "low", "medium", "high"] = ""
    underexplored_dimensions_considered: list[str] = Field(default_factory=list)
    novel_connection: str = ""  # why this connects evidence in a materially new way
    closest_prior_theses_considered: list[str] = Field(default_factory=list)
    orthogonality_defense: str = ""  # why this is orthogonal vs merely adjacent
    evidence_strength: Literal["", "direct", "proxy", "mixed", "speculative"] = ""
    thesis_role: Literal[
        "",
        "orthogonal_discovery",
        "implementation_unlock",
        "cleanup_validation_follow_up",
        "winning_cluster_follow_up",
    ] = ""
    falsification_or_alternative: str = ""  # what would weaken this mechanism
    new_dimension_name: str = ""  # required when mechanism_dimension == emergent
    why_existing_dimensions_do_not_fit: str = ""
    mechanism_family_definition: str = ""
    expected_reuse_across_future_theses: str = ""

    evidence: list[str] = Field(default_factory=list)

    # Research theses are baseline-first. These fields remain for compatibility
    # but must stay empty (or explicitly reference the family baseline path).
    base_contract_id: str = ""
    base_config_path: str = ""

    config_changes: dict[str, Any] = Field(default_factory=dict)

    expected_effects: list[ExpectedEffect] = Field(default_factory=list)
    disqualifiers: list[Disqualifier] = Field(default_factory=list)
    required_diagnostics: list[str] = Field(default_factory=list)
    required_diagnostic_specs: list[DiagnosticRequirementSpec] = Field(default_factory=list)

    requires_code_change: bool = False
    requested_primitives: list[str] = Field(default_factory=list)

    why_not_overfit: str = ""

    # Theme keywords: agent-supplied tokens for cluster-fixation detection (B1).
    # 2-3 short noun phrases that categorize the thesis (e.g. ["opening_session",
    # "stop_distance"]). Set/list overlap on these drives the cluster-fixation rule.
    theme_keywords: list[str] = Field(default_factory=list)

    # Citations of prior theses whose config-key concepts overlap with this one.
    # Required when reusing a lever in a different direction (B2 whipsaw rule).
    prior_lever_outcomes: list[PriorLeverOutcome] = Field(default_factory=list)

    # Alternative mechanism directions considered before the final pick.
    # Validator requires >=2 entries (recovers legacy §4 / D1 principle).
    alternatives_considered: list[Alternative] = Field(default_factory=list)

    # Typed evidence citations. Validator requires at least one with
    # source='web_search' AND one with source='analyst' (when applicable).
    # Replaces the legacy `evidence: list[str]` for enforcement purposes; the
    # legacy field is preserved above for backward compat.
    evidence_citations: list[EvidenceCitation] = Field(default_factory=list)

    # Citation of which strategy source-code file/function corroborates the
    # mechanism. Recovers legacy §13.5 "verify mechanism in code" requirement.
    source_code_verification: str = ""


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

    expected_effects: list[ExpectedEffect] = Field(default_factory=list)
    disqualifiers: list[Disqualifier] = Field(default_factory=list)
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
            expected_effects=sidecar.get("expected_effects") or [],
            disqualifiers=sidecar.get("disqualifiers") or [],
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
    status values: "ok" — thesis ready; "should_stop" — conductor decided to quit;
    "conductor_error" — timeout, parse failure, gate violation, or validation failure.
    """

    status: Literal["ok", "should_stop", "conductor_error"]
    thesis: dict[str, Any] | None = None
    error: str = ""
    validation_reason: str = ""
    reasoning: str = ""
    should_stop: bool = False
    # Tools the conductor invoked during this attempt. Surfaced so the outer
    # validator can enforce process-tier gates (e.g. web_search required) on
    # a retry-eligible basis instead of short-circuiting through conductor_error.
    # None = caller did not observe tools (e.g. tests constructing the result
    # by hand); the outer validator should skip the process gate. An empty
    # frozenset = the conductor ran and called no tools — gate must fire.
    tools_called: frozenset[str] | None = None


class BacktestVerdict(BaseModel):
    """Result of evaluating a backtest against the thesis predictions."""

    contract_id: str
    thesis_id: str

    status: Literal["accepted", "rejected", "inconclusive"]

    passed_effects: list[str] = Field(default_factory=list)
    failed_effects: list[str] = Field(default_factory=list)
    triggered_disqualifiers: list[str] = Field(default_factory=list)
    unparsed_disqualifiers: list[str] = Field(default_factory=list)
    missing_required_diagnostics: list[str] = Field(default_factory=list)

    summary: str = ""
