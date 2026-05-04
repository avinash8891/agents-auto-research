"""Ratchet: per-round keep/revert recommendation log.

Cold start (no eval baseline) keeps iff outcome ∈ {compiled, stopped}.
With a baseline, classifies on primary-metric delta in stdev units.
Never invokes git; this is a recommendation log only.
"""

from __future__ import annotations

import json
from pathlib import Path

from autoresearch_logging import get_logger
from eval_metrics import (
    DECISION_ABORTED,
    DECISION_INCONCLUSIVE,
    DECISION_KEEP,
    DECISION_REVERT,
    DECISION_SKIP,
    KEEP_OUTCOMES,
    EvalResult,
    classify_delta_in_stdevs,
    compare_eval_results,
)
from improvement_flags import ratchet_enabled
from persistence_utils import safe_stat_mtime

log = get_logger(__name__)

DECISIONS_DIRNAME = "improvement_reports/ratchet"
DECISIONS_FILENAME = "decisions.jsonl"

# Verdict precedence: revert > inconclusive > keep. Used to combine the
# Ratchet verdict (per-round) with the HALO-apply verdict (per-edit-cycle)
# when both are available — the more conservative wins. False-revert
# wastes a round; false-keep ratchets in a regression.
_VERDICT_RANK = {
    DECISION_REVERT: 0,
    DECISION_INCONCLUSIVE: 1,
    DECISION_KEEP: 2,
}

# HALO-apply statuses that do not contribute a verdict opinion.
# - DECISION_SKIP: flag off or not run this round, no signal.
# Note: DECISION_ABORTED is NOT in this set. It contributes DECISION_INCONCLUSIVE
# via _apply_verdict_contribution (measurement gap — defer, but it is still an opinion).
_APPLY_STATUS_NO_OPINION = frozenset({DECISION_SKIP})


def _decision_keep_if_compiled(outcome: str) -> tuple[str, str]:
    if outcome in KEEP_OUTCOMES:
        return DECISION_KEEP, f"outcome={outcome} is in keep-set"
    return (
        DECISION_REVERT,
        f"outcome={outcome} not in keep-set {sorted(KEEP_OUTCOMES)}",
    )


def _load_eval_result(path: Path) -> EvalResult | None:
    try:
        return EvalResult.from_dict(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, json.JSONDecodeError) as exc:
        log.error(
            f"RATCHET could not read {path}: {type(exc).__name__}: {exc}. "
            f"Action: inspect or remove the corrupt eval result."
        )
        return None


def _decision_benchmark(current_path: Path, eval_dir: Path) -> tuple[str, str, dict | None]:
    if not eval_dir.exists():
        return DECISION_INCONCLUSIVE, "eval_results dir missing", None
    candidates = sorted(
        (p for p in eval_dir.glob("*.json") if p != current_path),
        key=safe_stat_mtime,
    )
    if not candidates:
        return DECISION_INCONCLUSIVE, "no prior eval result", None
    prior_path = candidates[-1]
    current = _load_eval_result(current_path)
    prior = _load_eval_result(prior_path)
    if current is None or prior is None:
        return DECISION_INCONCLUSIVE, "failed to load eval payload", None
    try:
        delta = compare_eval_results(current, prior)
    except ValueError as exc:
        return DECISION_INCONCLUSIVE, str(exc), None
    detail = {
        "primary_metric_name": delta["primary_metric_name"],
        "current": delta["current"],
        "prior": delta["prior"],
        "delta": delta["delta"],
        "delta_in_stdevs": delta["delta_in_stdevs"],
        "prior_path": str(prior_path),
    }
    decision, rationale = classify_delta_in_stdevs(delta["delta_in_stdevs"], delta=delta["delta"])
    return decision, rationale, detail


def _apply_verdict_contribution(apply_decision: dict | None) -> tuple[str | None, str]:
    """Map a HALO-apply decision dict to a verdict + rationale.

    Returns ``(None, reason)`` when HALO-apply has no opinion to contribute
    (flag off, not run this round). Aborted statuses contribute
    INCONCLUSIVE (we have a measurement gap, not a bad signal).
    """
    if apply_decision is None:
        return None, "halo_apply not run"
    status = apply_decision.get("status")
    if status in _APPLY_STATUS_NO_OPINION or status is None:
        return None, "halo_apply skipped"
    if status == DECISION_ABORTED:
        return DECISION_INCONCLUSIVE, f"halo_apply aborted: {apply_decision.get('reason', '?')}"
    if status in _VERDICT_RANK:
        return status, f"halo_apply={status}"
    return None, f"halo_apply unknown status: {status!r}"


def _combine_verdicts(
    ratchet: tuple[str, str], apply_contribution: tuple[str | None, str]
) -> tuple[str, str]:
    """Conservative-min combine: pick the more-conservative verdict.

    revert > inconclusive > keep. Returns the joined rationale so the
    audit row records both signals.
    """
    r_verdict, r_reason = ratchet
    a_verdict, a_reason = apply_contribution
    if a_verdict is None:
        return r_verdict, r_reason
    if _VERDICT_RANK[a_verdict] < _VERDICT_RANK[r_verdict]:
        return a_verdict, f"{a_reason} overrides ratchet={r_verdict} ({r_reason})"
    return r_verdict, f"{r_reason}; {a_reason} concurs"


def record_round_decision(
    controller,
    round_number: int,
    outcome: str,
    eval_result_path: Path | None,
    *,
    apply_decision: dict | None = None,
) -> str:
    """Append a keep/revert decision row; return final decision string.

    Pass ``eval_result_path=None`` to force the cold-start oracle.
    ``apply_decision`` is the HALO-apply result for this round; combined
    with the Ratchet verdict via conservative-min (revert > inconclusive >
    keep). Both raw signals are preserved in the audit row.
    """
    if not ratchet_enabled():
        return DECISION_SKIP
    from persistence_utils import utc_now_iso8601

    try:
        controller_root = Path(controller.root)
    except AttributeError:
        log.warning(
            "RATCHET controller has no root attribute; skipping. "
            "Action: pass an AutoresearchController-shaped object."
        )
        return DECISION_SKIP

    detail: dict | None = None
    if eval_result_path is None:
        ratchet_verdict, ratchet_rationale = _decision_keep_if_compiled(outcome)
        primary_metric = None
    else:
        ratchet_verdict, ratchet_rationale, detail = _decision_benchmark(
            eval_result_path, eval_result_path.parent
        )
        primary_metric = (detail or {}).get("current")

    apply_contribution = _apply_verdict_contribution(apply_decision)
    decision, rationale = _combine_verdicts(
        (ratchet_verdict, ratchet_rationale), apply_contribution
    )

    decisions_dir = controller_root / DECISIONS_DIRNAME
    decisions_dir.mkdir(parents=True, exist_ok=True)
    row = {
        "round": round_number,
        "outcome": outcome,
        "primary_metric": primary_metric,
        "delta_vs_prior": (detail or {}).get("delta"),
        "delta_in_stdevs": (detail or {}).get("delta_in_stdevs"),
        "decision": decision,
        "rationale": rationale,
        "ratchet_verdict_raw": ratchet_verdict,
        "halo_apply_verdict": apply_contribution[0],
        "halo_apply_status": (apply_decision or {}).get("status"),
        "halo_apply_reason": (apply_decision or {}).get("reason"),
        "ts": utc_now_iso8601(),
    }
    with (decisions_dir / DECISIONS_FILENAME).open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row) + "\n")
    log.info(
        f"RATCHET round={round_number} outcome={outcome} decision={decision} "
        f"rationale={rationale}"
    )
    return decision
