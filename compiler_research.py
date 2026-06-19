from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

import yaml

from autoresearch_paths import promoted_baseline_path
from config_hash import _config_hash
from diagnostic_contracts import build_required_diagnostic_specs
from persistence_utils import write_text_atomic
from research_types import BacktestContract
from strategies import STRATEGIES
from strategy_family import load_family

if TYPE_CHECKING:
    from research_types import ResearchThesis


def _baseline_config_path_for_family(family_name: str) -> str:
    return f"configs/{load_family(family_name).base_config_filename}"


def _resolved_base_config_path(thesis: "ResearchThesis") -> str:
    baseline_path = _baseline_config_path_for_family(thesis.strategy_family)
    if thesis.base_contract_id:
        raise ValueError(
            f"Thesis '{thesis.thesis_id}' cannot set base_contract_id; "
            "research theses must start from the family baseline."
        )
    if thesis.base_config_path and thesis.base_config_path != baseline_path:
        raise ValueError(
            f"Thesis '{thesis.thesis_id}' cannot inherit non-baseline base_config_path "
            f"'{thesis.base_config_path}'; research theses must start from '{baseline_path}'."
        )
    return baseline_path


def _load_base_runtime_config(
    root: Path, thesis: "ResearchThesis", runtime_root: Path | None = None
) -> dict:
    base_path = _resolved_base_config_path(thesis)
    if thesis.base_config_path and thesis.base_config_path != base_path:
        raise ValueError(
            f"Base config path does not match family baseline for thesis "
            f"'{thesis.thesis_id}': {thesis.base_config_path}"
        )
    # Read the promoted overlay when one exists so research theses compound on the
    # latest validated baseline; the committed seed is the fallback. The logical
    # identity (base_path) stays the committed path for validation/hashing.
    path = root / base_path
    if runtime_root is not None:
        overlay = promoted_baseline_path(runtime_root, f"{thesis.strategy_family}_base.yaml")
        if overlay.exists():
            path = overlay
    if thesis.base_config_path and not path.exists():
        raise ValueError(
            f"Base config path does not exist for thesis '{thesis.thesis_id}': {base_path}"
        )
    if not path.exists():
        raise ValueError(
            f"Family baseline config is missing for thesis '{thesis.thesis_id}': {base_path}"
        )
    payload = yaml.safe_load(path.read_text()) or {}
    if not isinstance(payload, dict):
        raise ValueError(
            f"Base config must be a mapping for thesis '{thesis.thesis_id}': {base_path}"
        )
    runtime_config = payload.get("runtime_config", payload)
    if not isinstance(runtime_config, dict):
        raise ValueError(
            f"Base runtime config must be a mapping for thesis '{thesis.thesis_id}': {base_path}"
        )
    return dict(runtime_config)


def _needs_code_contract(
    thesis: "ResearchThesis",
    root: Path,
    *,
    artifact_root: Path | None = None,
    status: str = "needs_code",
    runtime_root: Path | None = None,
) -> "BacktestContract":
    family_name = thesis.strategy_family
    contract_id = thesis.thesis_id
    if artifact_root is None:
        raise ValueError("research round artifact_root is required for needs-code contracts")
    experiment_dir = artifact_root / "builder_request"
    experiment_dir.mkdir(parents=True, exist_ok=True)
    write_text_atomic(experiment_dir / "thesis.json", thesis.model_dump_json(indent=2) + "\n")
    contract = BacktestContract(
        contract_id=contract_id,
        thesis_id=thesis.thesis_id,
        strategy_family=family_name,
        baseline_config_path=_resolved_base_config_path(thesis),
        base_contract_id=thesis.base_contract_id,
        base_config_hash=_config_hash(_load_base_runtime_config(root, thesis, runtime_root)),
        runtime_config={},
        hypothesis=thesis.hypothesis,
        mechanism=thesis.mechanism,
        required_diagnostics=thesis.required_diagnostics,
        required_diagnostic_specs=build_required_diagnostic_specs(
            thesis.required_diagnostics,
            thesis.required_diagnostic_specs,
        ),
        missing_primitives=thesis.requested_primitives,
        status=status,
    )
    write_text_atomic(experiment_dir / "contract.json", contract.model_dump_json(indent=2) + "\n")
    return contract


