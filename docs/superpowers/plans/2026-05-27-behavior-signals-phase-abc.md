# Behavior Signals — Phases A, B, C Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Introduce a 2-layer abstraction (signals + policy) underneath the validator's 4 behavioral checks WITHOUT changing any external behavior. The validator continues to raise `ThesisValidationError` with the same `rejection_code` values; the new abstraction makes future calibration possible.

**Architecture:** The 4 behavioral checks in `_run_behavioral_pass` (theme_cluster_fixation, needs_code_starvation, direction_whipsaw, missing_mechanism_evidence_disqualifier) are converted from `raise` to `return BehaviorSignal | None`. A new policy layer maps lists of signals to decisions. The default policy is "block on any signal" — which exactly matches today's behavior. The validator then translates the policy decision back to a raise/no-raise outcome so callers see no API change.

**Tech Stack:** Python 3.13, dataclasses, no new dependencies.

**Why now (Phase A+B+C):** Establishes the seam without changing behavior. Phases D (outcome calibration) and E (auto-tuning) can come later when there's data and need.

---

## Background — what we're introducing

Today the 4 behavioral checks are pure-function rules embedded in `_run_behavioral_pass`. Each one raises directly when its pattern fires. Phase A+B+C inverts this:

1. **Phase A** — Detectors emit `BehaviorSignal` objects instead of raising. Each signal carries `code`, `confidence` (0–1), `severity` (`info|warn|block`), `summary`, `evidence`, `remediation`. The signal IS the data the validator would have put in the rejection.

2. **Phase B** — A Policy layer with one function `decide(signals) -> PolicyDecision` interprets the signal list. The decision has `action` (`accept|accept_with_warning|reject`), `rejection_code` (if reject), and the signals themselves.

3. **Phase C** — The default policy is hard-coded to "any signal → reject" (matching today). The first signal's `code` becomes the rejection_code so external callers receive identical errors.

The external contract is unchanged: `_run_behavioral_pass(thesis, priors)` raises `ThesisValidationError(rejection_code="thesis_quality_*", ...)` for the same inputs that raised today.

## File Structure

**Files created:**
- `behavior_signals.py` — `BehaviorSignal`, `PolicyDecision`, `decide()`. Small module (~80 lines).

**Files modified:**
- `thesis_validator.py` — refactor 4 `_check_*` functions to `_detect_*` returning `BehaviorSignal | None`; rewrite `_run_behavioral_pass` to call detectors → policy → translate.

**Files NOT touched:**
- `research_types.py` — schema unchanged
- `research_prompts.py` — no prompt change
- Any test file other than the ones for new abstraction — existing tests must still pass unchanged
- `rejection_artifact.py` — unchanged
- All existing test files — they assert on rejection_code values, which are preserved

**New test file:**
- `tests/test_behavior_signals.py` — exercises the new module independently

---

## Task 1: Create the behavior_signals module

**Files:**
- Create: `behavior_signals.py`
- Test: `tests/test_behavior_signals.py`

- [ ] **Step 1: Write the failing test for BehaviorSignal**

Create `tests/test_behavior_signals.py`:

