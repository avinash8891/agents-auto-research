"""Thesis validator — rejects vague, incomplete, or duplicate theses before compilation.

Bad conductor output should fail here, not waste a backtest run.

Three guardrails inspired by AlphaAgent (arxiv 2502.16789v2):
1. Config-key overlap detection — auto-reject theses that change the same config
   keys as a prior thesis (their AST subtree isomorphism equivalent).
2. Hypothesis-config alignment scoring — cheap LLM check that config_changes
   actually test the stated hypothesis (their c1/c2 consistency scoring).
3. Duplicate/runtime-compatibility rejection — fail loudly on legacy inheritance
   paths or reused runtime shapes instead of probing extra variants.

──────────────────────────────────────────────────────────────────────────────
Contract-extraction rule (for maintainers adding or refactoring checks)
──────────────────────────────────────────────────────────────────────────────

Multi-check contracts (e.g. mechanism_dimension, emergent path,
underexplored_dimensions, thesis_specifies_change, expected_effects) live in
dedicated private `_validate_<contract>(...)` helpers placed adjacent to the
`_validate_structural` orchestrator. The orchestrator stays a thin sequence
of inline single-check raises + helper calls.

When deciding whether to bundle two related checks into one helper, apply
this test:

    Do these checks have to run at different points in the
    overall fail-fast sequence?

If YES → split into separate helpers, called from the orchestrator at their
respective positions. Example: `_validate_expected_effects_present` (early,
presence-tier priority) and `_validate_expected_effects_metrics_backed`
(late, after disqualifiers presence check). Bundling them regresses the
global fail-fast order — pinned by the regression test
`test_disqualifiers_fire_before_expected_effects_metric_unbacked`.

If NO → one helper owning both checks is fine, with structured `evidence`
when failure modes are independent. Example: `_validate_emergent_dimension`
collects all emergent-path failures into one rejection.

Single-check contracts (thesis_id presence, hypothesis presence, etc.) stay
inline in the orchestrator — extracting them into one-line helpers is noise.
"""

from __future__ import annotations

import ast
import json
import re
from collections.abc import Callable
from functools import lru_cache
from pathlib import Path
from typing import Any, Final

from autoresearch_logging import get_logger
from autoresearch_runtime_paths import iter_family_backtest_db_paths
from behavior_signals import BehaviorSignal
from behavior_signals import decide as _policy_decide
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
# A mechanism_evidence disqualifier's `condition` must be substantive — short
# strings like "x" or "yes" satisfy the kind=mechanism_evidence check trivially
# without describing any observable data pattern. 40 chars forces real content.
_MIN_MECHANISM_EVIDENCE_CONDITION_CHARS: Final[int] = 40
# When falsification_or_alternative is set, it must be substantive — short text
# is decoration, not a real disconfirmer. The field itself remains optional;
# this rule only enforces quality when the agent does fill it in.
_MIN_FALSIFICATION_CHARS = 80
_MIN_ALTERNATIVES_CONSIDERED = 2
_MIN_EXPECTED_EFFECTS = 2
_MIN_EFFECT_RATIONALE_CHARS = 20
_EVIDENCE_CONTEXT_TRADES: Final[str] = "trades"
_EVIDENCE_CONTEXT_NO_TRADES: Final[str] = "no_trades"
_EVIDENCE_CONTEXT_COLD_START: Final[str] = "cold_start"
_EVIDENCE_SOURCES_BY_CONTEXT: Final[dict[str, frozenset[str]]] = {
    _EVIDENCE_CONTEXT_TRADES: frozenset({"web_search", "analyst"}),
    _EVIDENCE_CONTEXT_NO_TRADES: frozenset({"web_search", "round_result"}),
    _EVIDENCE_CONTEXT_COLD_START: frozenset({"web_search"}),
}
_EMERGENT_REQUIRED_FIELDS = (
    "why_existing_dimensions_do_not_fit",
    "mechanism_family_definition",
    "expected_reuse_across_future_theses",
)
_ALLOWED_BASE_CONFIG_PREFIXES = ("configs/",)

# Removed gate: prior-winner inheritance language regex.
# See docs/superpowers/plans/2026-05-27-validator-gate-consolidation.md
# ("Removed gates" section) for the rationale.


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


# Renames from the 2026-05-27 validator consolidation. Listed explicitly so
# downstream consumers (drift checker, analytics, prompt-rule mappings) can
# detect the rename rather than silently encountering an "unknown" code.
RETIRED_REJECTION_CODES: Final[dict[str, str]] = {
    "structural_missing_dimension_novelty": "structural_dimension_novelty_invalid",
    "thesis_quality_dimension_novelty_too_short": "structural_dimension_novelty_invalid",
    "structural_missing_falsification": "structural_falsification_invalid",
    "structural_falsification_too_short": "structural_falsification_invalid",
    "structural_missing_new_dimension_name": "structural_emergent_thesis_malformed",
    "structural_new_dimension_name_duplicates_core": "structural_emergent_thesis_malformed",
    "structural_emergent_field_too_short": "structural_emergent_thesis_malformed",
    "structural_missing_underexplored_dimensions": "structural_underexplored_dimensions_invalid",
    "structural_underexplored_dimensions_includes_chosen": "structural_underexplored_dimensions_invalid",
    "config_validity_base_config_path_legacy_experiments": "config_validity_base_config_path_inheritance_blocked",
    "thesis_quality_thesis_id_repeated": "structural_thesis_id_repeated",
}


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
    # Each branch below maps EITHER the current message OR a legacy message
    # from a renamed gate to the canonical rejection_code. The retired
    # codes themselves are listed in RETIRED_REJECTION_CODES above; this
    # function is the parallel back-compat layer for persisted rejection.json
    # records whose `message` field was written before the rename.
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
    # thesis_quality_*
    if "theme-cluster fixation" in msg:
        return "thesis_quality_theme_cluster_fixation"
    if "direction whipsaw" in msg:
        return "thesis_quality_direction_whipsaw"
    if "needs_code starvation" in msg:
        return "thesis_quality_needs_code_starvation"
    if "has already been proposed" in msg:
        return "structural_thesis_id_repeated"
    # Stage 2 (no section prefix; lives at the contract layer)
    if "hypothesis-config misalignment" in msg:
        return "hypothesis_config_misalignment"
    if "required diagnostics not present" in msg:
        return "required_diagnostic_missing_post_compile"
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


MECHANISM_DIMENSION_ALIASES = {
    "trade_filtering": "signal_quality",
    "other": EMERGENT_MECHANISM_DIMENSION,
}


def _dimension_slug(text: str) -> str:
    words = re.findall(r"[a-z0-9]+", text.lower())
    return "_".join(words)


