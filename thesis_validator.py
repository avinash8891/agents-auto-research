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

Multi-check contracts (e.g. underexplored_dimensions, thesis_specifies_change,
expected_effects) live in
dedicated private `_validate_<contract>(...)` helpers and feed the live
mechanical/behavioral collectors. `validate_research_thesis` is the only
entry point for full thesis validation.

When deciding whether to bundle two related checks into one helper, apply
this test:

    Do these checks have to run at different points in the
    overall fail-fast sequence?

If YES → split into separate helpers, collected at their respective positions.
Example: `_validate_expected_effects_present` runs before the research contract
collector, while metric-backing failures are collected later after disqualifier
checks. Bundling them regresses the expected mechanical failure ordering.

If NO → one helper owning both checks is fine, with structured `evidence`
when failure modes are independent.

Single-check contracts (thesis_id presence, hypothesis presence, etc.) stay
inline in the collector — extracting them into one-line helpers is noise.
"""

from __future__ import annotations

import ast
import json
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any, Final

from autoresearch_logging import get_logger
from autoresearch_runtime_paths import iter_family_backtest_db_paths
from behavior_signals import BehaviorSignal
from behavior_signals import decide as _policy_decide
from research_types import EMERGENT_MECHANISM_DIMENSION, ResearchThesis
from strategy_family import load_family

log = get_logger(__name__)

# Metrics the backtest engine always produces (no custom diagnostics needed)
BUILTIN_METRICS = {
    "profit_factor",
    "max_drawdown",
    "trade_count",
    "median_expectancy",
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


_MIN_EXPECTED_EFFECTS = 2
_MIN_EFFECT_RATIONALE_CHARS = 20
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
        # to the inheritance_blocked check in the mechanical config-validity
        # collector, which rejects ANY path that isn't the family baseline.


def _family_baseline_path(thesis: ResearchThesis) -> str:
    try:
        return load_family(thesis.strategy_family).baseline_config_path
    except ValueError as exc:
        raise ThesisValidationError(str(exc)) from exc


def _prior_thesis_details(prior: dict[str, Any]) -> dict[str, Any]:
    details = prior.get("thesis_details")
    return details if isinstance(details, dict) else {}


VALID_PROCESS_TOOLS: Final[frozenset[str]] = frozenset(
    {
        "list_round_results",
        "web_search",
    }
)


def _validate_process(
    tools_called: set[str] | frozenset[str], *, require_analyst_tool: bool = False
) -> BehaviorSignal | None:
    return _process_signal(tools_called, require_analyst_tool=require_analyst_tool)


def _process_signal(
    tools_called: set[str] | frozenset[str], *, require_analyst_tool: bool = False
) -> BehaviorSignal | None:
    missing = [tool for tool in sorted(VALID_PROCESS_TOOLS) if tool not in tools_called]
    if require_analyst_tool and "analyze_trades" not in tools_called:
        missing.append("analyze_trades")
    if not missing:
        return None
    return BehaviorSignal(
        code="process_missing_required_tools",
        confidence=1.0,
        severity="warn",
        summary=f"Process gate failed: required tools not called: {missing}",
        evidence={"missing_tools": missing, "tools_called": sorted(tools_called)},
        remediation=("Call the required research tools before submitting the thesis.",),
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
    evaluated. Additional metric-backed prediction checks live in the v2
    registered-prediction harvest path rather than a thesis-format helper.
    """
    if not thesis.expected_effects:
        raise ThesisValidationError(
            "Thesis has no expected_effects — cannot evaluate without predictions",
            rejection_code="structural_missing_expected_effects",
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


def _collect_research_contract_failures(thesis: ResearchThesis) -> list[BehaviorSignal]:
    failures: list[BehaviorSignal] = []

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
) -> list[BehaviorSignal]:
    failures = _collect_inline_structural_failures(thesis, prior_theses)
    failures.extend(_collect_from_validator(lambda: _validate_thesis_specifies_change(thesis)))
    failures.extend(_collect_from_validator(lambda: _validate_expected_effects_present(thesis)))
    failures.extend(_collect_research_contract_failures(thesis))
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
    require_analyst_tool: bool = False,
) -> ResearchThesis:
    """Validate a research thesis. Raises ThesisValidationError if invalid.

    Dispatches Stage 1 to four named sub-section helpers in fixed order:
    process → behavioral → mechanical.
    """
    if tools_called is not None:
        process_signal = _validate_process(tools_called, require_analyst_tool=require_analyst_tool)
        process_decision = _policy_decide([process_signal] if process_signal is not None else [])
        if process_decision.action == "reject":
            triggering = process_decision.triggering
            assert triggering is not None, "reject decisions must carry a triggering signal"
            raise ThesisValidationError(
                triggering.summary,
                rejection_code=triggering.code,
                evidence=dict(triggering.evidence),
                remediation_hint=_format_remediation(triggering.remediation),
            )
        for warning in process_decision.warnings:
            log.warning(
                "thesis accepted with warning code=%s summary=%s | remediation=%s",
                warning.code,
                warning.summary,
                _format_remediation(warning.remediation),
            )
    _run_behavioral_pass(thesis, prior_theses)
    mechanical_failures = _collect_mechanical_failures(thesis, prior_theses)
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
        require_analyst_tool=require_analyst_tool,
    )
