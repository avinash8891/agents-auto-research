"""Thesis validator — rejects vague, incomplete, or duplicate theses before compilation.

Bad conductor output should fail here, not waste a backtest run.

Three guardrails inspired by AlphaAgent (arxiv 2502.16789v2):
1. Config-key overlap detection — auto-reject theses that change the same config
   keys as a prior thesis (their AST subtree isomorphism equivalent).
2. Hypothesis-config alignment scoring — cheap LLM check that config_changes
   actually test the stated hypothesis (their c1/c2 consistency scoring).
3. Duplicate/runtime-compatibility rejection — fail loudly on legacy inheritance
   paths or reused runtime shapes instead of probing extra variants.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from autoresearch_logging import get_logger
from research_types import (
    CORE_MECHANISM_DIMENSIONS,
    EMERGENT_MECHANISM_DIMENSION,
    MECHANISM_DIMENSIONS,
    ResearchThesis,
)
from strategy_family import load_family

log = get_logger(__name__)

# Metrics the backtest engine always produces (no custom diagnostics needed)
BUILTIN_METRICS = {
    "profit_factor",
    "max_drawdown",
    "trade_count",
    "median_expectancy",
    "pct_profitable_windows",
    "avg_sharpe_across_windows",
}

# Minimum Jaccard overlap to trigger rejection
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
_MIN_EMERGENT_FIELD_CHARS = 40
_MIN_NOVEL_CONNECTION_CHARS = 40
# When falsification_or_alternative is set, it must be substantive — short text
# is decoration, not a real disconfirmer. The field itself remains optional;
# this rule only enforces quality when the agent does fill it in.
_MIN_FALSIFICATION_CHARS = 80
# Recovered from legacy prompt: minimum field counts and lengths.
_MIN_ALTERNATIVES_CONSIDERED = 2  # legacy §4
_MIN_EXPECTED_EFFECTS = 2  # legacy §17
_MIN_UNDEREXPLORED_DIMENSIONS = 2  # legacy §17
_MIN_ORTHOGONALITY_DEFENSE_CHARS = 40  # legacy §5
_MIN_SOURCE_CODE_VERIFICATION_CHARS = 40  # legacy §13.5
_MIN_EVIDENCE_STRENGTH_VALUES = ("direct", "proxy", "mixed", "speculative")
# Heuristic: causal_cluster should not look like a snake_case config key.
# Reject if the value contains common config-key suffixes/tokens, has no spaces,
# and is all lowercase + underscores.
_CONFIG_KEY_LIKE_TOKENS = (
    "_pct", "_threshold", "_atr", "_ratio", "_count", "_min", "_max",
    "_floor", "_cap", "_distance", "_window", "_seconds", "_minutes",
)
_EMERGENT_REQUIRED_FIELDS = (
    "why_existing_dimensions_do_not_fit",
    "mechanism_family_definition",
    "expected_reuse_across_future_theses",
)
_ALLOWED_BASE_CONFIG_PREFIXES = ("configs/",)
_PRIOR_BASE_LANGUAGE_PATTERNS = (
    r"\bcurrent\s+best\b",
    r"\bbest\s+(?:config|configuration|experiment|result|winner|runtime|trailing|pf)\b",
    r"\bprior\s+(?:config|configuration|experiment|result|winner|runtime|thesis)\b",
    r"\bkept\s+(?:config|configuration|experiment|result|winner|runtime|thesis)\b",
    r"\bwinning\s+(?:config|configuration|experiment|result|runtime|thesis)\b",
    r"\bpreserve\s+(?:the\s+)?(?:current\s+best|best|prior|kept|winning)\b",
    r"\b(?:build|builds|building)\s+on\s+(?:the\s+)?(?:current\s+best|best|prior|kept|winning)\b",
    r"\bcompound\s+(?:the\s+)?(?:current\s+best|best|prior|kept|winning)\b",
)


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
    if "config-key overlap" in msg:
        return "config_key_overlap_real"
    if "hypothesis-config misalignment" in msg:
        return "hypothesis_config_misalignment"
    if "do not construct" in msg or "points into runtime/" in msg:
        return "base_config_path_runtime_construction"
    if "legacy experiments/ inheritance" in msg or "must be under configs/" in msg:
        return "base_config_path_legacy_experiments"
    if "must point to a json or yaml" in msg or "must be a relative repo path" in msg:
        return "base_config_path_invalid"
    if "config_changes contains thesis metadata key" in msg:
        return "config_changes_metadata_leak"
    if "must be empty or the family baseline" in msg:
        return "base_config_path_inheritance_blocked"
    if "missing thesis_id" in msg:
        return "missing_thesis_id"
    if "missing hypothesis" in msg:
        return "missing_hypothesis"
    if "missing mechanism" in msg:
        return "missing_mechanism"
    if "requested_primitives" in msg:
        return "missing_requested_primitives"
    if "mechanism_dimension" in msg:
        return "mechanism_dimension_invalid"
    return "unspecified_validation_error"


MECHANISM_DIMENSION_ALIASES = {
    "trade_filtering": "signal_quality",
    "other": EMERGENT_MECHANISM_DIMENSION,
}


def _dimension_slug(text: str) -> str:
    words = re.findall(r"[a-z0-9]+", text.lower())
    return "_".join(words)


def _normalize_mechanism_dimension_name(dimension: Any) -> str:
    if not isinstance(dimension, str):
        return ""
    dimension_slug = _dimension_slug(dimension)
    return MECHANISM_DIMENSION_ALIASES.get(dimension_slug, dimension_slug)


def _validate_base_config_path(path: str) -> None:
    if not path:
        return
    normalized = path.replace("\\", "/")
    if normalized.startswith("/") or ".." in normalized.split("/"):
        raise ThesisValidationError(
            f"base_config_path '{path}' must be a relative repo path without '..'"
        )
    if not normalized.endswith((".json", ".yaml", ".yml")):
        raise ThesisValidationError(
            f"base_config_path '{path}' must point to a JSON or YAML config artifact"
        )
    is_allowed = normalized.startswith(_ALLOWED_BASE_CONFIG_PREFIXES)
    if not is_allowed:
        if normalized.startswith("runtime/"):
            raise ThesisValidationError(
                f"base_config_path '{path}' points into runtime/. Do not construct "
                f"paths from runtime artifacts; reference a checked-in config under "
                f"configs/ instead (for example, the family baseline config)."
            )
        raise ThesisValidationError(
            f"base_config_path '{path}' must be under configs/ only; "
            "legacy experiments/ inheritance paths are not allowed"
        )


def _requires_explicit_base_config(thesis: ResearchThesis) -> bool:
    text = " ".join(
        [
            thesis.hypothesis,
            thesis.mechanism,
            thesis.dimension_novelty,
            " ".join(thesis.evidence),
        ]
    ).lower()
    return any(re.search(pattern, text) for pattern in _PRIOR_BASE_LANGUAGE_PATTERNS)


def _family_baseline_path(thesis: ResearchThesis) -> str:
    try:
        return load_family(thesis.strategy_family).baseline_config_path
    except ValueError as exc:
        raise ThesisValidationError(str(exc)) from exc


def _prior_thesis_details(prior: dict[str, Any]) -> dict[str, Any]:
    details = prior.get("thesis_details")
    return details if isinstance(details, dict) else {}


def _known_emergent_dimension_names(prior_theses: list[dict[str, Any]] | None) -> set[str]:
    known: set[str] = set()
    if not prior_theses:
        return known
    for prior in prior_theses:
        if (
            _normalize_mechanism_dimension_name(prior.get("mechanism_dimension"))
            != EMERGENT_MECHANISM_DIMENSION
        ):
            continue
        details = _prior_thesis_details(prior)
        name = details.get("new_dimension_name") or prior.get("new_dimension_name")
        if isinstance(name, str) and name.strip():
            known.add(_dimension_slug(name))
    return known


def _prior_thesis_entry(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "thesis_id": row.get("thesis_id", "unknown"),
        "config_changes": row.get("config_changes") or {},
        "outcome": row.get("validator_status", "unknown"),
        "mechanism_dimension": row.get("mechanism_dimension", ""),
        "thesis_details": row.get("thesis_details", {}),
    }


# ---------------------------------------------------------------------------
# Stage 1 cross-thesis rules: theme cluster (B1), needs_code starvation (B3).
# ---------------------------------------------------------------------------

# B1: when 4 or more of the last 7 prior theses (plus the proposed one) share
# at least one keyword with the proposed theme, the agent has fixated on a
# single theme cluster. Forces dimension diversification.
B1_THEME_CLUSTER_THRESHOLD = 4
B1_THEME_CLUSTER_WINDOW = 7

# B3: 3 consecutive prior theses requiring code change with no completed run
# in between means the agent is queueing engine work without progress. Force
# a no-code thesis to break the starvation.
B3_NEEDS_CODE_STARVATION_LIMIT = 3


def _theme_keywords_from_prior(prior: dict[str, Any]) -> set[str]:
    details = _prior_thesis_details(prior)
    raw = details.get("theme_keywords") or prior.get("theme_keywords") or []
    if not isinstance(raw, list):
        return set()
    return {str(kw).strip() for kw in raw if str(kw).strip()}


def _prior_required_code_change(prior: dict[str, Any]) -> bool:
    details = _prior_thesis_details(prior)
    return bool(details.get("requires_code_change") or prior.get("requires_code_change"))


def _prior_was_run(prior: dict[str, Any]) -> bool:
    """Heuristic: any outcome other than 'needs_code' or 'rejected*' counts as ran."""
    outcome = str(prior.get("outcome") or "").lower()
    if not outcome:
        return False
    if outcome.startswith("rejected"):
        return False
    if outcome == "needs_code":
        return False
    if outcome == "stopped":
        return False
    return True


def _check_theme_cluster_fixation(
    thesis: ResearchThesis,
    prior_theses: list[dict[str, Any]],
) -> None:
    proposed_keywords = {kw.strip() for kw in thesis.theme_keywords if kw.strip()}
    if not proposed_keywords:
        return
    recent = prior_theses[-(B1_THEME_CLUSTER_WINDOW - 1) :]
    if not recent:
        return
    overlap_count = 1  # the new thesis itself
    overlapping_priors: list[str] = []
    for prior in recent:
        prior_kw = _theme_keywords_from_prior(prior)
        if prior_kw & proposed_keywords:
            overlap_count += 1
            overlapping_priors.append(str(prior.get("thesis_id") or "?"))
    if overlap_count >= B1_THEME_CLUSTER_THRESHOLD:
        raise ThesisValidationError(
            f"Theme-cluster fixation: {overlap_count} of last "
            f"{B1_THEME_CLUSTER_WINDOW} theses share keywords {sorted(proposed_keywords)} "
            f"(overlapping priors: {overlapping_priors}). Propose from a different "
            f"mechanism dimension, or justify novelty in dimension_novelty."
        )


# B2 direction whipsaw: maps lowercase substrings → opposing-direction tag.
_B2_DIRECTION_TIGHTEN_TOKENS = ("tighten", "narrow", "min_", "floor", "shrink")
_B2_DIRECTION_WIDEN_TOKENS = ("widen", "loosen", "max_", "cap_removal", "remove_cap", "expand")


def _b2_direction_of(text: str) -> str | None:
    """Return 'tighten', 'widen', or None for the dominant direction in `text`."""
    lowered = text.lower()
    has_tighten = any(tok in lowered for tok in _B2_DIRECTION_TIGHTEN_TOKENS)
    has_widen = any(tok in lowered for tok in _B2_DIRECTION_WIDEN_TOKENS)
    if has_tighten and not has_widen:
        return "tighten"
    if has_widen and not has_tighten:
        return "widen"
    return None  # ambiguous or none


def _check_direction_whipsaw(
    thesis: ResearchThesis,
    prior_theses: list[dict[str, Any]],
) -> None:
    """Reject if the thesis flips the direction of a lever already tested by a prior
    thesis on the same theme, unless prior_lever_outcomes cites that prior.
    """
    proposed_dir = _b2_direction_of(thesis.thesis_id + " " + thesis.hypothesis)
    if proposed_dir is None:
        return
    proposed_kw = {kw.strip() for kw in thesis.theme_keywords if kw.strip()}
    if not proposed_kw:
        return
    cited_prior_ids = {p.prior_thesis_id for p in thesis.prior_lever_outcomes}

    opposing = "widen" if proposed_dir == "tighten" else "tighten"
    for prior in prior_theses:
        prior_kw = _theme_keywords_from_prior(prior)
        if not (prior_kw & proposed_kw):
            continue
        prior_dir = _b2_direction_of(str(prior.get("thesis_id") or ""))
        if prior_dir != opposing:
            continue
        prior_id = str(prior.get("thesis_id") or "")
        if prior_id in cited_prior_ids:
            continue
        raise ThesisValidationError(
            f"Direction whipsaw: prior thesis '{prior_id}' tested the {opposing} "
            f"direction on lever theme {sorted(proposed_kw)}, and this thesis "
            f"flips to {proposed_dir} without acknowledgment. Cite '{prior_id}' "
            f"in prior_lever_outcomes (with direction_then, outcome, and why_retry) "
            f"or propose from a different mechanism dimension."
        )


def _check_alternatives_considered(thesis: ResearchThesis) -> None:
    """L3 (legacy §4): require >=2 alternative mechanisms considered."""
    if len(thesis.alternatives_considered) < _MIN_ALTERNATIVES_CONSIDERED:
        raise ThesisValidationError(
            f"alternatives_considered must contain at least "
            f"{_MIN_ALTERNATIVES_CONSIDERED} entries (got {len(thesis.alternatives_considered)}). "
            f"Generate >=2 candidate mechanism directions and explain why this one wins."
        )


def _check_expected_effects_count(thesis: ResearchThesis) -> None:
    """L3 (legacy §17): require >=2 expected_effects predictions."""
    if len(thesis.expected_effects) < _MIN_EXPECTED_EFFECTS:
        raise ThesisValidationError(
            f"expected_effects must contain at least {_MIN_EXPECTED_EFFECTS} "
            f"measurable predictions (got {len(thesis.expected_effects)}). "
            f"A single metric prediction is insufficient to validate a mechanism."
        )


def _check_evidence_strength(thesis: ResearchThesis) -> None:
    """L3 (legacy §17): evidence_strength must be a valid value, not empty."""
    value = thesis.evidence_strength
    if not value:
        raise ThesisValidationError(
            f"evidence_strength is required. "
            f"Set one of: {sorted(_MIN_EVIDENCE_STRENGTH_VALUES)}."
        )
    # Pydantic Literal already restricts; this catches the empty-string default.


def _check_causal_cluster_not_config_key_like(thesis: ResearchThesis) -> None:
    """L3 (legacy §17): causal_cluster must be a causal family name, not a
    config-key identifier."""
    cluster = thesis.causal_cluster.strip()
    if not cluster:
        return  # the upstream "required when prior_theses exist" rule handles emptiness
    looks_like_key = (
        " " not in cluster
        and cluster == cluster.lower()
        and any(token in cluster for token in _CONFIG_KEY_LIKE_TOKENS)
    )
    if looks_like_key:
        raise ThesisValidationError(
            f"causal_cluster '{cluster}' looks like a config key name. "
            f"Use a human-phrased causal-family name (e.g. 'opening-session "
            f"adverse selection'), not a config identifier."
        )


def _check_underexplored_dimensions_count(thesis: ResearchThesis) -> None:
    """L3 (legacy §17): >=2 underexplored dimensions when prior_theses exist."""
    if len(thesis.underexplored_dimensions_considered) < _MIN_UNDEREXPLORED_DIMENSIONS:
        raise ThesisValidationError(
            f"underexplored_dimensions_considered must contain at least "
            f"{_MIN_UNDEREXPLORED_DIMENSIONS} entries (got "
            f"{len(thesis.underexplored_dimensions_considered)}). Show what you "
            f"chose not to pursue and why this dimension still wins."
        )


def _check_closest_prior_theses_present(thesis: ResearchThesis) -> None:
    """L3 (legacy §5): closest_prior_theses_considered required when prior exists."""
    if not thesis.closest_prior_theses_considered:
        raise ThesisValidationError(
            "closest_prior_theses_considered is empty. When prior theses exist, "
            "name the prior theses you explicitly compared against before proposing."
        )


def _check_orthogonality_defense_quality(thesis: ResearchThesis) -> None:
    """L3 (legacy §5): orthogonality_defense >=40 chars when prior exists."""
    text = (thesis.orthogonality_defense or "").strip()
    if len(text) < _MIN_ORTHOGONALITY_DEFENSE_CHARS:
        raise ThesisValidationError(
            f"orthogonality_defense must be at least "
            f"{_MIN_ORTHOGONALITY_DEFENSE_CHARS} characters (got {len(text)}). "
            f"Explain why this thesis is orthogonal rather than merely adjacent "
            f"to the closest prior theses."
        )


def _check_thesis_id_not_repeated(
    thesis: ResearchThesis, prior_theses: list[dict[str, Any]]
) -> None:
    """L3 (legacy §18): the proposed thesis_id must not duplicate a prior one."""
    prior_ids = {str(p.get("thesis_id") or "") for p in prior_theses}
    if thesis.thesis_id in prior_ids:
        raise ThesisValidationError(
            f"thesis_id '{thesis.thesis_id}' has already been proposed in a prior "
            f"round. Each thesis must have a unique thesis_id (do not repeat or "
            f"resubmit prior names)."
        )


# Numeric tuning detector: same key, ratio within [1/_NEIGHBORING_RATIO,
# _NEIGHBORING_RATIO] is treated as a parameter tuning nudge.
_NEIGHBORING_RATIO = 2.0


def _is_numeric_value(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _check_neighboring_threshold(
    thesis: ResearchThesis, prior_theses: list[dict[str, Any]]
) -> None:
    """L5 (legacy §2 + §12): reject when a numeric config key has been changed
    by a prior thesis and the new value is within a narrow band of the prior's.

    "Narrow band" = within 2x of the prior value (ratio in [0.5, 2.0]).
    Different keys, non-numeric values, or large deltas are not flagged here.
    """
    new_changes = thesis.config_changes or {}
    if not new_changes:
        return
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
            # Both numeric. Compute ratio (handle zero defensively).
            new_f = float(new_val)
            prior_f = float(prior_val)
            if new_f == prior_f:
                # Identical value — Jaccard rule will catch broader overlap;
                # not flagged by the threshold detector specifically.
                continue
            if new_f == 0 or prior_f == 0:
                # One side is zero, ratio undefined; treat as significant change.
                continue
            ratio = new_f / prior_f
            if 1.0 / _NEIGHBORING_RATIO <= ratio <= _NEIGHBORING_RATIO:
                raise ThesisValidationError(
                    f"Neighboring threshold: config key '{key}' was set to "
                    f"{prior_val} by prior thesis '{prior.get('thesis_id', '?')}' "
                    f"and this thesis sets it to {new_val} (ratio "
                    f"{ratio:.2f}x, within {_NEIGHBORING_RATIO}x). This is "
                    f"parameter tuning, not a new mechanism. Either justify a "
                    f"structural boundary at this value or test a materially "
                    f"different lever."
                )


def _check_mechanism_judge_verdict(thesis: ResearchThesis) -> None:
    """L11 (legacy §17): LLM-as-judge for hypothesis describing mechanism vs
    param-value dressed in mechanistic prose. Fails open when no classifier
    is installed (development environments without OpenAI access).
    """
    from mechanism_judge import classify_hypothesis

    verdict = classify_hypothesis(thesis.hypothesis, thesis.mechanism)
    if verdict == "param_value_in_prose":
        raise ThesisValidationError(
            "Mechanism judge classified the hypothesis as 'param_value_in_prose' "
            "rather than a real mechanism. The hypothesis should describe what "
            "happens in the market and why this change should produce the "
            "expected effect — not which parameter value is being tried."
        )


def _check_source_code_verification(thesis: ResearchThesis) -> None:
    """L8 (legacy §13.5): require a non-trivial source_code_verification string.

    The agent must cite a specific source file/function whose behavior corroborates
    the mechanism. Empty or short text is decoration, not verification.
    """
    text = (thesis.source_code_verification or "").strip()
    if len(text) < _MIN_SOURCE_CODE_VERIFICATION_CHARS:
        raise ThesisValidationError(
            f"source_code_verification must be at least "
            f"{_MIN_SOURCE_CODE_VERIFICATION_CHARS} characters citing the strategy "
            f"source file/function whose behavior corroborates the mechanism "
            f"(got {len(text)} characters)."
        )


def _check_evidence_citations_coverage(thesis: ResearchThesis) -> None:
    """L4 (legacy §17): evidence_citations must include >=1 web_search AND
    >=1 analyst source. Recovers the legacy "evidence: ≥1 web + ≥1 analyst" rule.
    """
    if not thesis.evidence_citations:
        raise ThesisValidationError(
            "evidence_citations is empty. Provide at least one web_search citation "
            "and one analyst citation supporting the mechanism."
        )
    sources = {c.source for c in thesis.evidence_citations}
    if "web_search" not in sources:
        raise ThesisValidationError(
            "evidence_citations must include at least one entry with "
            "source='web_search'. The mechanism needs external corroboration."
        )
    if "analyst" not in sources:
        raise ThesisValidationError(
            "evidence_citations must include at least one entry with "
            "source='analyst'. The mechanism must be grounded in trade-level evidence."
        )


def _check_qualitative_disqualifier_present(thesis: ResearchThesis) -> None:
    """B5: at least one Disqualifier must have kind='mechanism_evidence'.

    Pure metric-threshold disqualifiers ("PF must improve by 5%") are pass/fail
    criteria, not Popperian disconfirmers. Force one to be qualitative.
    """
    if not thesis.disqualifiers:
        return  # absence is handled by the earlier "no disqualifiers" rule
    if any(d.kind == "mechanism_evidence" for d in thesis.disqualifiers):
        return
    raise ThesisValidationError(
        "Disqualifiers list contains only metric_threshold entries. "
        "At least one disqualifier must reference observable mechanism evidence "
        "(set kind='mechanism_evidence'), describing what data pattern would "
        "falsify the mechanism — independent of whether metrics improve."
    )


def _check_needs_code_starvation(
    thesis: ResearchThesis,
    prior_theses: list[dict[str, Any]],
) -> None:
    if not thesis.requires_code_change:
        return
    # Walk priors most-recent-first; count consecutive needs_code + requires_code_change.
    streak = 0
    for prior in reversed(prior_theses):
        if _prior_was_run(prior):
            break
        if _prior_required_code_change(prior):
            streak += 1
        else:
            # Non-code prior breaks the streak even if it didn't run.
            break
        if streak >= B3_NEEDS_CODE_STARVATION_LIMIT:
            break
    if streak >= B3_NEEDS_CODE_STARVATION_LIMIT:
        raise ThesisValidationError(
            f"needs_code starvation: {streak} consecutive prior theses required "
            f"engine changes without running. Propose a non-code thesis to break "
            f"the queue (set requires_code_change=false and operate on existing config keys)."
        )


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
    if "profitable window" in lowered:
        return "pct_profitable_windows"
    if "sharpe" in lowered:
        return "avg_sharpe_across_windows"
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
    dimension = normalized.get("mechanism_dimension")
    if isinstance(dimension, str):
        normalized["mechanism_dimension"] = _normalize_mechanism_dimension_name(dimension)
    normalized["expected_effects"] = [
        _normalize_expected_effect(effect) for effect in normalized.get("expected_effects", [])
    ]
    normalized["disqualifiers"] = [
        _normalize_disqualifier(disqualifier)
        for disqualifier in normalized.get("disqualifiers", [])
    ]
    return normalized


# ---------------------------------------------------------------------------
# Guardrail 1: Config-key overlap detection
# ---------------------------------------------------------------------------


def load_prior_theses(root: Path, db: Any | None = None) -> list[dict[str, Any]]:
    """Load all previously proposed theses from canonical persistence."""
    prior: list[dict[str, Any]] = []
    if db is None:
        from backtest_run_db import BacktestRunDB

        for db_path in sorted(root.glob("*_backtest_runs.db")):
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
                config_changes = row.get("config_changes") or {}
                if config_changes:
                    prior.append(_prior_thesis_entry(row))
                    continue
                details = _prior_thesis_details(row)
                if _normalize_mechanism_dimension_name(
                    row.get("mechanism_dimension")
                ) == EMERGENT_MECHANISM_DIMENSION and details.get("new_dimension_name"):
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
        config_changes = row.get("config_changes") or {}
        if config_changes:
            prior.append(_prior_thesis_entry(row))
            continue
        details = _prior_thesis_details(row)
        if _normalize_mechanism_dimension_name(
            row.get("mechanism_dimension")
        ) == EMERGENT_MECHANISM_DIMENSION and details.get("new_dimension_name"):
            prior.append(_prior_thesis_entry(row))
    return prior


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


# ---------------------------------------------------------------------------
# Guardrail 2: Hypothesis-config alignment scoring
# ---------------------------------------------------------------------------


def check_hypothesis_alignment(
    hypothesis: str,
    mechanism: str,
    config_changes: dict[str, Any],
) -> tuple[float, str]:
    """Score whether config_changes actually test the stated hypothesis.

    Uses a heuristic keyword approach (no LLM call needed for v1).
    Returns (score 0-1, explanation).

    Score meanings:
      1.0 = config keys directly reference concepts in hypothesis/mechanism
      0.5 = partial alignment
      0.0 = no connection between story and implementation
    """
    import re

    if not config_changes:
        return 1.0, "No config changes (code change thesis)"

    hyp_lower = (hypothesis + " " + mechanism).lower()
    config_keys = set(config_changes.keys())

    # Map config keys to concept patterns.
    # Each pattern is a regex searched against the lowercased hypothesis+mechanism.
    # Use specific multi-word phrases to avoid false positives.
    KEY_CONCEPTS: dict[str, list[str]] = {
        "entry_cutoff_time": [
            r"entry.{0,10}tim",
            r"cutoff",
            r"time window",
            r"entry window",
            r"morning",
            r"first.{0,5}\d+.{0,5}min",
            r"open.{0,10}bar",
        ],
        "max_trades_per_day": [
            r"max.{0,5}trade",
            r"one.{0,5}trade.{0,10}day",
            r"single.{0,5}trade.{0,10}day",
            r"first.{0,10}trade",
            r"first.{0,10}executed.{0,10}trade",
            r"first.{0,10}setup",
            r"only.{0,10}first",
            r"position limit",
            r"portfolio",
            r"daily.{0,5}cap",
            r"trade.{0,5}capacity",
            r"number of trades",
        ],
        "rr_ratio": [r"risk.{0,3}reward", r"target.{0,5}ratio", r"r.?r.?ratio", r"profit target"],
        "trail_after_r": [r"trail", r"let.{0,10}run", r"continuation", r"runner"],
        "max_hold_bars": [
            r"hold.{0,5}(duration|bar|time|period)",
            r"time.?stop",
            r"decay",
            r"dissipat",
            r"max.{0,3}hold",
        ],
        "gap_exclude": [r"gap", r"overnight.{0,10}(gap|move)"],
        "gap_exclude_pct": [r"gap", r"overnight.{0,10}(gap|move)"],
        "gap_filter": [r"gap", r"overnight"],
        "gap_pct": [r"gap"],
        "min_stop_distance_pct": [
            r"stop.{0,5}(distance|loss|size)",
            r"noise.{0,5}(stop|exit)",
            r"tight.{0,5}stop",
            r"slippage",
        ],
        "max_stop_distance_pct": [
            r"stop.{0,5}(distance|loss|size)",
            r"extreme.{0,5}(move|candle)",
            r"wide.{0,5}stop",
            r"candle.{0,5}size",
        ],
        "use_range_shift": [r"range.{0,5}shift", r"lookback", r"adaptive", r"context.{0,5}window"],
        "range_shift_lookback": [r"range.{0,5}shift", r"lookback", r"adaptive"],
        "timeframe_short": [r"timeframe", r"bar.{0,3}size", r"resolution", r"5.?min"],
        "timeframe_long": [r"timeframe", r"bar.{0,3}size", r"resolution", r"15.?min"],
        "direction_bias": [r"direction", r"long.only", r"short.only", r"bias"],
    }

    aligned_keys = 0
    misaligned_keys: list[str] = []

    for key in config_keys:
        concepts = KEY_CONCEPTS.get(key, [])
        if not concepts:
            # Unknown key — can't check, give benefit of doubt
            aligned_keys += 1
            continue
        matched = any(re.search(pattern, hyp_lower) for pattern in concepts)
        if matched:
            aligned_keys += 1
        else:
            misaligned_keys.append(key)

    score = aligned_keys / len(config_keys) if config_keys else 1.0

    if misaligned_keys:
        explanation = (
            f"Config keys {misaligned_keys} don't relate to the hypothesis. "
            f"Hypothesis mentions: {hypothesis[:100]}... "
            f"but config changes touch {sorted(config_keys)}."
        )
    else:
        explanation = "Config changes align with hypothesis."

    return score, explanation


ALIGNMENT_THRESHOLD = 0.4  # reject if less than 40% of keys align
_MIN_NOVELTY_EXPLANATION_CHARS = 30
_NUMERIC_VARIANT_BOUNDS: dict[str, tuple[float | None, float | None]] = {
    "max_trades_per_day": (1, 20),
    # Upper bounds are strategy-specific and are enforced when variants are queued.
    "max_hold_bars": (1, None),
}


# ---------------------------------------------------------------------------
# Guardrail 3: Multi-variant probing
# ---------------------------------------------------------------------------


def generate_variants(
    config_changes: dict[str, Any],
    baseline: dict[str, Any],
) -> list[dict[str, Any]]:
    """Generate conservative/proposed/aggressive variants for continuous params.

    Only applies when thesis changes 1-2 numeric params.
    Returns list of variant config_changes dicts (always includes the original).
    """
    numeric_changes = {
        k: v
        for k, v in config_changes.items()
        if isinstance(v, (int, float)) and not isinstance(v, bool)
    }
    non_numeric_changes = {k: v for k, v in config_changes.items() if k not in numeric_changes}

    # Only probe when there are 1-2 numeric changes and no complex non-numeric ones
    if not numeric_changes or len(numeric_changes) > 2 or non_numeric_changes:
        return [config_changes]

    variants = []
    for factor, label in [(0.5, "conservative"), (1.0, "proposed"), (2.0, "aggressive")]:
        variant = dict(config_changes)
        for key, proposed_val in numeric_changes.items():
            baseline_val = baseline.get(key)
            if baseline_val is None or not isinstance(baseline_val, (int, float)):
                continue
            if isinstance(baseline_val, bool):
                continue
            delta = proposed_val - baseline_val
            if delta == 0:
                continue
            new_val = baseline_val + delta * factor
            lower, upper = _NUMERIC_VARIANT_BOUNDS.get(key, (None, None))
            if lower is not None:
                new_val = max(new_val, lower)
            if upper is not None:
                new_val = min(new_val, upper)
            # Preserve int type if both baseline and proposed are int
            if isinstance(baseline_val, int) and isinstance(proposed_val, int):
                new_val = int(round(new_val))
            variant[key] = new_val
        variant["_variant_label"] = label
        variant["_variant_factor"] = factor
        variants.append(variant)

    # Deduplicate (conservative might equal proposed for small deltas)
    seen: set[str] = set()
    unique: list[dict[str, Any]] = []
    for v in variants:
        # Hash without metadata keys
        hashable = {k: v2 for k, v2 in v.items() if not k.startswith("_")}
        key = json.dumps(hashable, sort_keys=True)
        if key not in seen:
            seen.add(key)
            unique.append(v)

    return unique


# ---------------------------------------------------------------------------
# Core validation
# ---------------------------------------------------------------------------


def validate_research_thesis(
    thesis: ResearchThesis,
    prior_theses: list[dict[str, Any]] | None = None,
) -> ResearchThesis:
    """Validate a research thesis. Raises ThesisValidationError if invalid.

    Args:
        thesis: The thesis to validate.
        prior_theses: Previously proposed theses (from load_prior_theses).
            If provided, config-key overlap detection is enabled.
    """

    if not thesis.thesis_id.strip():
        raise ThesisValidationError("Missing thesis_id")

    if not thesis.hypothesis.strip():
        raise ThesisValidationError("Missing hypothesis")

    if not thesis.mechanism.strip():
        raise ThesisValidationError("Missing mechanism")

    if not thesis.mechanism_dimension.strip():
        raise ThesisValidationError(
            "Missing mechanism_dimension. Every thesis must declare which "
            "dimension it explores: " + ", ".join(sorted(MECHANISM_DIMENSIONS))
        )
    known_dimensions = MECHANISM_DIMENSIONS | _known_emergent_dimension_names(prior_theses)
    if thesis.mechanism_dimension not in known_dimensions:
        raise ThesisValidationError(
            f"Invalid mechanism_dimension '{thesis.mechanism_dimension}'. "
            f"Must be one of: {sorted(known_dimensions)}"
        )
    if thesis.mechanism_dimension == EMERGENT_MECHANISM_DIMENSION:
        new_dimension_name = _dimension_slug(thesis.new_dimension_name)
        if not new_dimension_name:
            raise ThesisValidationError(
                "new_dimension_name is required when mechanism_dimension is emergent"
            )
        if new_dimension_name in CORE_MECHANISM_DIMENSIONS:
            raise ThesisValidationError(
                f"new_dimension_name '{thesis.new_dimension_name}' duplicates a core "
                "mechanism dimension; use the core dimension instead"
            )
        for field in _EMERGENT_REQUIRED_FIELDS:
            value = getattr(thesis, field)
            if len(value.strip()) < _MIN_EMERGENT_FIELD_CHARS:
                raise ThesisValidationError(
                    f"{field} must be at least {_MIN_EMERGENT_FIELD_CHARS} characters "
                    "when mechanism_dimension is emergent"
                )
    if not thesis.dimension_novelty.strip():
        raise ThesisValidationError(
            "dimension_novelty is empty. "
            "Explain why this is not a parameter variation of a prior thesis."
        )
    if prior_theses:
        if not thesis.causal_cluster.strip():
            raise ThesisValidationError(
                "causal_cluster is required when prior theses exist. "
                "Name the causal family this thesis belongs to."
            )
        if not thesis.underexplored_dimensions_considered:
            raise ThesisValidationError(
                "underexplored_dimensions_considered is required when prior theses exist. "
                "Compare at least two underexplored dimensions before proposing."
            )
        if thesis.dominant_cluster_overlap == "high" and (
            len(thesis.novel_connection.strip()) < _MIN_NOVEL_CONNECTION_CHARS
        ):
            raise ThesisValidationError(
                "novel_connection must explain why a high-overlap thesis is "
                "materially new instead of another variation of the dominant cluster."
            )

    if not thesis.config_changes and not thesis.requires_code_change:
        raise ThesisValidationError(
            "Thesis has neither config_changes nor requires_code_change=true"
        )
    if thesis.requires_code_change and not thesis.requested_primitives:
        raise ThesisValidationError("requires_code_change theses must declare requested_primitives")
    _validate_base_config_path(thesis.base_config_path)
    if thesis.base_contract_id:
        raise ThesisValidationError(
            "base_contract_id is not allowed; research theses must start from the family "
            "baseline instead of inheriting a prior winner."
        )
    baseline_path = _family_baseline_path(thesis)
    if thesis.base_config_path and thesis.base_config_path != baseline_path:
        raise ThesisValidationError(
            f"base_config_path must be empty or the family baseline '{baseline_path}'; "
            "prior/winning config inheritance is not allowed."
        )
    if _requires_explicit_base_config(thesis):
        raise ThesisValidationError(
            "Thesis references current-best/prior-winner inheritance. "
            "That exploitation path is disabled; start from the family baseline."
        )
    for key in sorted(CONFIG_CHANGES_METADATA_KEYS & set(thesis.config_changes)):
        raise ThesisValidationError(
            f"config_changes contains thesis metadata key '{key}'. "
            f"Set top-level {key}=true instead of putting it in runtime config changes."
        )

    if not thesis.expected_effects:
        raise ThesisValidationError(
            "Thesis has no expected_effects — cannot evaluate without predictions"
        )

    # If falsification_or_alternative is set, require it be substantive. Short
    # text reads as decoration, not a real disconfirmer. The field is optional
    # at the schema level but must be quality-controlled when present.
    falsification_text = (thesis.falsification_or_alternative or "").strip()
    if falsification_text and len(falsification_text) < _MIN_FALSIFICATION_CHARS:
        raise ThesisValidationError(
            f"falsification_or_alternative must be at least "
            f"{_MIN_FALSIFICATION_CHARS} characters to count as a real disconfirmer; "
            f"got {len(falsification_text)} characters."
        )

    if not thesis.disqualifiers:
        raise ThesisValidationError(
            "Thesis has no disqualifiers — need at least one falsification condition"
        )

    # Every expected_effect metric must be either a builtin or explicitly
    # listed in required_diagnostics (so the analyst knows to compute it)
    for effect in thesis.expected_effects:
        if effect.metric not in BUILTIN_METRICS:
            if effect.metric not in thesis.required_diagnostics:
                raise ThesisValidationError(
                    f"Expected effect metric '{effect.metric}' is not a builtin metric "
                    f"and is not listed in required_diagnostics"
                )

    if prior_theses and thesis.config_changes:
        is_dup, reason = config_key_overlap(thesis.config_changes, prior_theses)
        if is_dup:
            raise ThesisValidationError(f"Config-key overlap: {reason}")

    # B1 + B3 + B2: cross-thesis pattern rules. Run after structural checks,
    # before the alignment scoring gate.
    if prior_theses:
        _check_theme_cluster_fixation(thesis, prior_theses)
        _check_needs_code_starvation(thesis, prior_theses)
        _check_direction_whipsaw(thesis, prior_theses)

    # B5: qualitative disqualifier requirement.
    _check_qualitative_disqualifier_present(thesis)

    # L3: legacy-prompt-recovered field-count and content rules.
    _check_alternatives_considered(thesis)
    _check_expected_effects_count(thesis)
    _check_evidence_strength(thesis)
    _check_causal_cluster_not_config_key_like(thesis)
    _check_evidence_citations_coverage(thesis)
    _check_source_code_verification(thesis)
    _check_mechanism_judge_verdict(thesis)
    if prior_theses:
        _check_underexplored_dimensions_count(thesis)
        _check_closest_prior_theses_present(thesis)
        _check_orthogonality_defense_quality(thesis)
        _check_thesis_id_not_repeated(thesis, prior_theses)
        _check_neighboring_threshold(thesis, prior_theses)

    if prior_theses and thesis.mechanism_dimension:
        same_dim = [
            p for p in prior_theses if p.get("mechanism_dimension") == thesis.mechanism_dimension
        ]
        if same_dim:
            prior_ids = [p["thesis_id"] for p in same_dim]
            # Not a hard reject — the prompt already told the conductor to
            # pick a different dimension. But if dimension_novelty doesn't
            # convincingly explain why this is different, warn loudly.
            if len(thesis.dimension_novelty) < _MIN_NOVELTY_EXPLANATION_CHARS:
                raise ThesisValidationError(
                    f"Dimension '{thesis.mechanism_dimension}' was already explored "
                    f"by {prior_ids}. dimension_novelty must explain (>30 chars) "
                    f"what fundamentally new mechanism this tests within that dimension."
                )

    if thesis.config_changes:
        score, explanation = check_hypothesis_alignment(
            thesis.hypothesis,
            thesis.mechanism,
            thesis.config_changes,
        )
        if score < ALIGNMENT_THRESHOLD:
            raise ThesisValidationError(
                f"Hypothesis-config misalignment (score={score:.2f}): {explanation}"
            )

    return thesis


def validate_thesis_dict(
    raw: dict,
    prior_theses: list[dict[str, Any]] | None = None,
) -> ResearchThesis:
    """Parse a raw dict into ResearchThesis and validate it.

    Use this when the conductor output is a plain dict.
    Raises ThesisValidationError or pydantic ValidationError.
    """
    thesis = ResearchThesis.model_validate(normalize_thesis_payload(raw))
    return validate_research_thesis(thesis, prior_theses=prior_theses)


# ---------------------------------------------------------------------------
# Two-stage validation
# ---------------------------------------------------------------------------
#
# Stage 1 runs on the raw thesis BEFORE compile. Catches structural,
# semantic, and historical-pattern violations that don't need the compiled
# config. Most rules live here.
#
# Stage 2 runs on the compiled BacktestContract. Catches rules that require
# the canonical resolved config (e.g. that all required diagnostics are
# actually wired in the compiled output). Currently a no-op; rules added
# as Stage 2 evolves.


def validate_stage_1(
    thesis: ResearchThesis,
    prior_theses: list[dict[str, Any]] | None = None,
) -> ResearchThesis:
    """Stage 1: pre-compile validator. Alias for `validate_research_thesis`."""
    return validate_research_thesis(thesis, prior_theses=prior_theses)


def validate_stage_2(contract: Any) -> Any:
    """Stage 2: post-compile validator. Currently a no-op.

    Rules that need the resolved/normalized config (e.g. required-diagnostics
    presence in the compiled output) belong here. Returns the contract
    unchanged, or raises ThesisValidationError on rule violation.
    """
    return contract
