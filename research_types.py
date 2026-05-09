"""Research types u2014 structured schema for the research pipeline.

ResearchThesis  u2192  ExperimentContract  u2192  RuntimeConfig

The conductor produces a ResearchThesis (why, what should happen, what disproves it).
The compiler converts it to an ExperimentContract (executable config + research metadata).
The evaluator checks the result against predictions and produces an ExperimentVerdict.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


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
}

# Valid mechanism dimensions for thesis classification. The emergent path is
# intentionally explicit so autonomous discovery is gated by validator evidence.
MECHANISM_DIMENSIONS = CORE_MECHANISM_DIMENSIONS | {EMERGENT_MECHANISM_DIMENSION}


class ResearchThesis(BaseModel):
    """What the conductor produces. Research-grade, not just config changes."""

    thesis_id: str
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
        "winning_cluster_follow_up",
        "implementation_unlock",
        "cleanup_validation_follow_up",
    ] = ""
    falsification_or_alternative: str = ""  # what would weaken this mechanism
    new_dimension_name: str = ""  # required when mechanism_dimension == emergent
    why_existing_dimensions_do_not_fit: str = ""
    mechanism_family_definition: str = ""
    expected_reuse_across_future_theses: str = ""

    evidence: list[str] = Field(default_factory=list)

    # Optional explicit starting point for thesis deltas. When omitted, the
    # family baseline config is the base.
    base_experiment_id: str = ""
    base_config_path: str = ""

    config_changes: dict[str, Any] = Field(default_factory=dict)

    expected_effects: list[ExpectedEffect] = Field(default_factory=list)
    disqualifiers: list[Disqualifier] = Field(default_factory=list)
    required_diagnostics: list[str] = Field(default_factory=list)
    required_diagnostic_specs: list[DiagnosticRequirementSpec] = Field(default_factory=list)

    requires_code_change: bool = False
    requested_primitives: list[str] = Field(default_factory=list)

    why_not_overfit: str = ""


class ExperimentContract(BaseModel):
    """Connects a research thesis to an executable backtest.

    The runtime_config is for the backtester.
    Everything else is for the evaluator.
    """

    experiment_id: str  # config content hash
    thesis_id: str
    strategy_family: str

    baseline_config_path: str
    base_experiment_id: str = ""
    base_config_hash: str = ""
    runtime_config: dict[str, Any]

    hypothesis: str
    mechanism: str

    expected_effects: list[ExpectedEffect] = Field(default_factory=list)
    disqualifiers: list[Disqualifier] = Field(default_factory=list)
    required_diagnostics: list[str] = Field(default_factory=list)
    required_diagnostic_specs: list[DiagnosticRequirementSpec] = Field(default_factory=list)
    missing_primitives: list[str] = Field(default_factory=list)

    status: Literal["ready_to_run", "needs_code", "rejected_at_compile"] = "ready_to_run"


class ExperimentVerdict(BaseModel):
    """Result of evaluating a backtest against the thesis predictions."""

    experiment_id: str
    thesis_id: str

    status: Literal["accepted", "rejected", "inconclusive"]

    passed_effects: list[str] = Field(default_factory=list)
    failed_effects: list[str] = Field(default_factory=list)
    triggered_disqualifiers: list[str] = Field(default_factory=list)
    missing_required_diagnostics: list[str] = Field(default_factory=list)

    summary: str = ""