```python
"""Tests for the behavior signal + policy abstraction.

The module is a pure data + decision layer with no dependencies on
the validator or any external state. Tests cover signal construction
and policy decision-making in isolation.
"""

from __future__ import annotations

from behavior_signals import BehaviorSignal, PolicyDecision, decide


def test_behavior_signal_is_frozen_and_carries_all_required_fields() -> None:
    """BehaviorSignal must be immutable so it can flow safely through the
    policy layer without surprise mutation."""
    sig = BehaviorSignal(
        code="thesis_quality_theme_cluster_fixation",
        confidence=0.83,
        severity="block",
        summary="4 of last 7 share keywords",
        evidence={"overlap_count": 4},
        remediation=("Propose from a different mechanism dimension",),
    )
    assert sig.code == "thesis_quality_theme_cluster_fixation"
    assert sig.confidence == 0.83
    assert sig.severity == "block"
    import dataclasses
    # frozen=True: attempting to set must raise.
    try:
        sig.code = "different"
    except dataclasses.FrozenInstanceError:
        pass
    else:
        raise AssertionError("BehaviorSignal must be frozen")


def test_decide_accepts_when_no_signals_present() -> None:
    decision = decide([])
    assert decision.action == "accept"
    assert decision.rejection_code == ""
    assert decision.signals == ()


def test_decide_rejects_when_one_signal_present() -> None:
    sig = BehaviorSignal(
        code="thesis_quality_theme_cluster_fixation",
        confidence=0.71,
        severity="block",
        summary="x",
    )
    decision = decide([sig])
    assert decision.action == "reject"
    assert decision.rejection_code == "thesis_quality_theme_cluster_fixation"
    assert decision.signals == (sig,)


def test_decide_rejects_with_first_signal_code_when_multiple_present() -> None:
    """Default policy: first signal wins for the rejection_code so the
    behavior matches the pre-refactor validator (which raised on the first
    check that fired)."""
    first = BehaviorSignal(code="thesis_quality_theme_cluster_fixation", confidence=1.0, severity="block", summary="")
    second = BehaviorSignal(code="thesis_quality_direction_whipsaw", confidence=1.0, severity="block", summary="")
    decision = decide([first, second])
    assert decision.action == "reject"
    assert decision.rejection_code == "thesis_quality_theme_cluster_fixation"
    assert decision.signals == (first, second)


def test_policy_decision_action_is_typed_literal() -> None:
    """A decision's action must be one of the three documented values."""
    decision = PolicyDecision(action="accept")
    assert decision.action in ("accept", "accept_with_warning", "reject")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_behavior_signals.py -v`
Expected: ImportError or ModuleNotFoundError — the module doesn't exist yet.

- [ ] **Step 3: Create the module**

Create `behavior_signals.py`:

```python
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_behavior_signals.py -v`
Expected: 5 tests pass.

- [ ] **Step 5: Commit**

```bash
git add behavior_signals.py tests/test_behavior_signals.py
git commit -m "$(cat <<'EOF'
feat(validator): add behavior_signals module — Phase A+B seams

Introduces BehaviorSignal (immutable detector observation) and
PolicyDecision (policy layer's verdict). The default policy `decide`
rejects on any signal, exactly matching today's pre-refactor behavior
where each behavioral check raised directly.

No external behavior change — this is the abstraction layer that the
validator will be wired through in the next commit. Phases D/E will
use it to enable outcome calibration without touching detectors.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 2: Refactor _check_theme_cluster_fixation → _detect

**Files:**
- Modify: `thesis_validator.py` (the `_check_theme_cluster_fixation` function and its call site)
- Test: existing tests in `tests/test_validator_gate_coverage.py`, `tests/test_stage1_rules.py` must still pass with no changes

- [ ] **Step 1: Read the existing implementation to understand its raise shape**

Read `thesis_validator.py:380-410` (the current `_check_theme_cluster_fixation` function).

- [ ] **Step 2: Add a regression test pinning the rejection_code + message remain stable**

Add to `tests/test_behavior_signals.py`:

```python
def test_theme_cluster_fixation_detector_returns_signal_when_pattern_fires() -> None:
    """When 4 of last 7 priors share keywords with the proposal, the detector
    returns a signal. Signal carries the same code that the pre-refactor
    raise produced."""
    from research_types import (
        ExpectedEffect, Disqualifier, ResearchThesis,
    )
    from thesis_validator import _detect_theme_cluster_fixation

    proposal = ResearchThesis(
        thesis_id="ema-new-v1",
        strategy_family="ema",
        hypothesis="x", mechanism="x",
        mechanism_dimension="entry_timing",
        dimension_novelty="x" * 50,
        theme_keywords=["opening", "stop_distance"],
        config_changes={"some_key": 1},
        expected_effects=[ExpectedEffect(metric="profit_factor", direction="increase")],
        disqualifiers=[Disqualifier(name="x", condition="y", kind="mechanism_evidence")],
        falsification_or_alternative="z" * 100,
    )
    def prior(thesis_id: str, kw: list[str]) -> dict:
        return {
            "thesis_id": thesis_id,
            "config_changes": {f"k_{thesis_id}": 1},
            "outcome": "compiled",
            "thesis_details": {"theme_keywords": kw},
        }
    priors = [
        prior("p1", ["opening", "a"]),
        prior("p2", ["opening", "b"]),
        prior("p3", ["opening", "c"]),
        prior("p4", ["d"]),
    ]
    sig = _detect_theme_cluster_fixation(proposal, priors)
    assert sig is not None
    assert sig.code == "thesis_quality_theme_cluster_fixation"
    assert sig.severity == "block"
    assert sig.confidence > 0.5  # >=4/7 overlap
    assert "overlap_count" in sig.evidence
    assert sig.evidence["overlap_count"] == 4