def _format_remediation(remediation: tuple[str, ...]) -> str:
    """Format a tuple of remediation suggestions into a single string.

    For a single suggestion, returns it as-is. For multiple, numbers them
    so the conductor can distinguish discrete options instead of seeing a
    forward-slash wall.
    """
    if not remediation:
        return ""
    if len(remediation) == 1:
        return remediation[0]
    return "; ".join(f"({i + 1}) {item}" for i, item in enumerate(remediation))


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
            f"base_config_path '{path}' must be a relative repo path without '..'",
            rejection_code="config_validity_base_config_path_invalid",
            evidence={"path": path},
        )
    if not normalized.endswith((".json", ".yaml", ".yml")):
        raise ThesisValidationError(
            f"base_config_path '{path}' must point to a JSON or YAML config artifact",
            rejection_code="config_validity_base_config_path_invalid",
            evidence={"path": path},
        )
    is_allowed = normalized.startswith(_ALLOWED_BASE_CONFIG_PREFIXES)
    if not is_allowed:
        if normalized.startswith("runtime/"):
            raise ThesisValidationError(
                f"base_config_path '{path}' points into runtime/. Do not construct "
                f"paths from runtime artifacts; reference a checked-in config under "
                f"configs/ instead (for example, the family baseline config).",
                rejection_code="config_validity_base_config_path_runtime_construction",
                evidence={"path": path},
            )
        # Other non-configs/ paths (including legacy experiments/) fall through
        # to the inheritance_blocked check in _validate_config_validity, which
        # rejects ANY path that isn't the family baseline. That gate is the
        # authoritative inheritance check; this specific subcase was redundant.


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


VALID_PROCESS_TOOLS: Final[frozenset[str]] = frozenset(
    {
        "list_round_results",
        "web_search",
    }
)


def _validate_process(
    tools_called: set[str] | frozenset[str], *, require_analyst_tool: bool = False
) -> None:
    missing = [tool for tool in sorted(VALID_PROCESS_TOOLS) if tool not in tools_called]
    if require_analyst_tool and "analyze_trades" not in tools_called:
        missing.append("analyze_trades")
    if not missing:
        return
    raise ThesisValidationError(
        f"Process gate failed: required tools not called: {missing}",
        rejection_code="process_required_tools_not_called",
        evidence={"missing_tools": missing, "tools_called": sorted(tools_called)},
    )


def _prior_thesis_entry(row: dict[str, Any]) -> dict[str, Any]:
    details = row.get("thesis_details", {})
    if not isinstance(details, dict):
        details = {}
    entry = {
        "thesis_id": row.get("thesis_id", "unknown"),
        "config_changes": row.get("config_changes") or {},
        "outcome": row.get("validator_status", "unknown"),
        "mechanism_dimension": row.get("mechanism_dimension", ""),
        "thesis_details": details,
    }
    proposal_label = row.get("proposal_label") or details.get("proposal_label")
    if proposal_label:
        entry["proposal_label"] = proposal_label
    hypothesis = row.get("hypothesis") or details.get("hypothesis")
    if hypothesis:
        entry["hypothesis"] = hypothesis
    return entry


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


# Thresholds for the computed dominant-cluster overlap. The cutoffs are tuned
# to match operator intuition: "high" means more than half the recent priors
# share a keyword with the proposed thesis.
_OVERLAP_HIGH_RATIO: Final[float] = 0.5
_OVERLAP_MEDIUM_RATIO: Final[float] = 0.25


def _computed_dominant_cluster_overlap(
    thesis: ResearchThesis, prior_theses: list[dict[str, Any]]
) -> str:
    """Compute overlap level from theme_keywords data, not LLM self-report.

    Returns "high" / "medium" / "low" based on the fraction of priors whose
    theme_keywords intersect the proposed thesis's theme_keywords.

    The LLM's `dominant_cluster_overlap` field is informational only — this
    function is the authoritative source for downstream gates.
    """
    if not prior_theses:
        return "low"
    proposed_kw = {kw.strip() for kw in thesis.theme_keywords if kw.strip()}
    if not proposed_kw:
        return "low"
    overlap_count = sum(
        1 for prior in prior_theses if _theme_keywords_from_prior(prior) & proposed_kw
    )
    ratio = overlap_count / len(prior_theses)
    if ratio >= _OVERLAP_HIGH_RATIO:
        return "high"
    if ratio >= _OVERLAP_MEDIUM_RATIO:
        return "medium"
    return "low"


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


def _detect_theme_cluster_fixation(
    thesis: ResearchThesis,
    prior_theses: list[dict[str, Any]],
) -> BehaviorSignal | None:
    """Detect when the proposed thesis fixates on a theme cluster.

    Returns a BehaviorSignal when >=4 of the last 7 priors (including the
    proposal itself) share at least one theme_keyword. Returns None when
    the pattern is absent.

    Confidence is proportional to the fraction of the window that overlaps:
    4/7 -> 0.57, 7/7 -> 1.0. Severity is "block" in Phase C to match the
    pre-refactor hard-block behavior.
    """
    proposed_keywords = {kw.strip() for kw in thesis.theme_keywords if kw.strip()}
    if not proposed_keywords:
        return None
    recent = prior_theses[-(B1_THEME_CLUSTER_WINDOW - 1) :]
    if not recent:
        return None
    overlap_count = 1  # the new thesis itself
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


# B2 direction whipsaw: detection has two complementary signals.
#
# (1) Data signal — preferred when both theses change the same numeric config
#     key. Lever-name prefix conventions map a value change to a direction:
#       - keys with min_/floor_ prefix: increase = tighten, decrease = loosen
#       - keys with max_/ceiling_ prefix: increase = loosen, decrease = tighten
#     This is the authoritative signal because it reads what the thesis
#     actually does, not what its name suggests.
#
# (2) Text signal — fallback when no shared numeric key gives a data direction.
#     Word-boundary match on explicit direction verbs in thesis_id or
#     hypothesis. Config-key prefixes are NOT direction tokens — that caused
#     false-positives where `min_stop_distance_pct` (a config key) registered
#     as a "tighten" direction word.
_B2_DIRECTION_TIGHTEN_TOKENS: Final[tuple[str, ...]] = (
    "tighten",
    "tightening",
    "narrow",
    "narrowing",
    "shrink",
    "shrinking",
)
_B2_DIRECTION_WIDEN_TOKENS: Final[tuple[str, ...]] = (
    "widen",
    "widening",
    "loosen",
    "loosening",
    "expand",
    "expanding",
)

_LEVER_PREFIX_TIGHTEN_ON_INCREASE: Final[tuple[str, ...]] = ("min_", "floor_", "minimum_")
_LEVER_PREFIX_LOOSEN_ON_INCREASE: Final[tuple[str, ...]] = (
    "max_",
    "ceiling_",
    "maximum_",
    "cap_",
)

# Pre-computed frozensets for hot-path membership checks in _b2_direction_of.
# Avoids rebuilding a fresh set on every call (called per-thesis during
# direction-whipsaw evaluation against the full prior list).
_B2_DIRECTION_TIGHTEN_TOKEN_SET: Final[frozenset[str]] = frozenset(_B2_DIRECTION_TIGHTEN_TOKENS)
_B2_DIRECTION_WIDEN_TOKEN_SET: Final[frozenset[str]] = frozenset(_B2_DIRECTION_WIDEN_TOKENS)


