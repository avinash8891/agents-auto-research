"""Structured detector observations for the prior-thesis dedupe checks.

Detectors (`_detect_neighboring_threshold`, `_detect_config_key_overlap` in
thesis_validator.py) are pure functions `(thesis, priors) -> Signal | None`.
The consumer (`autoresearch_research._validate_mechanism_dedupe`) raises on
any returned signal — the legacy policy layer that graded severities was
deleted with the v2 redesign.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

SignalSeverity = Literal["info", "warn", "block"]


@dataclass(frozen=True)
class BehaviorSignal:
    """One detector's observation about a thesis.

    Fields:
      code         rejection_code-compatible identifier (kept stable for
                   backwards compatibility with persisted rejection.json)
      confidence   0.0-1.0 strength of the detector's belief; the live
                   detectors are binary and always emit 1.0 when fired.
      severity     "info" | "warn" | "block" — detector's recommendation.
      summary      one-line human description (becomes the exception
                   message when the consumer rejects)
      evidence     structured data the conductor can use to fix
      remediation  zero or more concrete fix suggestions
    """

    code: str
    confidence: float
    severity: SignalSeverity
    summary: str
    evidence: dict[str, Any] = field(default_factory=dict)
    remediation: tuple[str, ...] = ()