def test_theme_cluster_fixation_detector_returns_none_when_pattern_absent() -> None:
    """When fewer than 4 priors share keywords, the detector returns None."""
    from research_types import (
        ExpectedEffect, Disqualifier, ResearchThesis,
    )
    from thesis_validator import _detect_theme_cluster_fixation

    proposal = ResearchThesis(
        thesis_id="ema-new-v1",
        strategy_family="ema",
        hypothesis="x", mechanism="x",
        mechanism_dimension="entry_timing",
        dimension_novelty="x" * 50,
        theme_keywords=["unique"],
        config_changes={"some_key": 1},
        expected_effects=[ExpectedEffect(metric="profit_factor", direction="increase")],
        disqualifiers=[Disqualifier(name="x", condition="y", kind="mechanism_evidence")],
        falsification_or_alternative="z" * 100,
    )
    priors = [{"thesis_id": "p1", "config_changes": {}, "thesis_details": {"theme_keywords": ["other"]}}]
    assert _detect_theme_cluster_fixation(proposal, priors) is None
```

- [ ] **Step 3: Run new tests to verify they fail (function doesn't exist yet)**

Run: `.venv/bin/python -m pytest tests/test_behavior_signals.py -v`
Expected: ImportError on `_detect_theme_cluster_fixation` (renamed from `_check_*`).

- [ ] **Step 4: Refactor `_check_theme_cluster_fixation` → `_detect_theme_cluster_fixation`**

In `thesis_validator.py`, replace the existing `_check_theme_cluster_fixation` function with:

```python
def _detect_theme_cluster_fixation(
    thesis: ResearchThesis,
    prior_theses: list[dict[str, Any]],
) -> "BehaviorSignal | None":
    """Detect when the proposed thesis fixates on a theme cluster.

    Returns a BehaviorSignal when >=4 of the last 7 priors (including the
    proposal itself) share at least one theme_keyword. Returns None when
    the pattern is absent.

    Confidence is proportional to the fraction of the window that overlaps:
    4/7 → 0.57, 7/7 → 1.0. Severity is "block" in Phase C to match the
    pre-refactor hard-block behavior.
    """
    proposed_keywords = {kw.strip() for kw in thesis.theme_keywords if kw.strip()}
    if not proposed_keywords:
        return None
    recent = prior_theses[-(B1_THEME_CLUSTER_WINDOW - 1):]
    if not recent:
        return None
    overlap_count = 1  # the proposed thesis itself
    overlapping_priors: list[str] = []
    for prior in recent:
        prior_kw = _theme_keywords_from_prior(prior)
        if prior_kw & proposed_keywords:
            overlap_count += 1
            overlapping_priors.append(str(prior.get("thesis_id") or "?"))
    if overlap_count < B1_THEME_CLUSTER_THRESHOLD:
        return None
    return BehaviorSignal(
        code="thesis_quality_theme_cluster_fixation",
        confidence=min(1.0, overlap_count / B1_THEME_CLUSTER_WINDOW),
        severity="block",
        summary=(
            f"Theme-cluster fixation: {overlap_count} of last "
            f"{B1_THEME_CLUSTER_WINDOW} theses share keywords {sorted(proposed_keywords)} "
            f"(overlapping priors: {overlapping_priors}). Propose from a different "
            f"mechanism dimension, or justify novelty in dimension_novelty."
        ),
        evidence={
            "overlap_count": overlap_count,
            "window": B1_THEME_CLUSTER_WINDOW,
            "shared_keywords": sorted(proposed_keywords),
            "overlapping_priors": overlapping_priors,
        },
        remediation=(
            "Propose from a different mechanism_dimension",
            "If staying in this dimension, use distinct theme_keywords",
        ),
    )