def _compile_runtime_config_contract(
    thesis: "ResearchThesis",
    root: Path,
    runtime_config: dict,
    *,
    artifact_root: Path | None = None,
    runtime_root: Path | None = None,
) -> "BacktestContract":
    family_name = thesis.strategy_family
    contract_id = _config_hash(runtime_config)
    if artifact_root is None:
        raise ValueError("research round artifact_root is required for compiled contracts")
    experiment_dir = artifact_root
    experiment_dir.mkdir(parents=True, exist_ok=True)

    write_text_atomic(
        experiment_dir / "selected_thesis.json", thesis.model_dump_json(indent=2) + "\n"
    )
    write_text_atomic(
        experiment_dir / "selected_config.json", json.dumps(runtime_config, indent=2) + "\n"
    )
    contract = BacktestContract(
        contract_id=contract_id,
        thesis_id=thesis.thesis_id,
        strategy_family=family_name,
        baseline_config_path=_resolved_base_config_path(thesis),
        base_contract_id=thesis.base_contract_id,
        base_config_hash=_config_hash(_load_base_runtime_config(root, thesis, runtime_root)),
        runtime_config=runtime_config,
        config_changes=dict(thesis.config_changes),
        hypothesis=thesis.hypothesis,
        mechanism=thesis.mechanism,
        required_diagnostics=thesis.required_diagnostics,
        required_diagnostic_specs=build_required_diagnostic_specs(
            thesis.required_diagnostics,
            thesis.required_diagnostic_specs,
        ),
        missing_primitives=[],
        status="ready_to_run",
    )
    write_text_atomic(
        experiment_dir / "selected_contract.json", contract.model_dump_json(indent=2) + "\n"
    )
    return contract


def _runtime_config_for_registered_strategy(
    family_name: str, config_changes: dict, base_config: dict
) -> dict | None:
    strategy = STRATEGIES.get(family_name)
    if strategy is None:
        return None
    return {**base_config, **config_changes}


def _format_noop_config_change_error(thesis: "ResearchThesis", base_config: dict) -> str:
    unchanged_keys: list[str] = []
    for key, value in thesis.config_changes.items():
        if base_config.get(key) == value:
            unchanged_keys.append(f"{key}={value!r}")
    detail = (
        f" Unchanged keys: {', '.join(unchanged_keys)}."
        if unchanged_keys
        else " No config_changes were provided."
    )
    return (
        f"Thesis '{thesis.thesis_id}' rejected: config_changes did not change "
        f"runtime_config relative to base_config_path '{_resolved_base_config_path(thesis)}'."
        f"{detail}"
    )


def compile_research_thesis(
    thesis: "ResearchThesis",
    root: Path,
    *,
    artifact_root: Path | None = None,
    runtime_root: Path | None = None,
) -> "BacktestContract":
    """Convert a validated ResearchThesis into an BacktestContract.

    Creates round-scoped selected thesis/config/contract artifacts.

    The contract_id is a content hash of the runtime config.

    ``runtime_root`` redirects the base-config read to the promoted baseline
    overlay when one exists, so theses compound on the latest validated config.
    """
    family_name = thesis.strategy_family
    if not thesis.required_diagnostic_specs:
        thesis.required_diagnostic_specs = build_required_diagnostic_specs(
            thesis.required_diagnostics
        )

    if thesis.requires_code_change:
        return _needs_code_contract(
            thesis, root, artifact_root=artifact_root, runtime_root=runtime_root
        )

    base_config = _load_base_runtime_config(root, thesis, runtime_root)
    runtime_config = _runtime_config_for_registered_strategy(
        family_name, thesis.config_changes, base_config
    )
    if runtime_config is not None:
        if not runtime_config:
            return _needs_code_contract(
                thesis, root, artifact_root=artifact_root, runtime_root=runtime_root
            )
        if runtime_config == base_config:
            raise ValueError(_format_noop_config_change_error(thesis, base_config))
        violations = STRATEGIES[family_name].validate_runtime_config(runtime_config)
        if violations:
            unsupported = [message for message in violations if message.startswith("Unsupported ")]
            if unsupported:
                raise ValueError(
                    f"Unsupported config_changes keys for thesis '{thesis.thesis_id}': "
                    + "; ".join(unsupported)
                )
            raise ValueError(
                f"Config validation failed for thesis '{thesis.thesis_id}': "
                + "; ".join(violations)
            )
        return _compile_runtime_config_contract(
            thesis, root, runtime_config, artifact_root=artifact_root, runtime_root=runtime_root
        )

    raise ValueError(f"compile_research_thesis does not support family '{family_name}' yet")