def _b2_direction_of(text: str) -> str | None:
    """Return 'tighten', 'widen', or None for the dominant direction in `text`.

    Tokenises `text` by splitting on any non-alphabetic character (so snake_case,
    kebab-case, and natural prose all decompose to the same alphabetic-token set)
    and tests for exact token membership in the direction-word sets. This avoids
    two prior failure modes:

      * Substring matching let config-key prefixes like 'min_' (an old TIGHTEN
        token) trip on `min_stop_distance_pct` (not a direction word).
      * `re.search(\\bwiden\\b)` does not match `widen_max_stops` because `_`
        is a regex word character, so the word-boundary fails between `n` and
        `_`. Tokenisation sidesteps the issue by treating `_` as a separator.
    """
    tokens = set(re.findall(r"[a-z]+", text.lower()))
    has_tighten = bool(tokens & _B2_DIRECTION_TIGHTEN_TOKEN_SET)
    has_widen = bool(tokens & _B2_DIRECTION_WIDEN_TOKEN_SET)
    if has_tighten and not has_widen:
        return "tighten"
    if has_widen and not has_tighten:
        return "widen"
    return None  # ambiguous or none


def _direction_from_value_change(key: str, old_val: float, new_val: float) -> str | None:
    """Map a numeric value change on a known-convention key to tighten/widen.

    Returns None when the key has no recognized convention (in which case the
    text-based signal is the only available fallback) or when values are equal.
    """
    if old_val == new_val:
        return None
    lower_key = key.lower()
    increased = new_val > old_val
    if any(lower_key.startswith(p) for p in _LEVER_PREFIX_TIGHTEN_ON_INCREASE):
        return "tighten" if increased else "widen"
    if any(lower_key.startswith(p) for p in _LEVER_PREFIX_LOOSEN_ON_INCREASE):
        return "widen" if increased else "tighten"
    return None


def _proposed_direction(thesis: ResearchThesis, prior: dict[str, Any]) -> str | None:
    """Resolve direction prioritising data signal over text signal.

    Looks for a shared numeric config key with a known lever-name convention;
    if found, returns the data-derived direction. Otherwise falls back to
    word-boundary text match on thesis_id + hypothesis.

    The asymmetry with _prior_direction is intentional: a prior thesis's
    config_changes are accessible via the prior dict, but its BASELINE value
    is not — without baseline, the prior's data-direction is unrecoverable.
    Text matching on the prior's thesis_id is the only signal available there.
    """
    prior_changes = prior.get("config_changes") or {}
    for key, new_val in (thesis.config_changes or {}).items():
        if key not in prior_changes:
            continue
        prior_val = prior_changes[key]
        if not (_is_numeric_value(new_val) and _is_numeric_value(prior_val)):
            continue
        data_direction = _direction_from_value_change(str(key), float(prior_val), float(new_val))
        if data_direction is not None:
            return data_direction
    return _b2_direction_of(f"{thesis.thesis_id} {thesis.hypothesis}")


def _prior_direction(prior: dict[str, Any]) -> str | None:
    """Resolve prior's direction: text-only (no baseline available here)."""
    details = _prior_thesis_details(prior)
    return _b2_direction_of(
        " ".join(
            str(part or "")
            for part in (
                prior.get("thesis_id"),
                prior.get("proposal_label") or details.get("proposal_label"),
                prior.get("hypothesis") or details.get("hypothesis"),
            )
        )
    )


def _detect_direction_whipsaw(
    thesis: ResearchThesis,
    prior_theses: list[dict[str, Any]],
) -> BehaviorSignal | None:
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
                signal = _neighboring_threshold_signal(
                    key=str(key),
                    prior_value=prior_val,
                    new_value=new_val,
                    prior_thesis_id=str(prior.get("thesis_id", "?")),
                    ratio=ratio,
                )
                raise ThesisValidationError(
                    signal.summary,
                    rejection_code=signal.code,
                    evidence=dict(signal.evidence),
                    remediation_hint=_format_remediation(signal.remediation),
                )


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


def _detect_missing_mechanism_evidence_disqualifier(
    thesis: ResearchThesis,
) -> BehaviorSignal | None:
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


