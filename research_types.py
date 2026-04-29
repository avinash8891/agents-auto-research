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


# Valid mechanism dimensions for thesis classification
MECHANISM_DIMENSIONS = {
    "entry_timing",
    "exit_mechanism",
    "signal_quality",
    "regime_conditioning",
    "portfolio_construction",
    "risk_structure",
    "market_microstructure",
}


class ResearchThesis(BaseModel):
    """What the conductor produces. Research-grade, not just config changes."""
    thesis_id: str
    strategy_family: str = "ema"

    hypothesis: str
    mechanism: str

    # Mechanism discovery fields — forces structural thinking
    mechanism_dimension: str = ""  # one of MECHANISM_DIMENSIONS
    dimension_novelty: str = ""  # why this is not a parameter variation of prior work

    evidence: list[str] = Field(default_factory=list)

    config_changes: dict[str, Any] = Field(default_factory=dict)

    expected_effects: list[ExpectedEffect] = Field(default_factory=list)
    disqualifiers: list[Disqualifier] = Field(default_factory=list)
    required_diagnostics: list[str] = Field(default_factory=list)

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
    runtime_config: dict[str, Any]

    hypothesis: str
    mechanism: str

    expected_effects: list[ExpectedEffect] = Field(default_factory=list)
    disqualifiers: list[Disqualifier] = Field(default_factory=list)
    required_diagnostics: list[str] = Field(default_factory=list)

    status: Literal["ready_to_run", "needs_code", "rejected_at_compile"] = "ready_to_run"


class ExperimentVerdict(BaseModel):
    """Result of evaluating a backtest against the thesis predictions."""
    experiment_id: str
    thesis_id: str

    status: Literal["accepted", "rejected", "inconclusive"]

    passed_effects: list[str] = Field(default_factory=list)
    failed_effects: list[str] = Field(default_factory=list)
    triggered_disqualifiers: list[str] = Field(default_factory=list)

    summary: str = ""