```

Add at the top of `thesis_validator.py`:

```python
from behavior_signals import BehaviorSignal, PolicyDecision, decide as _policy_decide
```

- [ ] **Step 5: Wire `_run_behavioral_pass` to use the detector + policy**

Find `_run_behavioral_pass` (around line 1390). It currently calls `_check_theme_cluster_fixation` directly (which raises). Update it to use the new shape — but ONLY for theme_cluster (we'll do the other 3 in subsequent tasks):

```python
def _run_behavioral_pass(
    thesis: ResearchThesis,
    prior_theses: list[dict[str, Any]] | None = None,
) -> None:
    """Stage 1 sub-section: behavior-pattern detection + policy.

    Each behavior detector returns a BehaviorSignal | None. The signals
    flow through the policy layer (behavior_signals.decide) which decides
    whether to reject. A reject is translated back to ThesisValidationError
    so external callers see the same API as before.
    """
    signals: list[BehaviorSignal] = []

    if prior_theses:
        if (sig := _detect_theme_cluster_fixation(thesis, prior_theses)) is not None:
            signals.append(sig)
        _check_needs_code_starvation(thesis, prior_theses)
        _check_direction_whipsaw(thesis, prior_theses)

    _check_qualitative_disqualifier_present(thesis)

    decision = _policy_decide(signals)
    if decision.action == "reject":
        triggering = decision.signals[0]
        raise ThesisValidationError(
            triggering.summary,
            rejection_code=triggering.code,
            evidence=dict(triggering.evidence),
            remediation_hint=" / ".join(triggering.remediation) if triggering.remediation else "",
        )
    # accept and accept_with_warning paths: no raise. Warnings will be
    # surfaced to the conductor in a future phase.
```

(The other 3 `_check_*` functions still raise directly for now — they'll be converted in subsequent tasks.)

- [ ] **Step 6: Verify all existing tests still pass + new tests pass**

Run: `.venv/bin/python -m pytest tests/test_behavior_signals.py tests/test_validator_gate_coverage.py tests/test_stage1_rules.py tests/test_validator_subsections.py -v`
Expected: all green. The old test `test_gate_thesis_quality_theme_cluster_fixation` still passes because the same `rejection_code` is raised — just via the new path.

- [ ] **Step 7: Commit**

```bash
git add thesis_validator.py tests/test_behavior_signals.py
git commit -m "$(cat <<'EOF'
refactor(validator): theme_cluster_fixation through behavior_signals

Converts _check_theme_cluster_fixation → _detect_theme_cluster_fixation:
the detector now returns a BehaviorSignal instead of raising. The validator's
_run_behavioral_pass collects signals and routes them through the
default policy, which translates back to a raise with the same
rejection_code and evidence as before.

External behavior unchanged. Establishes the wiring; the other 3
behavioral detectors are converted in subsequent commits.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: Refactor _check_needs_code_starvation → _detect

**Files:**
- Modify: `thesis_validator.py`
- Test: `tests/test_behavior_signals.py`

- [ ] **Step 1: Add a regression test**

Add to `tests/test_behavior_signals.py`:

```python
def test_needs_code_starvation_detector_returns_signal_at_streak_3() -> None:
    """Three consecutive priors with requires_code_change=true and no run
    in between → signal fired."""
    from research_types import ExpectedEffect, Disqualifier, ResearchThesis
    from thesis_validator import _detect_needs_code_starvation

    proposal = ResearchThesis(
        thesis_id="ema-new-v1",
        strategy_family="ema",
        hypothesis="x", mechanism="x",
        mechanism_dimension="entry_timing",
        dimension_novelty="x" * 50,
        config_changes={},
        requires_code_change=True,
        requested_primitives=["new_primitive"],
        expected_effects=[ExpectedEffect(metric="profit_factor", direction="increase")],
        disqualifiers=[Disqualifier(name="x", condition="y", kind="mechanism_evidence")],
        falsification_or_alternative="z" * 100,
    )
    def code_prior(thesis_id: str) -> dict:
        return {
            "thesis_id": thesis_id,
            "config_changes": {f"k_{thesis_id}": 1},
            "outcome": "needs_code",
            "thesis_details": {"requires_code_change": True},
        }
    priors = [code_prior("p1"), code_prior("p2"), code_prior("p3")]
    sig = _detect_needs_code_starvation(proposal, priors)
    assert sig is not None
    assert sig.code == "thesis_quality_needs_code_starvation"
    assert sig.severity == "block"
    assert sig.confidence == 1.0
```

- [ ] **Step 2: Verify test fails (no `_detect_needs_code_starvation` yet)**

Run: `.venv/bin/python -m pytest tests/test_behavior_signals.py::test_needs_code_starvation_detector_returns_signal_at_streak_3 -v`
Expected: ImportError.

- [ ] **Step 3: Refactor**

Find the existing `_check_needs_code_starvation` function in `thesis_validator.py`. Replace it with:

