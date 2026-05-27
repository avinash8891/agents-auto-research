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
from typing import Any, Final, Literal

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
                   Phase C: all behavior detectors emit "block" to match
                   pre-refactor behavior. Future phases will lower some
                   to "warn" once outcome data shows they over-fire.
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
    signals         every signal considered (preserved for logging /
                    persistence / future calibration)
    warnings        signals that did NOT trigger reject but should be
                    surfaced to the conductor's next round prompt
                    (reserved for future phase; empty in Phase C)
    """

    action: DecisionAction
    rejection_code: str = ""
    signals: tuple[BehaviorSignal, ...] = ()
    warnings: tuple[BehaviorSignal, ...] = ()


# Phase C policy: block on any signal. Matches the pre-refactor behavior
# where each detector raised directly. The constant exists so future
# phases can toggle the default without searching for magic numbers.
_BLOCK_ON_ANY_SIGNAL: Final[bool] = True


def decide(signals: list[BehaviorSignal]) -> PolicyDecision:
    """Apply the default policy to a list of detected signals.

    Today: any signal → reject (mirrors pre-refactor validator behavior
    where each detector raised on the first match). The first signal's
    code becomes the rejection_code, matching the pre-refactor "first
    check that fires wins" semantics.

    Future phases will introduce confidence/severity-driven decisions
    and per-strategy configuration. For now the function is intentionally
    a one-liner so the seam is obvious.
    """
    signal_tuple = tuple(signals)
    if not signal_tuple:
        return PolicyDecision(action="accept")
    if _BLOCK_ON_ANY_SIGNAL:
        first = signal_tuple[0]
        return PolicyDecision(
            action="reject",
            rejection_code=first.code,
            signals=signal_tuple,
        )
    # Reserved for future phases. Today this branch is unreachable
    # because _BLOCK_ON_ANY_SIGNAL is True.
    return PolicyDecision(
        action="accept_with_warning",
        signals=signal_tuple,
        warnings=signal_tuple,
    )