def _detect_needs_code_starvation(
    thesis: ResearchThesis,
    prior_theses: list[dict[str, Any]],
) -> BehaviorSignal | None:
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
        remediation=("Set requires_code_change=false and use existing config keys",),
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
        _normalize_expected_effect(effect) for effect in (normalized.get("expected_effects") or [])
    ]
    normalized["disqualifiers"] = [
        _normalize_disqualifier(disqualifier)
        for disqualifier in (normalized.get("disqualifiers") or [])
    ]
    citations = normalized.get("evidence_citations")
    if isinstance(citations, list):
        normalized["evidence_citations"] = [
            (
                {**c, "source": "round_result"}
                if isinstance(c, dict) and c.get("source") == "experiment_result"
                else c
            )
            for c in citations
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

        for db_path in _iter_backtest_db_paths(root):
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
        if strategy_family and row.get("strategy_family") != strategy_family:
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
# Guardrail 2: Hypothesis-config alignment scoring
# ---------------------------------------------------------------------------


class MissingFamilyKeyConceptsError(LookupError):
    """Raised when a family's `key_concepts` map is missing or empty.

    Stage 2 alignment is unenforceable without it; surfacing as a hard error
    forces operators to populate the map at the strategy level rather than
    silently disabling alignment for that family.
    """


@lru_cache(maxsize=None)
def _load_family_key_concepts(family_name: str) -> dict[str, tuple[str, ...]]:
    """Return the concept regex map for a family.

    Raises ``MissingFamilyKeyConceptsError`` when the family is unknown or
    has no `key_concepts` populated. Callers that need a permissive default
    must catch the error explicitly — there is no silent fail-open path here
    on purpose. The old fail-open behavior let entire families skip Stage 2
    alignment enforcement undetected.
    """
    if not family_name:
        raise MissingFamilyKeyConceptsError(
            "family_name is empty; cannot enforce hypothesis-config alignment"
        )
    try:
        from family_research_spec import get_family_research_spec
    except Exception as exc:  # noqa: BLE001
        raise MissingFamilyKeyConceptsError(
            f"family_research_spec module not importable: {exc}"
        ) from exc
    try:
        spec = get_family_research_spec(family_name)
    except (KeyError, ValueError) as exc:
        raise MissingFamilyKeyConceptsError(
            f"no FamilyResearchSpec registered for family '{family_name}'"
        ) from exc
    key_concepts = dict(getattr(spec, "key_concepts", {}) or {})
    if not key_concepts:
        # Documented fail-open: FamilyResearchSpec.key_concepts defaults to
        # empty. Families opt INTO Stage 2 alignment by populating it. Log
        # so operators can see opt-outs without breaking valid families
        # (e.g. the _demo skeleton, newly scaffolded families).
        log.warning(
            "family %r has empty FamilyResearchSpec.key_concepts — Stage 2 "
            "hypothesis-config alignment will fail-open. Populate key_concepts "
            "to enable enforcement.",
            family_name,
        )
        return {}
    return key_concepts


def check_hypothesis_alignment(
    hypothesis: str,
    mechanism: str,
    config_changes: dict[str, Any],
    *,
    family_name: str = "",
) -> tuple[float, str]:
    """Score whether config_changes actually test the stated hypothesis.

    Uses a heuristic keyword approach (no LLM call needed for v1) driven by
    the family-specific `key_concepts` map declared on the strategy's
    `FamilyResearchSpec`. Strategy-specific data lives on the strategy spec,
    not in the validator — the validator reads, never hardcodes.

    Returns (score 0-1, explanation).

    Score meanings:
      1.0 = every config key referenced a concept in hypothesis/mechanism
            (or the family ships no concept map → rule fails open)
      0.0 = no connection between story and implementation
    """
    import re

    if not config_changes:
        return 1.0, "No config changes (code change thesis)"

    # _load_family_key_concepts raises MissingFamilyKeyConceptsError only when
    # the family is unknown / unregistered (a configuration error). When the
    # family is registered but has empty key_concepts (the documented
    # fail-open default), the function logs and returns {} — we then
    # short-circuit to score 1.0 so families that haven't opted into Stage 2
    # alignment scoring don't get hard-rejected. See _load_family_key_concepts
    # for the policy rationale.
    key_concepts = _load_family_key_concepts(family_name)
    if not key_concepts:
        return (
            1.0,
            f"family '{family_name}' has empty key_concepts — alignment fails open",
        )

    hyp_lower = (hypothesis + " " + mechanism).lower()
    config_keys = set(config_changes.keys())

    aligned_keys = 0
    misaligned_keys: list[str] = []

    for key in config_keys:
        concepts = key_concepts.get(key, ())
        if not concepts:
            # Key isn't catalogued for this family — give benefit of doubt
            # rather than penalize agent-emitted bookkeeping or new keys.
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


def _raise_aggregated_validation_error(
    *,
    rejection_code: str,
    summary_prefix: str,
    issues: list[dict[str, Any]],
    describer: Callable[[dict[str, Any]], str],
    extra_evidence: dict[str, Any] | None = None,
) -> None:
    """Raise one ThesisValidationError summarizing N independent failures.

    Used by gates that validate multiple aspects of one field. Collects
    every failure mode into a single rejection with structured evidence
    so the LLM sees the full picture in one attempt instead of N retries.
    """
    if not issues:
        return
    descriptions = [describer(issue) for issue in issues]
    evidence: dict[str, Any] = {"issues": issues}
    if extra_evidence:
        evidence.update(extra_evidence)
    raise ThesisValidationError(
        f"{summary_prefix}: {'; '.join(descriptions)}",
        rejection_code=rejection_code,
        evidence=evidence,
    )


def _describe_emergent_issue(issue: dict[str, Any]) -> str:
    kind = issue["kind"]
    if kind == "missing_new_dimension_name":
        return "new_dimension_name is empty"
    if kind == "new_dimension_name_duplicates_core":
        return f"new_dimension_name '{issue['name']}' duplicates a core dimension"
    if kind == "short_fields":
        field_list = ", ".join(f"{f['field']} ({f['actual_chars']} chars)" for f in issue["fields"])
        return (
            f"emergent justification fields each need "
            f"≥{_MIN_EMERGENT_FIELD_CHARS} chars: {field_list}"
        )
    return kind  # defensive: unknown kinds surface their tag


def _describe_underexplored_issue(issue: dict[str, Any]) -> str:
    kind = issue["kind"]
    if kind == "empty":
        return "must be non-empty when prior theses exist"
    if kind == "invalid_values":
        return f"contains invalid mechanism dimensions: {issue['invalid']}"
    if kind == "includes_chosen":
        return f"must not include the chosen dimension '{issue['chosen']}'"
    return kind  # defensive: unknown kinds surface their tag


def _validate_emergent_dimension(thesis: ResearchThesis) -> None:
    """Validate the rare emergent-dimension path with a single rejection code.

    All emergent-path failures roll up to ``structural_emergent_thesis_malformed``
    with structured evidence describing every issue found. One rejection per
    attempt (since emergent fields are interdependent and the LLM should fix
    them all together).
    """
    issues: list[dict[str, Any]] = []

    new_dimension_name = _dimension_slug(thesis.new_dimension_name)
    if not new_dimension_name:
        issues.append({"kind": "missing_new_dimension_name"})
    elif new_dimension_name in CORE_MECHANISM_DIMENSIONS:
        issues.append(
            {
                "kind": "new_dimension_name_duplicates_core",
                "name": thesis.new_dimension_name,
            }
        )

    short_fields = [
        {"field": field, "actual_chars": len(getattr(thesis, field).strip())}
        for field in _EMERGENT_REQUIRED_FIELDS
        if len(getattr(thesis, field).strip()) < _MIN_EMERGENT_FIELD_CHARS
    ]
    if short_fields:
        issues.append({"kind": "short_fields", "fields": short_fields})

    # emergent passes the global threshold so the LLM rejection block can show it
    _raise_aggregated_validation_error(
        rejection_code="structural_emergent_thesis_malformed",
        summary_prefix="Emergent thesis malformed",
        issues=issues,
        describer=_describe_emergent_issue,
        extra_evidence={"min_emergent_field_chars": _MIN_EMERGENT_FIELD_CHARS},
    )


def _validate_underexplored_dimensions(
    thesis: ResearchThesis,
    prior_theses: list[dict[str, Any]] | None,
) -> None:
    """Enforce the underexplored_dimensions_considered contract.

    The contract only fires when prior theses exist (no priors = nothing to
    have underexplored). The helper is self-guarding: it returns immediately
    when prior_theses is None or empty, so callers don't need to wrap the
    call in `if prior_theses:`. Signature matches its sibling
    `_validate_mechanism_dimension` for consistency.

    When priors exist, the contract requires:
      * Non-empty list.
      * Every entry is a known mechanism dimension.
      * Chosen mechanism_dimension is NOT in the list.

    All failure modes share one rejection_code with structured evidence so
    the LLM sees every issue at once instead of one per retry.
    """
    if not prior_theses:
        return
    issues: list[dict[str, Any]] = []
    items = thesis.underexplored_dimensions_considered

    if not items:
        issues.append({"kind": "empty"})
    else:
        known = MECHANISM_DIMENSIONS | _known_emergent_dimension_names(prior_theses)
        invalid = [d for d in items if d not in known]
        if invalid:
            issues.append({"kind": "invalid_values", "invalid": invalid, "valid": sorted(known)})
        if thesis.mechanism_dimension in items:
            issues.append({"kind": "includes_chosen", "chosen": thesis.mechanism_dimension})

    # underexplored carries no extra_evidence: the valid-dimension list is
    # already embedded per-issue, and there is no global threshold to surface
    # (unlike emergent which reports min_emergent_field_chars).
    _raise_aggregated_validation_error(
        rejection_code="structural_underexplored_dimensions_invalid",
        summary_prefix="underexplored_dimensions_considered invalid",
        issues=issues,
        describer=_describe_underexplored_issue,
    )


def _validate_mechanism_dimension(
    thesis: ResearchThesis,
    prior_theses: list[dict[str, Any]] | None,
) -> None:
    """Validate the mechanism_dimension contract.

    Three checks in dependency order:
      1. Field is non-empty.
      2. Value is a known dimension (core set OR a prior-emergent name).
      3. If "emergent", delegate to _validate_emergent_dimension for the
         conditional sub-contract (new_dimension_name + 3 emergent fields).

    Fail-fast within the contract: a missing-field failure does not check
    the value-validity rule, since the latter would fire a meaningless
    "'' is not a valid mechanism_dimension" rejection. Each gate's
    rejection_code is preserved from its pre-refactor identity.
    """
    if not thesis.mechanism_dimension.strip():
        raise ThesisValidationError(
            "Missing mechanism_dimension. Every thesis must declare which "
            "dimension it explores: " + ", ".join(sorted(MECHANISM_DIMENSIONS)),
            rejection_code="structural_missing_mechanism_dimension",
        )
    known_dimensions = MECHANISM_DIMENSIONS | _known_emergent_dimension_names(prior_theses)
    if thesis.mechanism_dimension not in known_dimensions:
        raise ThesisValidationError(
            f"Invalid mechanism_dimension '{thesis.mechanism_dimension}'. "
            f"Must be one of: {sorted(known_dimensions)}",
            rejection_code="structural_mechanism_dimension_invalid",
            evidence={"mechanism_dimension": thesis.mechanism_dimension},
        )
    if thesis.mechanism_dimension == EMERGENT_MECHANISM_DIMENSION:
        _validate_emergent_dimension(thesis)


def _validate_thesis_specifies_change(thesis: ResearchThesis) -> None:
    """Validate that the thesis declares WHAT it changes.

    Two checks in dependency order:
      1. At least one of config_changes / requires_code_change is set.
      2. If requires_code_change=true, requested_primitives must be
         non-empty.

    Fail-fast within the contract: if neither change is specified, the
    requested_primitives check is meaningless. Each gate's rejection_code
    is preserved.
    """
    if not thesis.config_changes and not thesis.requires_code_change:
        raise ThesisValidationError(
            "Thesis has neither config_changes nor requires_code_change=true",
            rejection_code="structural_missing_config_or_code_change",
        )
    if thesis.requires_code_change and not thesis.requested_primitives:
        raise ThesisValidationError(
            "requires_code_change theses must declare requested_primitives",
            rejection_code="structural_missing_requested_primitives",
        )


def _validate_expected_effects_present(thesis: ResearchThesis) -> None:
    """Validate that expected_effects is populated.

    The conductor must declare ≥1 prediction before a thesis can be
    evaluated. Per-effect metric-backing validation lives in a separate
    helper (_validate_expected_effects_metrics_backed) called later in
    _validate_structural — that check must run only after the falsification
    and disqualifiers presence checks have passed, preserving the baseline
    fail-fast order that was in place before the contract-extraction
    refactor.
    """
    if not thesis.expected_effects:
        raise ThesisValidationError(
            "Thesis has no expected_effects — cannot evaluate without predictions",
            rejection_code="structural_missing_expected_effects",
        )


def _validate_expected_effects_metrics_backed(thesis: ResearchThesis) -> None:
    """Validate that every declared expected_effects metric is reachable.

    Each effect's metric must be either a builtin OR declared in
    required_diagnostics. Runs after the presence check and after
    falsification/disqualifiers — see _validate_expected_effects_present
    for why the two checks are not bundled.
    """
    for effect in thesis.expected_effects:
        if effect.metric in BUILTIN_METRICS:
            continue
        if effect.metric in thesis.required_diagnostics:
            continue
        raise ThesisValidationError(
            f"Expected effect metric '{effect.metric}' is not a builtin metric "
            f"and is not listed in required_diagnostics",
            rejection_code="structural_expected_effect_metric_unbacked",
            evidence={"metric": effect.metric},
        )


def _source_code_symbols(path: Path) -> set[str]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError) as exc:
        raise ThesisValidationError(
            f"source_code_verification cannot read or parse '{path}': {exc}",
            rejection_code="structural_source_code_verification_invalid",
            evidence={"path": str(path), "error": str(exc)},
        ) from exc
    return {
        node.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
    }