```python
def _detect_needs_code_starvation(
    thesis: ResearchThesis,
    prior_theses: list[dict[str, Any]],
) -> "BehaviorSignal | None":
    """Detect when the conductor is queueing engine work without progress.

    Fires when 3+ consecutive most-recent priors required code changes
    without a completed run between them, and this thesis also requires
    a code change. Returns None otherwise.
    """
    if not thesis.requires_code_change:
        return None
    streak = 0
    for prior in reversed(prior_theses):
        if _prior_was_run(prior):
            break
        if _prior_required_code_change(prior):
            streak += 1
        else:
            break
        if streak >= B3_NEEDS_CODE_STARVATION_LIMIT:
            break
    if streak < B3_NEEDS_CODE_STARVATION_LIMIT:
        return None
    return BehaviorSignal(
        code="thesis_quality_needs_code_starvation",
        confidence=1.0,
        severity="block",
        summary=(
            f"needs_code starvation: {streak} consecutive prior theses required "
            f"engine changes without running. Propose a non-code thesis to break "
            f"the queue (set requires_code_change=false and operate on existing config keys)."
        ),
        evidence={"streak": streak, "limit": B3_NEEDS_CODE_STARVATION_LIMIT},
        remediation=(
            "Set requires_code_change=false and use existing config keys",
        ),
    )
```

Update `_run_behavioral_pass` to call the detector:

```python
    if prior_theses:
        if (sig := _detect_theme_cluster_fixation(thesis, prior_theses)) is not None:
            signals.append(sig)
        if (sig := _detect_needs_code_starvation(thesis, prior_theses)) is not None:
            signals.append(sig)
        _check_direction_whipsaw(thesis, prior_theses)
```

- [ ] **Step 4: Verify tests pass**

Run: `.venv/bin/python -m pytest tests/test_behavior_signals.py tests/test_validator_gate_coverage.py tests/test_stage1_rules.py tests/test_stage1_rules_part2.py -v`
Expected: all green.

- [ ] **Step 5: Commit**

```bash
git add thesis_validator.py tests/test_behavior_signals.py
git commit -m "$(cat <<'EOF'
refactor(validator): needs_code_starvation through behavior_signals

Converts _check_needs_code_starvation → _detect_needs_code_starvation
following the same pattern as theme_cluster_fixation. External rejection
shape unchanged.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 4: Refactor _check_direction_whipsaw → _detect

**Files:**
- Modify: `thesis_validator.py`
- Test: `tests/test_behavior_signals.py`

- [ ] **Step 1: Add a regression test**

Add to `tests/test_behavior_signals.py`:

```python
def test_direction_whipsaw_detector_returns_signal_on_flip() -> None:
    """Prior tightened a theme; this thesis loosens it without citation."""
    from research_types import ExpectedEffect, Disqualifier, ResearchThesis
    from thesis_validator import _detect_direction_whipsaw

    proposal = ResearchThesis(
        thesis_id="ema-loosen-stops-v1",
        strategy_family="ema",
        hypothesis="x", mechanism="x",
        mechanism_dimension="entry_timing",
        dimension_novelty="x" * 50,
        theme_keywords=["stop_distance"],
        prior_lever_outcomes=[],
        config_changes={"different_key": 1},
        novel_connection=(
            "Stop-distance lever is approached as a regime-dependent floor "
            "rather than an absolute threshold tested previously."
        ),
        causal_cluster="stop-distance",
        underexplored_dimensions_considered=["risk_structure"],
        expected_effects=[ExpectedEffect(metric="profit_factor", direction="increase")],
        disqualifiers=[Disqualifier(name="x", condition="y", kind="mechanism_evidence")],
        falsification_or_alternative="z" * 100,
    )
    prior = {
        "thesis_id": "ema-tighten-stops-v0",
        "config_changes": {"some_other_key": 5},
        "outcome": "compiled",
        "thesis_details": {"theme_keywords": ["stop_distance"]},
    }
    sig = _detect_direction_whipsaw(proposal, [prior])
    assert sig is not None
    assert sig.code == "thesis_quality_direction_whipsaw"
    assert sig.severity == "block"
