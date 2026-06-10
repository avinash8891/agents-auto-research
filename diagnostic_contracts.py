from __future__ import annotations

import json
import re
from typing import Any

from research_types import DiagnosticRequirementSpec


def normalize_diagnostic_requirement(requirement: str) -> str:
    lowered = requirement.strip().lower()
    if not lowered:
        return ""
    before_paren = lowered.split("(", 1)[0].strip()
    return re.sub(r"[^a-z0-9]+", "_", before_paren).strip("_")


_REGISTERED_SPECS: dict[str, DiagnosticRequirementSpec] = {}
ANALYSIS_ONLY_DIAGNOSTIC_PREFIXES = ("definition_check_", "implementation_")


def _register(spec: DiagnosticRequirementSpec) -> None:
    _REGISTERED_SPECS[spec.key] = spec
    for alias in spec.aliases:
        _REGISTERED_SPECS[alias] = spec


_register(
    DiagnosticRequirementSpec(
        key="max_drawdown_vs_base",
        surface="experiment_evaluation",
        payload_fields=[
            "candidate_max_drawdown",
            "base_max_drawdown",
            "delta_max_drawdown",
        ],
        aliases=[
            "max_drawdown_change_relative_to_base",
            "max_drawdown_versus_base",
        ],
        description=(
            "Compare candidate vs baseline max_drawdown after the run has baseline "
            "metrics available."
        ),
    )
)


def registered_diagnostic_spec(key_or_alias: str) -> DiagnosticRequirementSpec | None:
    normalized = normalize_diagnostic_requirement(key_or_alias)
    spec = _REGISTERED_SPECS.get(normalized)
    if spec is None:
        return None
    return DiagnosticRequirementSpec.model_validate(spec.model_dump())


def build_required_diagnostic_specs(
    required_diagnostics: list[str],
    existing_specs: list[dict[str, Any] | DiagnosticRequirementSpec] | None = None,
) -> list[DiagnosticRequirementSpec]:
    specs: list[DiagnosticRequirementSpec] = []
    seen: set[str] = set()
    for item in existing_specs or []:
        spec = (
            item
            if isinstance(item, DiagnosticRequirementSpec)
            else DiagnosticRequirementSpec.model_validate(item)
        )
        if spec.key not in seen:
            specs.append(spec)
            seen.add(spec.key)
    for requirement in required_diagnostics:
        if not isinstance(requirement, str):
            continue
        normalized = normalize_diagnostic_requirement(requirement)
        if not normalized or normalized in seen:
            continue
        if normalized.startswith(ANALYSIS_ONLY_DIAGNOSTIC_PREFIXES):
            continue
        registered = registered_diagnostic_spec(normalized)
        if registered is not None:
            specs.append(registered)
            seen.add(registered.key)
            continue
        specs.append(
            DiagnosticRequirementSpec(
                key=normalized,
                surface="strategy_diagnostics",
                description=requirement.strip(),
            )
        )
        seen.add(normalized)
    return specs


def enrich_required_diagnostics(
    specs: list[dict[str, Any] | DiagnosticRequirementSpec],
    *,
    baseline_metrics: dict[str, Any],
    candidate_metrics: dict[str, Any],
    strategy_diagnostics: dict[str, Any] | None = None,
) -> dict[str, Any]:
    enriched = dict(strategy_diagnostics or {})
    for item in specs:
        spec = (
            item
            if isinstance(item, DiagnosticRequirementSpec)
            else DiagnosticRequirementSpec.model_validate(item)
        )
        if spec.key == "max_drawdown_vs_base":
            b_md = baseline_metrics.get("max_drawdown")
            c_md = candidate_metrics.get("max_drawdown")
            if None in {b_md, c_md}:
                missing_inputs: list[str] = []
                if b_md is None:
                    missing_inputs.append("base_max_drawdown")
                if c_md is None:
                    missing_inputs.append("candidate_max_drawdown")
                enriched[spec.key] = {"missing_inputs": missing_inputs}
                continue
            enriched[spec.key] = {
                "candidate_max_drawdown": float(c_md),
                "base_max_drawdown": float(b_md),
                "delta_max_drawdown": round(float(c_md) - float(b_md), 6),
            }
    return enriched


def diagnostic_spec_debug_dump(specs: list[DiagnosticRequirementSpec]) -> str:
    return json.dumps([spec.model_dump() for spec in specs], indent=2, sort_keys=True)
