"""Prior-thesis dedupe detectors and payload normalization for the v2 engine.

The legacy full-thesis validator was deleted with the v2 redesign. What
remains is the surface the mechanism path actually uses:

- `normalize_thesis_payload` — canonicalizes payloads before
  `ResearchThesis.model_validate` (compiler adapter + needs_code halt path).
- `load_prior_theses` + `_detect_neighboring_threshold` /
  `_detect_config_key_overlap` — config-level dedupe consumed by
  `autoresearch_research._validate_mechanism_dedupe`.
- `ThesisValidationError` / `infer_rejection_code` — structured rejection
  carrier and the legacy-message → code map used when reading persisted
  rejection artifacts from pre-v2 jobs.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from autoresearch_logging import get_logger
from autoresearch_runtime_paths import iter_family_backtest_db_paths
from behavior_signals import BehaviorSignal
from research_types import ResearchThesis

log = get_logger(__name__)

CONFIG_OVERLAP_THRESHOLD = 0.5

# Metadata/sentinel keys that are carried in config_changes for orchestration,
# but are not actual strategy parameters. Including these in novelty checks
# makes every engine-change thesis look like a duplicate of the previous one.
CONFIG_OVERLAP_IGNORED_KEYS = frozenset(
    {
        "requires_engine_change",
        # Variant observed in production: agents emit `requires_new_config_keys`
        # as a sentinel boolean alongside (or instead of) `requires_engine_change`.
        # Bookkeeping, not a real config key. Note: `new_config_keys_needed` is
        # NOT ignored here because it is sometimes used as a parent dict whose
        # children are real config keys; the upstream metadata-key check rejects
        # the leaf case.
        "requires_new_config_keys",
    }
)
# Prefix-matched sentinels: agents label which engine change is needed via
# `requires_engine_change__<descriptor>`. The descriptor is bookkeeping, not a
# real config key, so any key with this prefix is ignored for overlap purposes.
CONFIG_OVERLAP_IGNORED_PREFIXES: tuple[str, ...] = ("requires_engine_change__",)
CONFIG_CHANGES_METADATA_KEYS = frozenset({"requires_code_change", "new_config_keys_needed"})


def _is_overlap_ignored_key(key: str) -> bool:
    if key in CONFIG_OVERLAP_IGNORED_KEYS:
        return True
    return any(key.startswith(prefix) for prefix in CONFIG_OVERLAP_IGNORED_PREFIXES)


class ThesisValidationError(ValueError):
    """Raised when a thesis fails validation.

    Optional structured fields support StructuredRejection persistence. New
    raises should pass `rejection_code` and `evidence`; legacy raises with
    only a message still work and fall back to `infer_rejection_code` for
    a best-effort code assignment.
    """

    def __init__(
        self,
        message: str,
        *,
        rejection_code: str = "",
        evidence: dict[str, Any] | None = None,
        remediation_hint: str = "",
    ) -> None:
        super().__init__(message)
        self.rejection_code = rejection_code
        self.evidence = dict(evidence or {})
        self.remediation_hint = remediation_hint


def infer_rejection_code(message: str) -> str:
    """Best-effort mapping from a legacy ThesisValidationError message → code.

    Used when a raise site has not yet been updated to pass `rejection_code`
    explicitly. Add new patterns as new rules land.
    """
    msg = message.lower()
    # config_validity_*
    if "config-key overlap" in msg:
        return "config_validity_config_key_overlap_real"
    if "do not construct" in msg or "points into runtime/" in msg:
        return "config_validity_base_config_path_runtime_construction"
    if "legacy experiments/ inheritance" in msg or "must be under configs/" in msg:
        return "config_validity_base_config_path_inheritance_blocked"
    if "must point to a json or yaml" in msg or "must be a relative repo path" in msg:
        return "config_validity_base_config_path_invalid"
    if "config_changes contains thesis metadata key" in msg:
        return "config_validity_config_changes_metadata_leak"
    if "falsification_or_alternative" in msg:
        return "structural_falsification_invalid"
    if "underexplored_dimensions_considered" in msg:
        return "structural_underexplored_dimensions_invalid"
    if "emergent" in msg and (
        "malformed" in msg or "new_dimension_name" in msg or "duplicates a core" in msg
    ):
        return "structural_emergent_thesis_malformed"
    if (
        "dimension_novelty must explain" in msg
        or "dimension_novelty is empty" in msg
        or "dimension_novelty must be" in msg
    ):
        return "structural_dimension_novelty_invalid"
    if "must be empty or the family baseline" in msg:
        return "config_validity_base_config_path_inheritance_blocked"
    if "neighboring threshold" in msg:
        return "config_validity_neighboring_threshold"
    if "has already been proposed" in msg:
        return "structural_thesis_id_repeated"
    if "required diagnostics not present" in msg:
        return "mechanical_validation_failure"
    # structural_*
    if "missing thesis_id" in msg:
        return "structural_missing_thesis_id"
    if "missing hypothesis" in msg:
        return "structural_missing_hypothesis"
    if "missing mechanism_dimension" in msg:
        return "structural_missing_mechanism_dimension"
    if "missing mechanism" in msg:
        return "structural_missing_mechanism"
    if "requested_primitives" in msg:
        return "structural_missing_requested_primitives"
    if "invalid mechanism_dimension" in msg or "mechanism_dimension" in msg:
        return "structural_mechanism_dimension_invalid"
    return "unspecified_validation_error"


def _prior_thesis_entry(row: dict[str, Any]) -> dict[str, Any]:
    details = row.get("thesis_details", {})
    if not isinstance(details, dict):
        details = {}
    entry = {
        "thesis_id": row.get("thesis_id", "unknown"),
        "config_changes": row.get("config_changes") or {},
        "outcome": row.get("validator_status", "unknown"),
        "thesis_details": details,
    }
    proposal_label = row.get("proposal_label") or details.get("proposal_label")
    if proposal_label:
        entry["proposal_label"] = proposal_label
    hypothesis = row.get("hypothesis") or details.get("hypothesis")
    if hypothesis:
        entry["hypothesis"] = hypothesis
    return entry


# Numeric tuning detector: same key, ratio within [1/_NEIGHBORING_RATIO,
# _NEIGHBORING_RATIO] is treated as a parameter tuning nudge.
_NEIGHBORING_RATIO = 2.0


def _is_numeric_value(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _neighboring_threshold_signal(
    *,
    key: str,
    prior_value: Any,
    new_value: Any,
    prior_thesis_id: str,
    ratio: float,
) -> BehaviorSignal:
    return BehaviorSignal(
        code="config_validity_neighboring_threshold",
        confidence=1.0,
        severity="block",
        summary=(
            f"Neighboring threshold: config key '{key}' was set to "
            f"{prior_value} by prior thesis '{prior_thesis_id}' "
            f"and this thesis sets it to {new_value} (ratio "
            f"{ratio:.2f}x, within {_NEIGHBORING_RATIO}x). This is "
            f"parameter tuning, not a new mechanism. Either justify a "
            f"structural boundary at this value or test a materially "
            f"different lever."
        ),
        evidence={
            "config_key": key,
            "prior_value": prior_value,
            "new_value": new_value,
            "prior_thesis_id": prior_thesis_id,
            "ratio": round(ratio, 4),
        },
        remediation=(
            "Test a materially different lever",
            "Or justify the structural boundary at this value",
        ),
    )


def _detect_neighboring_threshold(
    thesis: ResearchThesis, prior_theses: list[dict[str, Any]]
) -> BehaviorSignal | None:
    new_changes = thesis.config_changes or {}
    if not new_changes:
        return None
    for key, new_val in new_changes.items():
        if not _is_numeric_value(new_val):
            continue
        if _is_overlap_ignored_key(str(key)):
            continue
        for prior in prior_theses:
            prior_changes = prior.get("config_changes") or {}
            if key not in prior_changes:
                continue
            prior_val = prior_changes[key]
            if not _is_numeric_value(prior_val):
                continue
            new_f = float(new_val)
            prior_f = float(prior_val)
            if new_f == prior_f or new_f == 0 or prior_f == 0:
                continue
            ratio = new_f / prior_f
            if 1.0 / _NEIGHBORING_RATIO <= ratio <= _NEIGHBORING_RATIO:
                return _neighboring_threshold_signal(
                    key=str(key),
                    prior_value=prior_val,
                    new_value=new_val,
                    prior_thesis_id=str(prior.get("thesis_id", "?")),
                    ratio=ratio,
                )
    return None


def _slugify(text: str, max_words: int = 8) -> str:
    words = re.findall(r"[a-z0-9]+", text.lower())
    return "_".join(words[:max_words]) or "disqualifier"


def _infer_effect_metric(text: str) -> str:
    lowered = text.lower()
    if "trade" in lowered and ("count" in lowered or "frequency" in lowered):
        return "trade_count"
    if "drawdown" in lowered or "mdd" in lowered:
        return "max_drawdown"
    if "expectancy" in lowered or "exp" in lowered:
        return "median_expectancy"
    return "profit_factor"


def _infer_effect_direction(text: str) -> str:
    lowered = text.lower()
    if any(term in lowered for term in ("decrease", "decline", "drop", "reduce", "fall", "lower")):
        return "decrease"
    if any(term in lowered for term in ("not worse", "no worse", "maintain")):
        return "not_worse_than"
    return "increase"


def _normalize_expected_effect(effect: Any) -> Any:
    if isinstance(effect, str):
        return {
            "metric": _infer_effect_metric(effect),
            "direction": _infer_effect_direction(effect),
            "rationale": effect,
        }
    if isinstance(effect, dict) and "direction" not in effect and effect.get("metric"):
        normalized = dict(effect)
        normalized["direction"] = _infer_effect_direction(json.dumps(effect))
        return normalized
    return effect


def _normalize_disqualifier(disqualifier: Any) -> Any:
    if isinstance(disqualifier, str):
        return {
            "name": _slugify(disqualifier),
            "condition": disqualifier,
            "severity": "hard_fail",
        }
    if isinstance(disqualifier, dict) and "name" not in disqualifier:
        metric = str(disqualifier.get("metric") or "condition")
        condition = disqualifier.get("condition")
        threshold = disqualifier.get("threshold")
        reason = disqualifier.get("reason") or ""
        parts = [metric]
        if condition:
            parts.append(str(condition))
        if threshold is not None:
            parts.append(str(threshold))
        normalized = {
            "name": _slugify("_".join(parts)),
            "condition": " ".join(
                str(part)
                for part in (metric, condition, threshold, reason)
                if part not in (None, "")
            ),
            "severity": disqualifier.get("severity", "hard_fail"),
        }
        return normalized
    return disqualifier


def normalize_thesis_payload(raw: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(raw)
    family = normalized.get("family")
    if family and not normalized.get("strategy_family"):
        try:
            from strategies import STRATEGIES

            if family in STRATEGIES:
                normalized["strategy_family"] = family
        except Exception:
            # Leave the payload unchanged; validation will reject missing/invalid family.
            pass
    normalized["expected_effects"] = [
        _normalize_expected_effect(effect) for effect in (normalized.get("expected_effects") or [])
    ]
    normalized["disqualifiers"] = [
        _normalize_disqualifier(disqualifier)
        for disqualifier in (normalized.get("disqualifiers") or [])
    ]
    return normalized


# ---------------------------------------------------------------------------
# Guardrail 1: Config-key overlap detection
# ---------------------------------------------------------------------------


def load_prior_theses(
    root: Path,
    db: Any | None = None,
    *,
    strategy_family: str | None = None,
) -> list[dict[str, Any]]:
    """Load all previously proposed theses from canonical persistence."""
    prior: list[dict[str, Any]] = []
    if db is None:
        from backtest_run_db import BacktestRunDB

        for db_path in _iter_backtest_db_paths(root, family=strategy_family):
            db = BacktestRunDB(db_path)
            for line_no, row in enumerate(db.list_research_thesis_attempts(), start=1):
                if not isinstance(row, dict):
                    log.warning(
                        "PRIOR_THESES_SQLITE_INVALID path=%s row=%s error=not_a_dict "
                        "| hint=fix the malformed thesis-attempt row; it is ignored",
                        db_path,
                        line_no,
                    )
                    continue
                if strategy_family and row.get("strategy_family") != strategy_family:
                    continue
                if row.get("config_changes"):
                    prior.append(_prior_thesis_entry(row))
        return prior

    for line_no, row in enumerate(db.list_research_thesis_attempts(), start=1):
        if not isinstance(row, dict) or "thesis_id" not in row or row.get("_invalid"):
            log.warning(
                "PRIOR_THESES_SQLITE_INVALID path=%s row=%s error=malformed_row "
                "| hint=fix the malformed thesis-attempt row; it is ignored",
                db.path,
                line_no,
            )
            continue
        if strategy_family and row.get("strategy_family") != strategy_family:
            continue
        if row.get("config_changes"):
            prior.append(_prior_thesis_entry(row))
    return prior


def _iter_backtest_db_paths(root: Path, *, family: str | None = None) -> list[Path]:
    return iter_family_backtest_db_paths(root, family=family)


def _flatten_config_change_keys(config_changes: dict[str, Any]) -> set[str]:
    """Return comparable config key paths, including nested engine-change requests."""
    flattened: set[str] = set()

    def visit(prefix: str, value: Any) -> None:
        if isinstance(value, dict):
            if not value:
                flattened.add(prefix)
                return
            for child_key, child_value in value.items():
                if not isinstance(child_key, str):
                    child_key = str(child_key)
                child_path = f"{prefix}.{child_key}" if prefix else child_key
                visit(child_path, child_value)
            return
        flattened.add(prefix)

    for key, value in config_changes.items():
        if _is_overlap_ignored_key(str(key)):
            continue
        visit(str(key), value)
    return flattened


def config_key_overlap(
    proposed: dict[str, Any],
    prior_theses: list[dict[str, Any]],
    threshold: float = CONFIG_OVERLAP_THRESHOLD,
) -> tuple[bool, str]:
    """Check if proposed config_changes overlap too much with any prior thesis.

    Returns (is_duplicate, reason).
    """
    proposed_keys = _flatten_config_change_keys(proposed)
    if not proposed_keys:
        return False, ""

    for prior in prior_theses:
        prior_keys = _flatten_config_change_keys(prior["config_changes"])
        if not prior_keys:
            continue
        overlap = proposed_keys & prior_keys
        if not overlap:
            continue
        jaccard = len(overlap) / len(proposed_keys | prior_keys)
        if jaccard >= threshold:
            return True, (
                f"Config-key overlap {jaccard:.0%} with prior thesis "
                f"'{prior['thesis_id']}' (shared keys: {sorted(overlap)}). "
                f"This is a parameter variation, not a new mechanism. "
                f"Change DIFFERENT config keys to explore a new dimension."
            )
    return False, ""


def _detect_config_key_overlap(
    thesis: ResearchThesis,
    prior_theses: list[dict[str, Any]] | None,
) -> BehaviorSignal | None:
    if not prior_theses or not thesis.config_changes:
        return None
    is_dup, reason = config_key_overlap(thesis.config_changes, prior_theses)
    if not is_dup:
        return None
    return BehaviorSignal(
        code="config_validity_config_key_overlap_real",
        confidence=1.0,
        severity="block",
        summary=f"Config-key overlap: {reason}",
        evidence={"reason": reason},
        remediation=("Change different config keys to explore a new dimension",),
    )


# ---------------------------------------------------------------------------
# Core validation
# ---------------------------------------------------------------------------