```

- [ ] **Step 2: Verify test fails**

Run: `.venv/bin/python -m pytest tests/test_behavior_signals.py::test_direction_whipsaw_detector_returns_signal_on_flip -v`
Expected: ImportError on `_detect_direction_whipsaw`.

- [ ] **Step 3: Refactor**

Find `_check_direction_whipsaw` in `thesis_validator.py`. Replace its body to collect-and-return rather than raise. The function structure stays largely the same; only the `raise ThesisValidationError(...)` becomes `return BehaviorSignal(...)` and the early `return` paths stay as-is.

```python
def _detect_direction_whipsaw(
    thesis: ResearchThesis,
    prior_theses: list[dict[str, Any]],
) -> "BehaviorSignal | None":
    """Detect when the thesis flips the direction of a lever already tested
    by a prior thesis on the same theme, without citing it.

    Direction is determined per (current_thesis, prior) pair using the data
    signal first (shared numeric key with lever-name convention), falling
    back to word-boundary text matching when no data signal is available.
    """
    proposed_kw = {kw.strip() for kw in thesis.theme_keywords if kw.strip()}
    if not proposed_kw:
        return None
    cited_prior_ids = {p.prior_thesis_id for p in thesis.prior_lever_outcomes}

    for prior in prior_theses:
        prior_kw = _theme_keywords_from_prior(prior)
        if not (prior_kw & proposed_kw):
            continue
        prior_id = str(prior.get("thesis_id") or "")
        if prior_id in cited_prior_ids:
            continue
        prior_dir = _prior_direction(prior)
        if prior_dir is None:
            continue
        proposed_dir = _proposed_direction(thesis, prior)
        opposing = "widen" if prior_dir == "tighten" else "tighten"
        if proposed_dir != opposing:
            continue
        return BehaviorSignal(
            code="thesis_quality_direction_whipsaw",
            confidence=1.0,
            severity="block",
            summary=(
                f"Direction whipsaw: prior thesis '{prior_id}' tested the {prior_dir} "
                f"direction on lever theme {sorted(proposed_kw)}, and this thesis "
                f"flips to {proposed_dir} without acknowledgment. Cite '{prior_id}' "
                f"in prior_lever_outcomes (with direction_then, outcome, and why_retry) "
                f"or propose from a different mechanism dimension."
            ),
            evidence={
                "prior_thesis_id": prior_id,
                "opposing_direction": opposing,
                "proposed_direction": proposed_dir,
                "lever_theme": sorted(proposed_kw),
            },
            remediation=(
                f"Cite '{prior_id}' in prior_lever_outcomes",
                "Or propose from a different mechanism dimension",
            ),
        )
    return None
```

Update `_run_behavioral_pass`:

```python
    if prior_theses:
        if (sig := _detect_theme_cluster_fixation(thesis, prior_theses)) is not None:
            signals.append(sig)
        if (sig := _detect_needs_code_starvation(thesis, prior_theses)) is not None:
            signals.append(sig)
        if (sig := _detect_direction_whipsaw(thesis, prior_theses)) is not None:
            signals.append(sig)
    _check_qualitative_disqualifier_present(thesis)
```

- [ ] **Step 4: Verify tests pass**

Run: `.venv/bin/python -m pytest tests/ --ignore=tests/test_experiment_db_timestamps.py --ignore=tests/test_vps_runner_config.py -q 2>&1 | tail -5`
Expected: all green (899 passed, 2 skipped + new tests).

- [ ] **Step 5: Commit**

```bash
git add thesis_validator.py tests/test_behavior_signals.py
git commit -m "$(cat <<'EOF'
refactor(validator): direction_whipsaw through behavior_signals

Converts _check_direction_whipsaw → _detect_direction_whipsaw.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 5: Refactor _check_qualitative_disqualifier_present → _detect

**Files:**
- Modify: `thesis_validator.py`
- Test: `tests/test_behavior_signals.py`

- [ ] **Step 1: Add a regression test**

