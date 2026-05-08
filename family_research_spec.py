from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class FamilyResearchSpec:
    strategy_label: str
    one_thesis_label: str
    config_schema: str
    research_questions: tuple[str, ...]
    config_rules: tuple[str, ...]
    prompt_focus: tuple[str, ...]
    thesis_json_hint: str
    allowed_config_keys: frozenset[str]
    resolution_config_keys: tuple[str, ...] = ()


def get_family_research_spec(name: str) -> FamilyResearchSpec:
    from strategies import STRATEGIES

    return STRATEGIES[name].research_spec


def resolve_research_resolution_context(
    family_name: str, runtime_config: dict[str, Any] | None
) -> dict[str, Any]:
    spec = get_family_research_spec(family_name)
    resolved_minutes_by_key: dict[str, int] = {}
    runtime = runtime_config or {}
    for key in spec.resolution_config_keys:
        value = runtime.get(key)
        minutes = _coerce_positive_resolution_minutes(value)
        if minutes is None:
            continue
        resolved_minutes_by_key[key] = minutes

    minimum_supported = min(resolved_minutes_by_key.values()) if resolved_minutes_by_key else None
    return {
        "resolution_config_keys": list(spec.resolution_config_keys),
        "resolved_minutes_by_key": resolved_minutes_by_key,
        "minimum_supported_time_bucket_minutes": minimum_supported,
    }


def _coerce_positive_resolution_minutes(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value > 0 else None
    if isinstance(value, str):
        stripped = value.strip()
        if re.fullmatch(r"[1-9][0-9]*", stripped):
            return int(stripped)
    return None


def validate_family_config_changes(family_name: str, thesis: dict[str, Any]) -> dict[str, Any]:
    spec = get_family_research_spec(family_name)
    config_changes = thesis.get("config_changes") or {}
    invalid = sorted(set(config_changes) - spec.allowed_config_keys)
    if not invalid:
        return thesis
    sanitized = dict(thesis)
    sanitized["requires_code_change"] = True
    sanitized["invalid_config_keys"] = invalid
    sanitized["code_change_idea"] = sanitized.get("code_change_idea") or {
        "idea": f"{family_name} thesis requires unsupported runtime keys",
        "what_code_needs": f"Add {', '.join(invalid)} support to the {family_name} family compiler/runtime or reformulate the thesis.",
        "evidence": [f"Unsupported keys proposed for {family_name}: {', '.join(invalid)}"],
    }
    sanitized["config_changes"] = {}
    return sanitized
