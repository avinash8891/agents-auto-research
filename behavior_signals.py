"""Behavior signals + policy layer for the thesis validator.

Separates "what the detectors observed" (BehaviorSignal) from "what the
harness does about it" (PolicyDecision). Today there is exactly one
policy — the default `decide` function — which rejects on any signal,
matching the pre-Phase-A validator behavior exactly.

The seam exists so future phases (D: outcome calibration, E: per-strategy
configurable policy) can soften or strengthen rejection thresholds
without touching the detectors or the validator.

Properties preserved across the abstraction:
  * Detectors are pure functions: (thesis, priors) -> Signal | None
  * Policy is a pure function: list[Signal] -> Decision
  * Validator translates the decision back to a raise/no-raise outcome
  * External callers see no API change
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

SignalSeverity = Literal["info", "warn", "block"]
DecisionAction = Literal["accept", "accept_with_warning", "reject"]


@dataclass(frozen=True)
class BehaviorSignal:
    """One detector's observation about a thesis.

    Detectors emit signals describing what they observed. They do not
    decide outcomes; that is the policy layer's job. Signals are
    immutable (frozen=True) so they can be safely passed through the
    policy and persisted without surprise mutation.

    Fields:
      code         rejection_code-compatible identifier (kept stable for
                   backwards compatibility with persisted rejection.json)
      confidence   0.0-1.0 strength of the detector's belief. For binary
                   detectors (e.g. "missing mechanism_evidence"), this is
                   1.0 when fired. For graded detectors (e.g. cluster
                   fixation), this is the proportion of evidence found.
      severity     "info" | "warn" | "block" — detector's recommendation.
                   "block" rejects, "warn" accepts with warnings, and
                   "info" records non-blocking context.
      summary      one-line human description (becomes the exception
                   message when the policy rejects)
      evidence     structured data the conductor can use to fix
      remediation  zero or more concrete fix suggestions
    """

    code: str
    confidence: float
    severity: SignalSeverity
    summary: str
    evidence: dict[str, Any] = field(default_factory=dict)
    remediation: tuple[str, ...] = ()


@dataclass(frozen=True)
class PolicyDecision:
    """The policy layer's verdict given a set of detected signals.

    action          accept | accept_with_warning | reject
    rejection_code  populated when action == "reject"; mirrors the
                    triggering signal's code so persisted rejection.json
                    records and downstream consumers see the same code
                    they did pre-refactor.
    triggering      the single signal that caused a reject decision
                    (None for accept / accept_with_warning). Lifted to
                    its own field so callers do not need to know the
                    policy's "first signal wins" implementation detail.
    signals         every signal considered (preserved for logging /
                    persistence / future calibration)
    warnings        signals that did NOT trigger reject but should be
                    surfaced to the conductor's next round prompt
                    (reserved for future phase; empty in Phase C)
    """

    action: DecisionAction
    rejection_code: str = ""
    triggering: BehaviorSignal | None = None
    signals: tuple[BehaviorSignal, ...] = ()
    warnings: tuple[BehaviorSignal, ...] = ()


def decide(signals: list[BehaviorSignal]) -> PolicyDecision:
    """Apply the default policy to a list of detected signals.

    Block signals reject, with the first block signal becoming the
    rejection_code. Warn signals accept with warnings, and info-only signals
    accept while preserving the observed signals for logs/future calibration.
    """
    signal_tuple = tuple(signals)
    if not signal_tuple:
        return PolicyDecision(action="accept")
    for signal in signal_tuple:
        if signal.severity == "block":
            return PolicyDecision(
                action="reject",
                rejection_code=signal.code,
                triggering=signal,
                signals=signal_tuple,
            )
    warnings = tuple(signal for signal in signal_tuple if signal.severity == "warn")
    if warnings:
        return PolicyDecision(
            action="accept_with_warning",
            signals=signal_tuple,
            warnings=warnings,
        )
    return PolicyDecision(action="accept", signals=signal_tuple)