```python
def test_missing_mechanism_evidence_disqualifier_detector_fires_when_all_metric_threshold() -> None:
    from research_types import ExpectedEffect, Disqualifier, ResearchThesis
    from thesis_validator import _detect_missing_mechanism_evidence_disqualifier

    proposal = ResearchThesis(
        thesis_id="ema-x", strategy_family="ema",
        hypothesis="x", mechanism="x",
        mechanism_dimension="entry_timing",
        dimension_novelty="x" * 50,
        config_changes={"k": 1},
        expected_effects=[ExpectedEffect(metric="profit_factor", direction="increase")],
        disqualifiers=[Disqualifier(name="x", condition="y" * 100, kind="metric_threshold")],
        falsification_or_alternative="z" * 100,
    )
    sig = _detect_missing_mechanism_evidence_disqualifier(proposal)
    assert sig is not None
    assert sig.code == "thesis_quality_missing_mechanism_evidence_disqualifier"
    assert sig.severity == "block"


def test_missing_mechanism_evidence_disqualifier_detector_returns_none_when_substantive_present() -> None:
    from research_types import ExpectedEffect, Disqualifier, ResearchThesis
    from thesis_validator import _detect_missing_mechanism_evidence_disqualifier

    proposal = ResearchThesis(
        thesis_id="ema-x", strategy_family="ema",
        hypothesis="x", mechanism="x",
        mechanism_dimension="entry_timing",
        dimension_novelty="x" * 50,
        config_changes={"k": 1},
        expected_effects=[ExpectedEffect(metric="profit_factor", direction="increase")],
        disqualifiers=[Disqualifier(name="x", condition="z" * 50, kind="mechanism_evidence")],
        falsification_or_alternative="z" * 100,
    )
    assert _detect_missing_mechanism_evidence_disqualifier(proposal) is None
```

- [ ] **Step 2: Verify tests fail**

Run: `.venv/bin/python -m pytest tests/test_behavior_signals.py -k "mechanism_evidence" -v`
Expected: ImportError.

- [ ] **Step 3: Refactor**

Replace `_check_qualitative_disqualifier_present` with:

```python
def _detect_missing_mechanism_evidence_disqualifier(
    thesis: ResearchThesis,
) -> "BehaviorSignal | None":
    """Detect when no substantive mechanism_evidence disqualifier is present.

    Requires at least one disqualifier with kind='mechanism_evidence' AND
    condition ≥40 chars. Pure metric_threshold disqualifiers are pass/fail
    criteria, not Popperian disconfirmers; substantively-short mechanism_evidence
    conditions are ceremonial (the LLM can game the enum without writing real
    falsification evidence).
    """
    if not thesis.disqualifiers:
        return None  # absence handled by structural_missing_disqualifiers
    has_substantive = any(
        d.kind == "mechanism_evidence"
        and len(d.condition.strip()) >= _MIN_MECHANISM_EVIDENCE_CONDITION_CHARS
        for d in thesis.disqualifiers
    )
    if has_substantive:
        return None
    return BehaviorSignal(
        code="thesis_quality_missing_mechanism_evidence_disqualifier",
        confidence=1.0,
        severity="block",
        summary=(
            "Need at least one disqualifier with kind='mechanism_evidence' AND a "
            f"condition ≥{_MIN_MECHANISM_EVIDENCE_CONDITION_CHARS} chars describing "
            "an observable data pattern that would falsify the mechanism. "
            "Pure kind='metric_threshold' disqualifiers ('PF must improve by 5%') "
            "are pass/fail criteria, not Popperian disconfirmers."
        ),
        evidence={
            "min_condition_chars": _MIN_MECHANISM_EVIDENCE_CONDITION_CHARS,
            "disqualifier_count": len(thesis.disqualifiers),
        },
        remediation=(
            "Add a disqualifier with kind='mechanism_evidence' and a substantive condition",
        ),
    )
```

Update `_run_behavioral_pass`:

```python
def _run_behavioral_pass(
    thesis: ResearchThesis,
    prior_theses: list[dict[str, Any]] | None = None,
) -> None:
    """Stage 1 sub-section: behavior-pattern detection + policy.

    Each behavior detector returns a BehaviorSignal | None. Signals flow
    through the policy layer (behavior_signals.decide). A reject decision
    is translated back to ThesisValidationError so external callers see
    the same API as before the refactor.
    """
    signals: list[BehaviorSignal] = []

    if prior_theses:
        if (sig := _detect_theme_cluster_fixation(thesis, prior_theses)) is not None:
            signals.append(sig)
        if (sig := _detect_needs_code_starvation(thesis, prior_theses)) is not None:
            signals.append(sig)
        if (sig := _detect_direction_whipsaw(thesis, prior_theses)) is not None:
            signals.append(sig)

    if (sig := _detect_missing_mechanism_evidence_disqualifier(thesis)) is not None:
        signals.append(sig)

    decision = _policy_decide(signals)
    if decision.action == "reject":
        triggering = decision.signals[0]
        raise ThesisValidationError(
            triggering.summary,
            rejection_code=triggering.code,
            evidence=dict(triggering.evidence),
            remediation_hint=" / ".join(triggering.remediation) if triggering.remediation else "",
        )
```