def _collect_source_code_verification_failures(thesis: ResearchThesis) -> list[BehaviorSignal]:
    text = thesis.source_code_verification.strip()
    if not text:
        return [
            BehaviorSignal(
                code="structural_source_code_verification_invalid",
                confidence=1.0,
                severity="block",
                summary=(
                    "source_code_verification must cite a real repo-relative Python "
                    "file and symbol, for example strategies/ema/signals.py:generate_signals_for_frame"
                ),
            )
        ]

    matches = re.findall(r"([A-Za-z0-9_./-]+\.py):([A-Za-z_][A-Za-z0-9_]*)", text)
    if not matches:
        return [
            BehaviorSignal(
                code="structural_source_code_verification_invalid",
                confidence=1.0,
                severity="block",
                summary=(
                    "source_code_verification must include at least one "
                    "repo-relative path:symbol reference"
                ),
                evidence={"source_code_verification": text},
            )
        ]

    repo_root = Path(__file__).resolve().parent
    failures: list[BehaviorSignal] = []
    family_prefix = ""
    try:
        family_prefix = f"strategies/{load_family(thesis.strategy_family).name}/"
    except ValueError:
        # Unknown-family failures are reported by the config-validity layer.
        family_prefix = ""
    has_family_reference = False
    for raw_path, symbol in matches:
        path = Path(raw_path)
        if path.is_absolute() or ".." in path.parts:
            failures.append(
                BehaviorSignal(
                    code="structural_source_code_verification_invalid",
                    confidence=1.0,
                    severity="block",
                    summary=f"source_code_verification path '{raw_path}' must be repo-relative",
                    evidence={"path": raw_path, "symbol": symbol},
                )
            )
            continue
        if family_prefix and path.as_posix().startswith(family_prefix):
            has_family_reference = True
        full_path = repo_root / path
        if not full_path.exists() or not full_path.is_file():
            failures.append(
                BehaviorSignal(
                    code="structural_source_code_verification_invalid",
                    confidence=1.0,
                    severity="block",
                    summary=f"source_code_verification file '{raw_path}' does not exist",
                    evidence={"path": raw_path, "symbol": symbol},
                )
            )
            continue
        try:
            symbols = _source_code_symbols(full_path)
        except ThesisValidationError as exc:
            failures.append(_signal_from_validation_error(exc))
            continue
        if symbol not in symbols:
            failures.append(
                BehaviorSignal(
                    code="structural_source_code_verification_invalid",
                    confidence=1.0,
                    severity="block",
                    summary=(
                        f"source_code_verification symbol '{symbol}' was not found in '{raw_path}'"
                    ),
                    evidence={"path": raw_path, "symbol": symbol},
                )
            )
    if family_prefix and not has_family_reference:
        failures.append(
            BehaviorSignal(
                code="structural_source_code_verification_invalid",
                confidence=1.0,
                severity="block",
                summary=(
                    "source_code_verification must cite at least one source file "
                    f"under '{family_prefix}'"
                ),
                evidence={"required_prefix": family_prefix},
            )
        )
    return failures


def _resolve_evidence_context(
    *, require_analyst_evidence: bool, evidence_context: str | None
) -> str:
    if evidence_context is None:
        return _EVIDENCE_CONTEXT_TRADES if require_analyst_evidence else _EVIDENCE_CONTEXT_NO_TRADES
    if evidence_context not in _EVIDENCE_SOURCES_BY_CONTEXT:
        allowed = ", ".join(sorted(_EVIDENCE_SOURCES_BY_CONTEXT))
        raise ValueError(
            f"Unknown evidence_context '{evidence_context}'; expected one of {allowed}"
        )
    return evidence_context


