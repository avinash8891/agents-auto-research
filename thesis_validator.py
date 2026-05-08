"""Thesis validator — rejects vague, incomplete, or duplicate theses before compilation.

Bad conductor output should fail here, not waste a backtest run.

Three guardrails inspired by AlphaAgent (arxiv 2502.16789v2):
1. Config-key overlap detection — auto-reject theses that change the same config
   keys as a prior thesis (their AST subtree isomorphism equivalent).
2. Hypothesis-config alignment scoring — cheap LLM check that config_changes
   actually test the stated hypothesis (their c1/c2 consistency scoring).
3. Multi-variant probing — generate 3 configs per continuous param to separate
   "mechanism works" from "got lucky with value" (their multi-factor-per-hypothesis).
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
CONFIG_OVERLAP_IGNORED_KEYS = frozenset({"requires_engine_change"})
CONFIG_CHANGES_METADATA_KEYS = frozenset({"requires_code_change", "new_config_keys_needed"})
_MIN_EMERGENT_FIELD_CHARS = 40
_MIN_NOVEL_CONNECTION_CHARS = 40
_EMERGENT_REQUIRED_FIELDS = (
    "why_existing_dimensions_do_not_fit",
    "mechanism_family_definition",
    "expected_reuse_across_future_theses",
)
_ALLOWED_BASE_CONFIG_PREFIXES = ("configs/", "experiments/")
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
    """Raised when a thesis fails validation."""

    pass


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
    parts = [part for part in normalized.split("/") if part]
    is_allowed = normalized.startswith(_ALLOWED_BASE_CONFIG_PREFIXES) or (
        len(parts) >= 4 and parts[:2] == ["runtime", "jobs"] and parts[3] == "experiments"
    )
    if not is_allowed:
        raise ThesisValidationError(
            f"base_config_path '{path}' must be under configs/, experiments/, "
            "or runtime/jobs/<job>/experiments/"
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
        from experiment_db import ExperimentDB

        for db_path in sorted(root.glob("*_experiments.db")):
            db = ExperimentDB(db_path)
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
        if key in CONFIG_OVERLAP_IGNORED_KEYS:
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
    if _requires_explicit_base_config(thesis) and not thesis.base_config_path:
        raise ThesisValidationError(
            "base_config_path is required when a thesis builds on, preserves, "
            "or compounds a prior/best configuration"
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