- [ ] **Step 4: Verify all tests pass**

Run: `.venv/bin/python -m pytest tests/ --ignore=tests/test_experiment_db_timestamps.py --ignore=tests/test_vps_runner_config.py -q 2>&1 | tail -5`
Expected: 899 + new pass, 2 skipped.

- [ ] **Step 5: Commit**

```bash
git add thesis_validator.py tests/test_behavior_signals.py
git commit -m "$(cat <<'EOF'
refactor(validator): mechanism_evidence_disqualifier through behavior_signals

Converts _check_qualitative_disqualifier_present →
_detect_missing_mechanism_evidence_disqualifier. All four behavioral
checks now flow through behavior_signals.decide; the validator is a thin
translator from policy decisions to ThesisValidationError.

External behavior unchanged. The seam is now complete for Phases D/E.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 6: Verify the seam — end-to-end policy test

**Files:**
- Test: `tests/test_behavior_signals.py`

- [ ] **Step 1: Add an integration test that exercises the full path**

```python
def test_run_behavioral_pass_translates_policy_reject_to_raise() -> None:
    """End-to-end: when a detector fires, the validator raises with the
    detector's code (mediated by the policy)."""
    from research_types import ExpectedEffect, Disqualifier, ResearchThesis
    from thesis_validator import ThesisValidationError, _run_behavioral_pass

    proposal = ResearchThesis(
        thesis_id="ema-x", strategy_family="ema",
        hypothesis="x", mechanism="x",
        mechanism_dimension="entry_timing",
        dimension_novelty="x" * 50,
        config_changes={"k": 1},
        expected_effects=[ExpectedEffect(metric="profit_factor", direction="increase")],
        # Only metric_threshold → mechanism_evidence detector fires
        disqualifiers=[Disqualifier(name="x", condition="y" * 100, kind="metric_threshold")],
        falsification_or_alternative="z" * 100,
    )
    import pytest
    with pytest.raises(ThesisValidationError) as exc_info:
        _run_behavioral_pass(proposal, prior_theses=[])
    assert exc_info.value.rejection_code == "thesis_quality_missing_mechanism_evidence_disqualifier"


def test_run_behavioral_pass_does_not_raise_when_no_signals_fire() -> None:
    """End-to-end: with no signals, the validator returns without raising."""
    from research_types import ExpectedEffect, Disqualifier, ResearchThesis
    from thesis_validator import _run_behavioral_pass

    proposal = ResearchThesis(
        thesis_id="ema-x", strategy_family="ema",
        hypothesis="x", mechanism="x",
        mechanism_dimension="entry_timing",
        dimension_novelty="x" * 50,
        config_changes={"k": 1},
        expected_effects=[ExpectedEffect(metric="profit_factor", direction="increase")],
        disqualifiers=[Disqualifier(name="x", condition="z" * 50, kind="mechanism_evidence")],
        falsification_or_alternative="z" * 100,
    )
    _run_behavioral_pass(proposal, prior_theses=[])  # no raise expected
```

- [ ] **Step 2: Run**

Run: `.venv/bin/python -m pytest tests/test_behavior_signals.py -v`
Expected: all pass.

- [ ] **Step 3: Final full sweep**

Run: `.venv/bin/python scripts/check_prompt_drift.py && .venv/bin/python -m pytest tests/ --ignore=tests/test_experiment_db_timestamps.py --ignore=tests/test_vps_runner_config.py -q 2>&1 | tail -5`
Expected: drift checker OK, all tests pass.

- [ ] **Step 4: Commit**

```bash
git add tests/test_behavior_signals.py
git commit -m "$(cat <<'EOF'
test(validator): end-to-end seam test for behavior_signals translation

Pins the behavior that _run_behavioral_pass translates a policy
reject decision into the same ThesisValidationError shape as before the
refactor. Anchors Phase A+B+C against accidental regression.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Out of scope (Phase D and beyond)

- Outcome calibration (Phase D) — needs persistence + offline calibrator script
- Per-strategy policy configs (Phase E) — needs config-loading layer
- Warning surfacing into next round's prompt (Phase E variant) — needs prompt-rendering change
- Removing/relaxing severity on any specific detector — needs outcome data to justify