def _collect_research_contract_failures(
    thesis: ResearchThesis, *, evidence_context: str
) -> list[BehaviorSignal]:
    failures: list[BehaviorSignal] = []

    if not thesis.evidence_strength:
        failures.append(
            BehaviorSignal(
                code="structural_missing_evidence_strength",
                confidence=1.0,
                severity="block",
                summary=(
                    "evidence_strength is required; classify evidence as direct, "
                    "proxy, mixed, or speculative"
                ),
            )
        )

    blank_alternative_mechanisms = [
        index
        for index, alternative in enumerate(thesis.alternatives_considered)
        if not alternative.mechanism.strip()
    ]
    if (
        len(thesis.alternatives_considered) < _MIN_ALTERNATIVES_CONSIDERED
        or blank_alternative_mechanisms
    ):
        failures.append(
            BehaviorSignal(
                code="structural_alternatives_considered_invalid",
                confidence=1.0,
                severity="block",
                summary=(
                    f"alternatives_considered must contain at least "
                    f"{_MIN_ALTERNATIVES_CONSIDERED} rejected mechanisms"
                ),
                evidence={
                    "actual_count": len(thesis.alternatives_considered),
                    "min_count": _MIN_ALTERNATIVES_CONSIDERED,
                    "blank_mechanism_indexes": blank_alternative_mechanisms,
                },
            )
        )

    sources = {citation.source for citation in thesis.evidence_citations}
    empty_citations = [
        citation.source for citation in thesis.evidence_citations if not citation.citation.strip()
    ]
    required_sources = _EVIDENCE_SOURCES_BY_CONTEXT[evidence_context]
    missing_sources = sorted(required_sources - sources)
    if missing_sources or empty_citations:
        failures.append(
            BehaviorSignal(
                code="structural_evidence_citations_invalid",
                confidence=1.0,
                severity="block",
                summary=(
                    "evidence_citations must include non-empty "
                    + ", ".join(sorted(required_sources))
                    + " citations"
                ),
                evidence={
                    "missing_sources": missing_sources,
                    "empty_citation_sources": empty_citations,
                    "observed_sources": sorted(sources),
                    "evidence_context": evidence_context,
                },
            )
        )

    effect_metrics_are_backed = all(
        effect.metric in BUILTIN_METRICS or effect.metric in thesis.required_diagnostics
        for effect in thesis.expected_effects
    )
    if not thesis.expected_effects:
        pass
    elif not effect_metrics_are_backed:
        pass
    elif len(thesis.expected_effects) < _MIN_EXPECTED_EFFECTS:
        failures.append(
            BehaviorSignal(
                code="structural_expected_effects_not_coupled",
                confidence=1.0,
                severity="block",
                summary=(
                    f"expected_effects must contain at least {_MIN_EXPECTED_EFFECTS} "
                    "coupled metric predictions"
                ),
                evidence={
                    "actual_count": len(thesis.expected_effects),
                    "min_count": _MIN_EXPECTED_EFFECTS,
                },
            )
        )
    else:
        metrics = {effect.metric for effect in thesis.expected_effects}
        short_rationales = [
            effect.metric
            for effect in thesis.expected_effects
            if len((effect.rationale or "").strip()) < _MIN_EFFECT_RATIONALE_CHARS
        ]
        if len(metrics) < _MIN_EXPECTED_EFFECTS or short_rationales:
            failures.append(
                BehaviorSignal(
                    code="structural_expected_effects_not_coupled",
                    confidence=1.0,
                    severity="block",
                    summary=(
                        "expected_effects must use at least two distinct metrics and "
                        "each prediction must include a substantive rationale"
                    ),
                    evidence={
                        "distinct_metrics": sorted(metrics),
                        "short_rationale_metrics": short_rationales,
                        "min_rationale_chars": _MIN_EFFECT_RATIONALE_CHARS,
                    },
                )
            )

    failures.extend(_collect_source_code_verification_failures(thesis))
    return failures


def _validate_structural(
    thesis: ResearchThesis,
    prior_theses: list[dict[str, Any]] | None = None,
) -> None:
    """Compatibility entry point for tests; production uses the same collector."""
    _raise_mechanical_batch(_collect_mechanical_failures(thesis, prior_theses))


def _validate_thesis_quality(
    thesis: ResearchThesis,
    prior_theses: list[dict[str, Any]] | None = None,
) -> None:
    """Compatibility entry point; delegates to the live behavioral pass."""
    _run_behavioral_pass(thesis, prior_theses)


def _validate_config_validity(
    thesis: ResearchThesis,
    prior_theses: list[dict[str, Any]] | None = None,
) -> None:
    """Compatibility entry point; delegates to live behavioral/mechanical gates."""
    _run_behavioral_pass(thesis, prior_theses)
    _raise_mechanical_batch(_collect_mechanical_config_validity_failures(thesis))


def _signal_from_validation_error(exc: ThesisValidationError) -> BehaviorSignal:
    return BehaviorSignal(
        code=exc.rejection_code or infer_rejection_code(str(exc)),
        confidence=1.0,
        severity="block",
        summary=str(exc),
        evidence=dict(exc.evidence),
        remediation=(exc.remediation_hint,) if exc.remediation_hint else (),
    )


def _collect_from_validator(call: Callable[[], None]) -> list[BehaviorSignal]:
    try:
        call()
    except ThesisValidationError as exc:
        return [_signal_from_validation_error(exc)]
    return []


def _run_behavioral_pass(
    thesis: ResearchThesis,
    prior_theses: list[dict[str, Any]] | None = None,
) -> None:
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

    if prior_theses:
        if (sig := _detect_neighboring_threshold(thesis, prior_theses)) is not None:
            signals.append(sig)
        if (sig := _detect_config_key_overlap(thesis, prior_theses)) is not None:
            signals.append(sig)

    decision = _policy_decide(signals)
    if decision.action == "reject":
        triggering = decision.triggering
        assert triggering is not None, "reject decisions must carry a triggering signal"
        raise ThesisValidationError(
            triggering.summary,
            rejection_code=triggering.code,
            evidence=dict(triggering.evidence),
            remediation_hint=_format_remediation(triggering.remediation),
        )


def _collect_inline_structural_failures(
    thesis: ResearchThesis,
    prior_theses: list[dict[str, Any]] | None,
) -> list[BehaviorSignal]:
    failures: list[BehaviorSignal] = []

    if not thesis.thesis_id.strip():
        failures.append(
            BehaviorSignal(
                code="structural_missing_thesis_id",
                confidence=1.0,
                severity="block",
                summary="Missing thesis_id",
            )
        )

    if not thesis.hypothesis.strip():
        failures.append(
            BehaviorSignal(
                code="structural_missing_hypothesis",
                confidence=1.0,
                severity="block",
                summary="Missing hypothesis",
            )
        )
    if not thesis.mechanism.strip():
        failures.append(
            BehaviorSignal(
                code="structural_missing_mechanism",
                confidence=1.0,
                severity="block",
                summary="Missing mechanism",
            )
        )

    novelty_text = thesis.dimension_novelty.strip()
    if not novelty_text or len(novelty_text) < _MIN_NOVELTY_EXPLANATION_CHARS:
        failures.append(
            BehaviorSignal(
                code="structural_dimension_novelty_invalid",
                confidence=1.0,
                severity="block",
                summary=(
                    f"dimension_novelty must be ≥{_MIN_NOVELTY_EXPLANATION_CHARS} chars "
                    f"explaining why this thesis is not a parameter variation of prior work. "
                    f"Got {len(novelty_text)} chars."
                ),
                evidence={
                    "actual_chars": len(novelty_text),
                    "min_chars": _MIN_NOVELTY_EXPLANATION_CHARS,
                },
            )
        )

    if prior_theses:
        if not thesis.causal_cluster.strip():
            failures.append(
                BehaviorSignal(
                    code="structural_missing_causal_cluster",
                    confidence=1.0,
                    severity="block",
                    summary=(
                        "causal_cluster is required when prior theses exist. "
                        "Name the causal family this thesis belongs to."
                    ),
                )
            )
        computed_overlap = _computed_dominant_cluster_overlap(thesis, prior_theses)
        if computed_overlap == "high" and (
            len(thesis.novel_connection.strip()) < _MIN_NOVEL_CONNECTION_CHARS
        ):
            failures.append(
                BehaviorSignal(
                    code="structural_novel_connection_too_short",
                    confidence=1.0,
                    severity="block",
                    summary=(
                        f"novel_connection must explain why a high-overlap thesis is "
                        f"materially new instead of another variation of the dominant cluster "
                        f"(computed overlap with prior theme_keywords: high). "
                        f"Required ≥{_MIN_NOVEL_CONNECTION_CHARS} chars in novel_connection."
                    ),
                    evidence={
                        "min_chars": _MIN_NOVEL_CONNECTION_CHARS,
                        "computed_overlap": computed_overlap,
                    },
                )
            )

    falsification_text = (thesis.falsification_or_alternative or "").strip()
    if len(falsification_text) < _MIN_FALSIFICATION_CHARS:
        failures.append(
            BehaviorSignal(
                code="structural_falsification_invalid",
                confidence=1.0,
                severity="block",
                summary=(
                    f"falsification_or_alternative must be ≥{_MIN_FALSIFICATION_CHARS} chars "
                    f"describing what data pattern would weaken this mechanism, independent "
                    f"of metric movement. Got {len(falsification_text)} chars."
                ),
                evidence={
                    "actual_chars": len(falsification_text),
                    "min_chars": _MIN_FALSIFICATION_CHARS,
                },
            )
        )

    if not thesis.disqualifiers:
        failures.append(
            BehaviorSignal(
                code="structural_missing_disqualifiers",
                confidence=1.0,
                severity="block",
                summary="Thesis has no disqualifiers — need at least one falsification condition",
            )
        )

    return failures


def _collect_mechanical_config_validity_failures(thesis: ResearchThesis) -> list[BehaviorSignal]:
    failures: list[BehaviorSignal] = []
    base_path_valid = True
    if thesis.base_config_path:
        path_failures = _collect_from_validator(
            lambda: _validate_base_config_path(thesis.base_config_path)
        )
        failures.extend(path_failures)
        base_path_valid = not path_failures
    if thesis.base_contract_id:
        failures.append(
            BehaviorSignal(
                code="config_validity_base_contract_id_not_allowed",
                confidence=1.0,
                severity="block",
                summary=(
                    "base_contract_id is not allowed; research theses must start from the family "
                    "baseline instead of inheriting a prior winner."
                ),
            )
        )
    family_load_failed = False
    try:
        baseline_path = _family_baseline_path(thesis)
    except ThesisValidationError as exc:
        family_load_failed = True
        failures.append(_signal_from_validation_error(exc))
    if (
        not family_load_failed
        and base_path_valid
        and thesis.base_config_path
        and thesis.base_config_path != baseline_path
    ):
        failures.append(
            BehaviorSignal(
                code="config_validity_base_config_path_inheritance_blocked",
                confidence=1.0,
                severity="block",
                summary=(
                    f"base_config_path must be empty or the family baseline '{baseline_path}'; "
                    "prior/winning config inheritance is not allowed."
                ),
                evidence={"baseline_path": baseline_path, "actual_path": thesis.base_config_path},
            )
        )
    for key in sorted(CONFIG_CHANGES_METADATA_KEYS & set(thesis.config_changes)):
        failures.append(
            BehaviorSignal(
                code="config_validity_config_changes_metadata_leak",
                confidence=1.0,
                severity="block",
                summary=(
                    f"config_changes contains thesis metadata key '{key}'. "
                    f"Set top-level {key}=true instead of putting it in runtime config changes."
                ),
                evidence={"leaked_key": key},
            )
        )
    return failures


def _collect_mechanical_failures(
    thesis: ResearchThesis,
    prior_theses: list[dict[str, Any]] | None = None,
    *,
    evidence_context: str = _EVIDENCE_CONTEXT_TRADES,
) -> list[BehaviorSignal]:
    failures = _collect_inline_structural_failures(thesis, prior_theses)
    failures.extend(
        _collect_from_validator(lambda: _validate_mechanism_dimension(thesis, prior_theses))
    )
    if prior_theses:
        failures.extend(
            _collect_from_validator(
                lambda: _validate_underexplored_dimensions(thesis, prior_theses)
            )
        )
    failures.extend(_collect_from_validator(lambda: _validate_thesis_specifies_change(thesis)))
    failures.extend(_collect_from_validator(lambda: _validate_expected_effects_present(thesis)))
    failures.extend(
        _collect_research_contract_failures(
            thesis,
            evidence_context=evidence_context,
        )
    )
    for effect in thesis.expected_effects:
        if effect.metric in BUILTIN_METRICS:
            continue
        if effect.metric in thesis.required_diagnostics:
            continue
        failures.append(
            BehaviorSignal(
                code="structural_expected_effect_metric_unbacked",
                confidence=1.0,
                severity="block",
                summary=(
                    f"Expected effect metric '{effect.metric}' is not a builtin metric "
                    f"and is not listed in required_diagnostics"
                ),
                evidence={"metric": effect.metric},
            )
        )
    failures.extend(_collect_mechanical_config_validity_failures(thesis))
    return failures


def _raise_mechanical_batch(failures: list[BehaviorSignal]) -> None:
    if not failures:
        return
    if len(failures) == 1:
        single = failures[0]
        raise ThesisValidationError(
            single.summary,
            rejection_code=single.code,
            evidence=dict(single.evidence),
            remediation_hint=_format_remediation(single.remediation),
        )
    failure_payload = [
        {"code": f.code, "summary": f.summary, "evidence": dict(f.evidence)} for f in failures
    ]
    raise ThesisValidationError(
        "Multiple mechanical issues: " + "; ".join(f"{f.code}: {f.summary}" for f in failures),
        rejection_code="structural_mechanical_batch_failures",
        evidence={"count": len(failures), "failures": failure_payload},
    )


def validate_research_thesis(
    thesis: ResearchThesis,
    prior_theses: list[dict[str, Any]] | None = None,
    *,
    tools_called: set[str] | None = None,
    require_analyst_evidence: bool = True,
    evidence_context: str | None = None,
    require_analyst_tool: bool = False,
) -> ResearchThesis:
    """Validate a research thesis. Raises ThesisValidationError if invalid.

    Dispatches Stage 1 to four named sub-section helpers in fixed order:
    process → behavioral → mechanical.
    """
    resolved_evidence_context = _resolve_evidence_context(
        require_analyst_evidence=require_analyst_evidence,
        evidence_context=evidence_context,
    )
    if tools_called is not None:
        _validate_process(tools_called, require_analyst_tool=require_analyst_tool)
    _run_behavioral_pass(thesis, prior_theses)
    mechanical_failures = _collect_mechanical_failures(
        thesis,
        prior_theses,
        evidence_context=resolved_evidence_context,
    )
    _raise_mechanical_batch(mechanical_failures)
    return thesis


def validate_thesis_dict(
    raw: dict,
    prior_theses: list[dict[str, Any]] | None = None,
    *,
    research_round_id: str,
    attempt_number: int,
    assign_thesis_id: Callable[[str, int], str],
    tools_called: set[str] | None = None,
    require_analyst_evidence: bool = True,
    evidence_context: str | None = None,
    require_analyst_tool: bool = False,
) -> ResearchThesis:
    """Parse a raw dict into ResearchThesis and validate it.

    Use this when the conductor output is a plain dict.
    Raises ThesisValidationError or pydantic ValidationError.
    """
    normalized = normalize_thesis_payload(dict(raw))
    normalized["thesis_id"] = assign_thesis_id(research_round_id, attempt_number)
    thesis = ResearchThesis.model_validate(normalized)
    return validate_research_thesis(
        thesis,
        prior_theses=prior_theses,
        tools_called=tools_called,
        require_analyst_evidence=require_analyst_evidence,
        evidence_context=evidence_context,
        require_analyst_tool=require_analyst_tool,
    )


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
    *,
    tools_called: set[str] | None = None,
    require_analyst_evidence: bool = True,
    evidence_context: str | None = None,
    require_analyst_tool: bool = False,
) -> ResearchThesis:
    """Stage 1: pre-compile validator. Alias for `validate_research_thesis`."""
    return validate_research_thesis(
        thesis,
        prior_theses=prior_theses,
        tools_called=tools_called,
        require_analyst_evidence=require_analyst_evidence,
        evidence_context=evidence_context,
        require_analyst_tool=require_analyst_tool,
    )


def _collect_runtime_config_keys(value: Any) -> set[str]:
    """Return every dict key that appears anywhere in the runtime_config tree.

    Used by Stage 2 to determine whether a required_diagnostic name is
    referenced in the compiled config's diagnostic surface, even when the
    compiler did not turn it into a structured spec.
    """
    keys: set[str] = set()

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            for k, v in node.items():
                if isinstance(k, str):
                    keys.add(k)
                walk(v)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(value)
    return keys


def _detect_stage_2_hypothesis_config_misalignment(
    hypothesis: str,
    mechanism: str,
    config_changes: dict[str, Any],
    strategy_family: str,
) -> BehaviorSignal | None:
    score, explanation = check_hypothesis_alignment(
        hypothesis, mechanism, config_changes, family_name=strategy_family
    )
    if score >= ALIGNMENT_THRESHOLD:
        return None
    return BehaviorSignal(
        code="hypothesis_config_misalignment",
        confidence=1.0,
        severity="block",
        summary=f"Hypothesis-config misalignment (score={score:.2f}): {explanation}",
        evidence={"score": round(score, 4), "explanation": explanation},
    )


def _collect_stage_2_required_diagnostic_failures(
    contract: Any,
    runtime_config: dict[str, Any],
) -> list[BehaviorSignal]:
    required_diagnostics = list(getattr(contract, "required_diagnostics", []) or [])
    if not required_diagnostics:
        return []

    from diagnostic_contracts import normalize_diagnostic_requirement

    specs = list(getattr(contract, "required_diagnostic_specs", []) or [])
    resolved: set[str] = set()
    for spec in specs:
        spec_key = getattr(spec, "key", "") or ""
        if spec_key:
            resolved.add(spec_key)
        for alias in getattr(spec, "aliases", []) or []:
            if isinstance(alias, str) and alias:
                resolved.add(alias)

    runtime_keys = _collect_runtime_config_keys(runtime_config)
    missing: list[str] = []
    for name in required_diagnostics:
        if not isinstance(name, str) or not name.strip():
            continue
        normalized = normalize_diagnostic_requirement(name)
        if normalized and (
            normalized in resolved
            or name in resolved
            or name in runtime_keys
            or normalized in runtime_keys
        ):
            continue
        missing.append(name)

    if not missing:
        return []
    return [
        BehaviorSignal(
            code="required_diagnostic_missing_post_compile",
            confidence=1.0,
            severity="block",
            summary=(
                "Required diagnostics not present in compiled contract: "
                f"{missing}. Each name must appear as a key/alias in "
                "required_diagnostic_specs or as a key in runtime_config."
            ),
            evidence={"missing": missing},
        )
    ]


def validate_stage_2(contract: Any) -> Any:
    """Stage 2: post-compile validator. Operates on the compiled BacktestContract.

    Rules that need the resolved/normalized config live here:
      - hypothesis-config alignment scored against `contract.config_changes`
        so inherited baseline keys do not dilute a mismatched thesis delta
      - every entry in `thesis.required_diagnostics` (carried through to
        `contract.required_diagnostics`) must resolve to a real diagnostic in
        the compiled output — present in `contract.required_diagnostic_specs`
        (key or alias) OR referenced as a key in `contract.runtime_config`.

    Returns the contract on success; raises ThesisValidationError on violation.
    """
    if contract is None:
        return contract

    runtime_config = getattr(contract, "runtime_config", None) or {}
    config_changes = getattr(contract, "config_changes", None) or {}
    hypothesis = getattr(contract, "hypothesis", "") or ""
    mechanism = getattr(contract, "mechanism", "") or ""
    strategy_family = getattr(contract, "strategy_family", "") or ""

    signals: list[BehaviorSignal] = []
    if isinstance(config_changes, dict) and config_changes:
        try:
            signal = _detect_stage_2_hypothesis_config_misalignment(
                hypothesis, mechanism, config_changes, strategy_family
            )
        except MissingFamilyKeyConceptsError as exc:
            # Operator-visible configuration error, not a thesis-quality issue.
            # Raised as a validation error so the harness halts the affected
            # job loudly instead of silently letting every thesis pass.
            raise ThesisValidationError(
                f"Cannot enforce Stage 2 hypothesis-config alignment: {exc}. "
                f"Populate FamilyResearchSpec.key_concepts for family "
                f"'{strategy_family}' to enable alignment scoring.",
                rejection_code="hypothesis_config_alignment_unconfigured",
                evidence={
                    "strategy_family": strategy_family,
                    "reason": str(exc),
                },
            ) from exc
        if signal is not None:
            signals.append(signal)

    decision = _policy_decide(signals)
    if decision.action == "reject":
        triggering = decision.triggering
        assert triggering is not None, "reject decisions must carry a triggering signal"
        raise ThesisValidationError(
            triggering.summary,
            rejection_code=triggering.code,
            evidence=dict(triggering.evidence),
            remediation_hint=_format_remediation(triggering.remediation),
        )

    mechanical_failures = _collect_stage_2_required_diagnostic_failures(
        contract,
        runtime_config if isinstance(runtime_config, dict) else {},
    )
    _raise_mechanical_batch(mechanical_failures)

    return contract
