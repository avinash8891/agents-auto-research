"""Thesis compilation pipeline.

Converts raw research theses into executable config artifacts:
  1. Operationalization — resolves ambiguous terms into exact contracts
  2. Compilation — contract → runtime config via family-specific compiler
  3. Artifact creation — writes proposal, compilation, queue files
  4. Builder dispatch — implements missing primitives via CLI

Migrated from research_subagent.py — all thesis-to-config logic lives here.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

from artifact_store import timestamp_ms, write_json_artifact
from compiler_defaults import (
    _get_ema_defaults,
    _get_orb_defaults,
)
from family_research import (
    infer_family_from_dir_name,
    validate_family_config_changes,
)
from orb_contract import (
    compile_contract,
    render_contract_to_runtime_config,
    resolve_contract_support,
)
from strategy_family import load_family

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------


if TYPE_CHECKING:
    from research_types import ExperimentContract, ResearchThesis

AMBIGUOUS_PATTERNS = {
    "stocks_in_play": ("stocks in play", "stocks-in-play", "stocks_in_play"),
    "narrow_or": ("narrow or", "narrow-or", "narrow_or", "narrow opening range"),
    "wide_or": ("wide or", "wide-or", "wide_or", "wide opening range"),
}


def validate_orb_runtime_config(config: dict[str, Any]) -> list[str]:
    """Validate ORB runtime config. Returns list of violations (empty = OK)."""
    violations: list[str] = []
    or_min = config.get("or_minutes", 30)
    if not (5 <= or_min <= 120):
        violations.append(f"or_minutes={or_min} out of range [5, 120]")
    tf = config.get("timeframe_minutes", 5)
    if tf not in (1, 2, 5, 10, 15, 30):
        violations.append(f"timeframe_minutes={tf} not in {{1,2,5,10,15,30}}")
    rr = config.get("rr_ratio", 2.0)
    if not (0.5 <= rr <= 20):
        violations.append(f"rr_ratio={rr} out of range [0.5, 20]")
    mhb = config.get("max_hold_bars", 78)
    if not (1 <= mhb <= 200):
        violations.append(f"max_hold_bars={mhb} out of range [1, 200]")
    slip = config.get("slippage_pct", 0.05)
    if not (0 <= slip <= 1.0):
        violations.append(f"slippage_pct={slip} out of range [0, 1.0]")
    return violations


def validate_ema_runtime_config(config: dict[str, Any]) -> list[str]:
    """Validate EMA runtime config values. Returns list of violations (empty = OK).

    Hard constraints: reject strategically nonsensical configs before they
    waste a backtest run. Grounded in transcript rules and market structure.
    """
    violations: list[str] = []

    # --- EMA length ---
    ema = config.get("ema_length")
    if ema is not None:
        if not isinstance(ema, (int, float)) or int(ema) < 2:
            violations.append(f"ema_length={ema}: must be >= 2 (EMA of 1 is just price)")
        if isinstance(ema, (int, float)) and int(ema) > 200:
            violations.append(f"ema_length={ema}: must be <= 200 (intraday bars, not daily)")

    # --- Risk-reward ratio ---
    rr = config.get("rr_ratio")
    if rr is not None:
        if not isinstance(rr, (int, float)) or float(rr) < 0.5:
            violations.append(
                f"rr_ratio={rr}: must be >= 0.5 (below 0.5 is guaranteed negative edge)"
            )
        if isinstance(rr, (int, float)) and float(rr) > 20:
            violations.append(f"rr_ratio={rr}: must be <= 20 (unreachable targets waste entries)")

    # --- Timeframes ---
    tf_short = config.get("timeframe_short")
    tf_long = config.get("timeframe_long")
    if tf_short is not None:
        if not isinstance(tf_short, (int, float)) or int(tf_short) < 1:
            violations.append(f"timeframe_short={tf_short}: must be >= 1 minute")
        if isinstance(tf_short, (int, float)) and int(tf_short) > 60:
            violations.append(f"timeframe_short={tf_short}: must be <= 60 minutes for intraday")
    if tf_long is not None:
        if not isinstance(tf_long, (int, float)) or int(tf_long) < 1:
            violations.append(f"timeframe_long={tf_long}: must be >= 1 minute")
        if isinstance(tf_long, (int, float)) and int(tf_long) > 60:
            violations.append(f"timeframe_long={tf_long}: must be <= 60 minutes for intraday")
    if tf_short is not None and tf_long is not None:
        if isinstance(tf_short, (int, float)) and isinstance(tf_long, (int, float)):
            if int(tf_short) > int(tf_long):
                violations.append(
                    f"timeframe_short={tf_short} > timeframe_long={tf_long}: "
                    f"short TF must be <= long TF"
                )

    # --- Max trades per day ---
    mtpd = config.get("max_trades_per_day")
    if mtpd is not None:
        if isinstance(mtpd, (int, float)) and int(mtpd) < 1:
            violations.append(f"max_trades_per_day={mtpd}: must be >= 1 (0 disables trading)")
        if isinstance(mtpd, (int, float)) and int(mtpd) > 20:
            violations.append(
                f"max_trades_per_day={mtpd}: must be <= 20 "
                f"(transcript says 3-5; >20 effectively disables the cap)"
            )

    # --- Direction bias ---
    bias = config.get("direction_bias")
    VALID_BIASES = {"long_only", "short_only", "both"}
    if bias is not None and bias not in VALID_BIASES:
        violations.append(f"direction_bias='{bias}': must be one of {sorted(VALID_BIASES)}")

    # --- Entry cutoff time ---
    cutoff = config.get("entry_cutoff_time")
    if cutoff is not None and isinstance(cutoff, str):
        try:
            parts = cutoff.split(":")
            hour, minute = int(parts[0]), int(parts[1])
            cutoff_minutes = hour * 60 + minute
            market_open = 9 * 60 + 30  # 09:30
            market_close = 16 * 60  # 16:00
            if cutoff_minutes < market_open:
                violations.append(f"entry_cutoff_time='{cutoff}': before market open (09:30)")
            if cutoff_minutes >= market_close:
                violations.append(f"entry_cutoff_time='{cutoff}': at or after market close (16:00)")
        except (ValueError, IndexError):
            violations.append(f"entry_cutoff_time='{cutoff}': invalid time format (use HH:MM)")

    # --- Gap filter ---
    gap_pct = config.get("gap_pct")
    if gap_pct is not None and isinstance(gap_pct, (int, float)):
        if float(gap_pct) <= 0:
            violations.append(f"gap_pct={gap_pct}: must be > 0")
        if float(gap_pct) > 0.20:
            violations.append(
                f"gap_pct={gap_pct}: must be <= 0.20 (20%; higher filters out everything)"
            )

    # --- Range shift ---
    rsl = config.get("range_shift_lookback")
    if rsl is not None:
        if not (5 <= int(rsl) <= 100):
            violations.append(f"range_shift_lookback={rsl}: must be between 5 and 100")

    return violations


# ---------------------------------------------------------------------------
# Core compilation: proposal → compilation artifact → contract + queue
# ---------------------------------------------------------------------------


def _config_content_hash(config: dict[str, Any]) -> str:
    """Deterministic hash of a config dict. Used as run_id and filename."""
    import hashlib

    blob = json.dumps(config, sort_keys=True).encode()
    return hashlib.sha256(blob).hexdigest()[:12]


def compile_ema_thesis(
    thesis_id: str,
    config_changes: dict[str, Any],
    root: Path,
) -> dict[str, Any]:
    """Compile EMA thesis: validate + merge with baseline + write JSON.

    Two steps:
      1. Validate config_changes keys against allowed set
      2. runtime_config = {**ema_base_yaml, **config_changes}, write JSON

    Filenames use config_hash (deterministic content hash), not thesis_id.
    thesis_id is stored as metadata inside the file for human readability.
    Identical configs get the same hash = free dedup.

    Returns {"status": ..., "config_path": ..., "runtime_config": ..., "config_hash": ...}
    """
    family = load_family("ema")
    defaults = _get_ema_defaults()
    allowed = set(defaults.keys())
    invalid_keys = sorted(set(config_changes.keys()) - allowed)
    if invalid_keys:
        return {
            "status": "requires_code_change",
            "invalid_keys": invalid_keys,
            "config_path": None,
            "runtime_config": {},
        }

    runtime_config = {**defaults, **config_changes}

    # Hard constraints: reject strategically nonsensical configs
    violations = validate_ema_runtime_config(runtime_config)
    if violations:
        return {
            "status": "rejected_at_compile",
            "violations": violations,
            "config_path": None,
            "runtime_config": runtime_config,
        }

    config_hash = _config_content_hash(runtime_config)

    contracts_dir = root / family.contracts_dirname
    contracts_dir.mkdir(parents=True, exist_ok=True)
    config_path = contracts_dir / f"{config_hash}.json"

    # Skip write if identical config already exists (dedup)
    if not config_path.exists():
        config_path.write_text(json.dumps(runtime_config, indent=2) + "\n")

    # Write queue entry (also keyed by hash)
    run_queue_dir = root / family.run_queue_dirname
    run_queue_dir.mkdir(parents=True, exist_ok=True)
    write_json_artifact(
        run_queue_dir / f"{config_hash}.json",
        {
            "queue_id": config_hash,
            "thesis_id": thesis_id,
            "status": "pending",
            "config": config_path.relative_to(root).as_posix(),
            "timestamp": timestamp_ms(),
        },
    )
    return {
        "status": "ready_to_run",
        "config_path": config_path.relative_to(root).as_posix(),
        "config_hash": config_hash,
        "runtime_config": runtime_config,
    }


def compile_proposal_artifact(
    proposal: dict[str, Any],
    root: Path,
) -> dict[str, Any]:
    """Compile a thesis proposal into a family-specific runtime artifact."""
    thesis_id = proposal["thesis_id"]
    family_name = proposal.get("strategy_family", "orb")
    family = load_family(family_name)
    contract = proposal.get("primitive_contract", [])
    if family_name == "ema":
        from ema_contract import compile_ema_contract

        result = compile_ema_contract(contract)
    else:
        result = compile_contract(contract)

    compilations_dir = root / family.compilations_dirname
    run_queue_dir = root / family.run_queue_dirname
    contracts_dir = root / family.contracts_dirname

    compilation_payload = {
        "thesis_id": thesis_id,
        "family": family_name,
        "status": result.status,
        "runtime_config": result.runtime_config,
        "missing_primitives": result.missing_primitives,
        "normalized_contract": result.normalized_contract,
        "timestamp": timestamp_ms(),
    }
    write_json_artifact(compilations_dir / f"{thesis_id}.json", compilation_payload)

    if result.status != "ready_to_run":
        return compilation_payload

    contract_path = contracts_dir / f"{thesis_id}.json"
    contract_path.parent.mkdir(parents=True, exist_ok=True)
    contract_path.write_text(json.dumps(result.normalized_contract, indent=2) + "\n")

    queue_payload = {
        "queue_id": thesis_id,
        "thesis_id": thesis_id,
        "status": "pending",
        "config": contract_path.relative_to(root).as_posix(),
        "timestamp": timestamp_ms(),
    }
    write_json_artifact(run_queue_dir / f"{thesis_id}.json", queue_payload)
    return compilation_payload


# ---------------------------------------------------------------------------
# Operationalization: ambiguous thesis → exact contract
# ---------------------------------------------------------------------------


def thesis_needs_operationalization(thesis: dict[str, Any]) -> bool:
    """Check if a thesis contains ambiguous terms needing resolution."""
    haystack = " ".join(str(thesis.get(key, "")) for key in ("hypothesis", "mechanism")).lower()
    return any(term in haystack for terms in AMBIGUOUS_PATTERNS.values() for term in terms)


def finalize_thesis_config_changes(
    thesis: dict[str, Any], clarification: dict[str, Any]
) -> dict[str, Any]:
    """Merge operationalization clarification into a thesis.

    Takes the resolved contract from the clarification agent and
    produces a finalized thesis with concrete config_changes.
    """
    finalized = dict(thesis)
    finalized["operationalization_reasoning"] = clarification.get("reasoning", "")
    primitive_contract = clarification.get("resolved_contract")
    resolved_changes = clarification.get("resolved_changes") or {}
    if primitive_contract is None:
        if resolved_changes:
            finalized["primitive_contract"] = thesis.get("primitive_contract", [])
            normalized = dict(resolved_changes)
            if normalized.get("require_regimes") == ["narrow-or"]:
                normalized["require_regimes"] = ["narrow-OR"]
            finalized["config_changes"] = normalized
            finalized["requires_code_change"] = clarification.get("requires_code_change", False)
            if finalized["requires_code_change"]:
                finalized["missing_primitives"] = clarification.get("missing_primitives", [])
                finalized["code_change_idea"] = clarification.get("code_change_idea")
                finalized["config_changes"] = {}
            return finalized
        primitive_contract = []
    finalized["primitive_contract"] = primitive_contract
    support = resolve_contract_support(finalized["primitive_contract"])
    renderable = True
    try:
        rendered_config = render_contract_to_runtime_config(finalized["primitive_contract"])
    except (KeyError, TypeError, ValueError):
        renderable = False
        rendered_config = {}
    if clarification.get("requires_code_change"):
        finalized["requires_code_change"] = True
        finalized["config_changes"] = {}
        finalized["missing_primitives"] = (
            clarification.get("missing_primitives") or support["missing_primitive_types"]
        )
        finalized["code_change_idea"] = clarification.get("code_change_idea")
        return finalized

    finalized["requires_code_change"] = (not support["supported"]) or (not renderable)
    finalized["missing_primitives"] = support["missing_primitive_types"]
    finalized["config_changes"] = rendered_config if renderable and support["supported"] else {}
    if finalized["requires_code_change"] and not finalized.get("code_change_idea"):
        finalized["code_change_idea"] = clarification.get("code_change_idea")
    return finalized


def operationalize_thesis(thesis: dict[str, Any]) -> dict[str, Any]:
    """Convert a potentially ambiguous thesis into an executable contract.

    For EMA theses with config_changes, maps them to primitive_contract entries.
    For ORB theses with config_changes, wraps as config_changes_passthrough.
    For ambiguous theses (detected by AMBIGUOUS_PATTERNS), runs an SDK
    operationalization agent to resolve the ambiguity.
    For clear theses with primitive_contract, renders to runtime config directly.
    """
    # Direct config_changes → primitive_contract mapping
    print(
        f"OPERATIONALIZE: config_changes={bool(thesis.get('config_changes'))} needs_op={thesis_needs_operationalization(thesis)} has_pc={bool(thesis.get('primitive_contract'))}"
    )
    if thesis.get("config_changes"):
        if thesis.get("strategy_family") == "ema":
            config_changes = {**_get_ema_defaults(), **thesis["config_changes"]}
            primitive_contract: list[dict[str, Any]] = []
            if "ema_length" in config_changes:
                primitive_contract.append(
                    {"type": "ema_length", "value": config_changes["ema_length"]}
                )
            if "timeframe_long" in config_changes:
                primitive_contract.append(
                    {"type": "timeframe_long", "minutes": config_changes["timeframe_long"]}
                )
            if "timeframe_short" in config_changes:
                primitive_contract.append(
                    {"type": "timeframe_short", "minutes": config_changes["timeframe_short"]}
                )
            if "rr_ratio" in config_changes:
                primitive_contract.append(
                    {"type": "risk_reward", "rr_ratio": config_changes["rr_ratio"]}
                )
            if "direction_bias" in config_changes:
                primitive_contract.append(
                    {"type": "direction_bias", "value": config_changes["direction_bias"]}
                )
            if "entry_cutoff_time" in config_changes:
                primitive_contract.append(
                    {"type": "entry_cutoff", "time": config_changes["entry_cutoff_time"]}
                )
            if "max_trades_per_day" in config_changes:
                primitive_contract.append(
                    {"type": "max_trades_per_day", "value": config_changes["max_trades_per_day"]}
                )
            thesis["primitive_contract"] = thesis.get("primitive_contract") or primitive_contract
        else:
            thesis["primitive_contract"] = thesis.get("primitive_contract") or [
                {"type": "config_changes_passthrough", "config_changes": thesis["config_changes"]}
            ]
        thesis["requires_code_change"] = thesis.get("requires_code_change", False)
        return thesis

    # Clear thesis — just render the contract
    if not thesis_needs_operationalization(thesis):
        thesis["primitive_contract"] = thesis.get("primitive_contract", [])
        thesis["config_changes"] = render_contract_to_runtime_config(thesis["primitive_contract"])
        return thesis

    # Ambiguous thesis — use SDK operationalization agent
    clarification = _run_operationalization_agent(thesis)
    return finalize_thesis_config_changes(thesis, clarification)


def _run_operationalization_agent(thesis: dict[str, Any]) -> dict[str, Any]:
    """Run SDK agent to resolve ambiguous thesis into exact contract.

    Returns a clarification dict with resolved_changes/resolved_contract.
    Falls back to empty resolution if agent unavailable.
    """
    try:
        import asyncio

        from claude_agent_sdk import AgentDefinition

        from agent_orchestrator import _run_single_agent

        agent_def = AgentDefinition(
            description="Resolves ambiguous trading theses into exact executable contracts.",
            prompt=_build_operationalization_prompt(thesis),
            tools=[],
            model="sonnet",
            maxTurns=3,
        )

        result = asyncio.run(
            _run_single_agent(
                "operationalization-agent",
                f"Operationalize this thesis: {json.dumps(thesis, indent=2)}",
                agent_def,
            )
        )

        if result:
            return result

        print(f"OPERATIONALIZE: agent returned None for {thesis.get('thesis_id')}")
        return {
            "resolved_changes": {},
            "requires_code_change": True,
            "missing_primitives": ["operationalization_failed"],
            "code_change_idea": f"Agent could not operationalize '{thesis.get('thesis_id')}'",
        }

    except Exception as exc:
        print(f"OPERATIONALIZE: SDK error for {thesis.get('thesis_id')}: {exc}")
        return {
            "resolved_changes": {},
            "requires_code_change": True,
            "missing_primitives": ["operationalization_error"],
            "code_change_idea": str(exc),
        }


def _build_operationalization_prompt(thesis: dict[str, Any]) -> str:
    """Build the prompt for the operationalization agent."""
    return f"""You are a trading strategy contract resolver. Your job is to convert
ambiguous thesis descriptions into exact, executable contracts.

THESIS:
{json.dumps({
    "thesis_id": thesis.get("thesis_id"),
    "hypothesis": thesis.get("hypothesis"),
    "family": thesis.get("strategy_family", thesis.get("family")),
    "mechanism": thesis.get("mechanism"),
}, indent=2)}

TASK:
1. Identify every ambiguous term in the thesis/mechanism.
2. For each ambiguous term, resolve to an exact contract:
   - measurement variable
   - ranking method or baseline
   - lookback/window
   - operator
   - threshold/top-N
   - hard gate vs score
3. If the contract cannot be expressed with standard config keys,
   set requires_code_change=true and describe the missing primitive.

Return a JSON object:
{{
  "resolved_changes": {{"config-like fields if directly expressible": "value"}},
  "reasoning": "the exact contract and why",
  "requires_code_change": false,
  "code_change_idea": null
}}

If code change needed:
{{
  "resolved_changes": {{}},
  "reasoning": "explanation",
  "requires_code_change": true,
  "missing_primitives": ["primitive_name"],
  "code_change_idea": "what needs implementing"
}}

Return ONLY the JSON object."""


# ---------------------------------------------------------------------------
# Research thesis → ExperimentContract (new structured path)
# ---------------------------------------------------------------------------


def compile_research_thesis(
    thesis: "ResearchThesis",
    root: Path,
) -> "ExperimentContract":
    """Convert a validated ResearchThesis into an ExperimentContract.

    Creates:
      experiments/{experiment_id}/thesis.json
      experiments/{experiment_id}/contract.json
      experiments/{experiment_id}/runtime_config.json

    The experiment_id is a content hash of the runtime config.
    """
    from research_types import ExperimentContract

    family_name = thesis.strategy_family

    if thesis.requires_code_change:
        experiment_id = thesis.thesis_id
        experiment_dir = root / "experiments" / experiment_id
        experiment_dir.mkdir(parents=True, exist_ok=True)
        (experiment_dir / "thesis.json").write_text(thesis.model_dump_json(indent=2) + "\n")
        contract = ExperimentContract(
            experiment_id=experiment_id,
            thesis_id=thesis.thesis_id,
            strategy_family=family_name,
            baseline_config_path=f"configs/{load_family(family_name).base_config_filename}",
            runtime_config={},
            hypothesis=thesis.hypothesis,
            mechanism=thesis.mechanism,
            expected_effects=thesis.expected_effects,
            disqualifiers=thesis.disqualifiers,
            required_diagnostics=thesis.required_diagnostics,
            status="needs_code",
        )
        (experiment_dir / "contract.json").write_text(contract.model_dump_json(indent=2) + "\n")
        return contract

    if family_name == "ema":
        defaults = _get_ema_defaults()
        allowed = set(defaults.keys())
        invalid_keys = sorted(set(thesis.config_changes.keys()) - allowed)

        if invalid_keys:
            experiment_id = thesis.thesis_id
            experiment_dir = root / "experiments" / experiment_id
            experiment_dir.mkdir(parents=True, exist_ok=True)
            (experiment_dir / "thesis.json").write_text(thesis.model_dump_json(indent=2) + "\n")
            contract = ExperimentContract(
                experiment_id=experiment_id,
                thesis_id=thesis.thesis_id,
                strategy_family=family_name,
                baseline_config_path=f"configs/{load_family(family_name).base_config_filename}",
                runtime_config={},
                hypothesis=thesis.hypothesis,
                mechanism=thesis.mechanism,
                expected_effects=thesis.expected_effects,
                disqualifiers=thesis.disqualifiers,
                required_diagnostics=thesis.required_diagnostics,
                status="needs_code",
            )
            (experiment_dir / "contract.json").write_text(contract.model_dump_json(indent=2) + "\n")
            return contract

        runtime_config = {**defaults, **thesis.config_changes}

        # Hard constraints: reject strategically nonsensical configs
        violations = validate_ema_runtime_config(runtime_config)
        if violations:
            raise ValueError(
                f"Config validation failed for thesis '{thesis.thesis_id}': "
                + "; ".join(violations)
            )

        experiment_id = _config_content_hash(runtime_config)
        experiment_dir = root / "experiments" / experiment_id
        experiment_dir.mkdir(parents=True, exist_ok=True)

        (experiment_dir / "thesis.json").write_text(thesis.model_dump_json(indent=2) + "\n")
        (experiment_dir / "runtime_config.json").write_text(
            json.dumps(runtime_config, indent=2) + "\n"
        )
        contract = ExperimentContract(
            experiment_id=experiment_id,
            thesis_id=thesis.thesis_id,
            strategy_family=family_name,
            baseline_config_path=f"configs/{load_family(family_name).base_config_filename}",
            runtime_config=runtime_config,
            hypothesis=thesis.hypothesis,
            mechanism=thesis.mechanism,
            expected_effects=thesis.expected_effects,
            disqualifiers=thesis.disqualifiers,
            required_diagnostics=thesis.required_diagnostics,
            status="ready_to_run",
        )
        (experiment_dir / "contract.json").write_text(contract.model_dump_json(indent=2) + "\n")
        return contract

    if family_name == "orb":
        defaults = _get_orb_defaults()
        allowed = set(defaults.keys())
        invalid_keys = sorted(set(thesis.config_changes.keys()) - allowed)

        if invalid_keys:
            experiment_id = thesis.thesis_id
            experiment_dir = root / "experiments" / experiment_id
            experiment_dir.mkdir(parents=True, exist_ok=True)
            (experiment_dir / "thesis.json").write_text(thesis.model_dump_json(indent=2) + "\n")
            contract = ExperimentContract(
                experiment_id=experiment_id,
                thesis_id=thesis.thesis_id,
                strategy_family=family_name,
                baseline_config_path=f"configs/{load_family(family_name).base_config_filename}",
                runtime_config={},
                hypothesis=thesis.hypothesis,
                mechanism=thesis.mechanism,
                expected_effects=thesis.expected_effects,
                disqualifiers=thesis.disqualifiers,
                required_diagnostics=thesis.required_diagnostics,
                status="needs_code",
            )
            (experiment_dir / "contract.json").write_text(contract.model_dump_json(indent=2) + "\n")
            return contract

        runtime_config = {**defaults, **thesis.config_changes}

        violations = validate_orb_runtime_config(runtime_config)
        if violations:
            raise ValueError(
                f"Config validation failed for thesis '{thesis.thesis_id}': "
                + "; ".join(violations)
            )

        experiment_id = _config_content_hash(runtime_config)
        experiment_dir = root / "experiments" / experiment_id
        experiment_dir.mkdir(parents=True, exist_ok=True)

        (experiment_dir / "thesis.json").write_text(thesis.model_dump_json(indent=2) + "\n")
        (experiment_dir / "runtime_config.json").write_text(
            json.dumps(runtime_config, indent=2) + "\n"
        )
        contract = ExperimentContract(
            experiment_id=experiment_id,
            thesis_id=thesis.thesis_id,
            strategy_family=family_name,
            baseline_config_path=f"configs/{load_family(family_name).base_config_filename}",
            runtime_config=runtime_config,
            hypothesis=thesis.hypothesis,
            mechanism=thesis.mechanism,
            expected_effects=thesis.expected_effects,
            disqualifiers=thesis.disqualifiers,
            required_diagnostics=thesis.required_diagnostics,
            status="ready_to_run",
        )
        (experiment_dir / "contract.json").write_text(contract.model_dump_json(indent=2) + "\n")
        return contract

    raise ValueError(f"compile_research_thesis does not support family '{family_name}' yet")


# ---------------------------------------------------------------------------
# Legacy artifact creation (kept for ORB compatibility)
# ---------------------------------------------------------------------------


def create_executable_artifact(
    thesis_dir: Path,
    base_config_path: Path,
    thesis: dict[str, Any],
    root: Path,
) -> dict[str, Any]:
    """Create executable config from a thesis.

    EMA: validate config_changes keys, merge with baseline, write JSON.
    ORB: operationalize + primitive_contract compilation (legacy path).

    Returns {"generated_config": str|None, "generated_config_needs_build": bool, ...}.
    """
    thesis["strategy_family"] = thesis.get("strategy_family") or infer_family_from_dir_name(
        thesis_dir.name
    )
    thesis_id = thesis.get("thesis_id", "")
    family_name = thesis["strategy_family"]

    family = load_family(family_name)

    if thesis.get("requires_code_change"):
        # Save proposal for human review (use thesis_id here since no config to hash)
        proposals_dir = root / family.proposals_dirname
        write_json_artifact(
            proposals_dir / f"{thesis_id}.json",
            {
                "thesis_id": thesis_id,
                "hypothesis": thesis.get("hypothesis", ""),
                "strategy_family": family_name,
                "mechanism": thesis.get("mechanism", ""),
                "config_changes": thesis.get("config_changes", {}),
                "requires_code_change": True,
                "evidence": thesis.get("evidence", []),
                "timestamp": timestamp_ms(),
            },
        )
        return {
            "generated_config": None,
            "generated_config_needs_build": True,
            "generated_thesis_id": thesis_id,
        }

    if family_name == "ema":
        # Simple path: validate + merge + write JSON
        config_changes = thesis.get("config_changes", {})
        result = compile_ema_thesis(thesis_id, config_changes, root)
        if result["status"] != "ready_to_run":
            return {
                "generated_config": None,
                "generated_config_needs_build": True,
                "generated_thesis_id": thesis_id,
            }
        # Save proposal keyed by config_hash (not thesis_id)
        config_hash = result["config_hash"]
        proposals_dir = root / family.proposals_dirname
        write_json_artifact(
            proposals_dir / f"{config_hash}.json",
            {
                "thesis_id": thesis_id,
                "config_hash": config_hash,
                "hypothesis": thesis.get("hypothesis", ""),
                "strategy_family": family_name,
                "mechanism": thesis.get("mechanism", ""),
                "config_changes": thesis.get("config_changes", {}),
                "evidence": thesis.get("evidence", []),
                "timestamp": timestamp_ms(),
            },
        )
        return {
            "generated_config": result["config_path"],
            "generated_config_needs_build": False,
            "generated_thesis_id": thesis_id,
        }

    # ORB: legacy operationalization + primitive_contract path
    thesis = validate_family_config_changes(family_name, thesis)
    thesis = operationalize_thesis(dict(thesis))
    proposal = {
        "thesis_id": thesis_id,
        "strategy_family": family_name,
        "primitive_contract": thesis.get("primitive_contract", []),
        "timestamp": timestamp_ms(),
    }
    compilation = compile_proposal_artifact(proposal, root)
    if compilation["status"] != "ready_to_run":
        return {
            "generated_config": None,
            "generated_config_needs_build": True,
            "generated_thesis_id": thesis_id,
        }
    return {
        "generated_config": f"{family.contracts_dirname}/{thesis_id}.json",
        "generated_config_needs_build": False,
        "generated_thesis_id": thesis_id,
    }


def derive_thesis_artifacts(
    thesis_dir: Path,
    base_config_path: Path,
    parsed: dict[str, Any],
    root: Path,
) -> list[str]:
    """Create thesis artifacts + configs from research findings.

    Iterates over suggested_theses in parsed, validates, operationalizes,
    and compiles each. Returns list of generated config paths.
    """
    suggested = parsed.get("suggested_theses", [])
    if not suggested:
        return []

    generated: list[str] = []
    for thesis in suggested:
        thesis.setdefault("strategy_family", "ema" if "ema" in thesis_dir.name else "orb")
        thesis = validate_family_config_changes(thesis["strategy_family"], thesis)
        result = create_executable_artifact(thesis_dir, base_config_path, thesis, root)
        if result.get("generated_config"):
            generated.append(result["generated_config"])

    return generated


# ---------------------------------------------------------------------------
# Research artifact I/O
# ---------------------------------------------------------------------------


def write_research_artifact(
    research_dir: Path,
    request: dict[str, Any],
    parsed: dict[str, Any],
    raw_output: str,
    *,
    research_mode: str = "grounded",
    external_research_attempted: bool = True,
    external_research_attempts: int = 1,
    fallback_reason: str | None = None,
) -> Path:
    """Write completed research artifact to disk."""
    research_dir.mkdir(parents=True, exist_ok=True)
    request_id = request.get("request_id", f"research-{int(time.time())}")

    artifact = {
        "request_id": request_id,
        "status": "completed",
        "timestamp": int(time.time() * 1000),
        "research_mode": research_mode,
        "external_research_attempted": external_research_attempted,
        "external_research_attempts": external_research_attempts,
        "findings": parsed.get("findings", []),
        "suggested_theses": parsed.get("suggested_theses", []),
        "sources": parsed.get("sources", []),
    }
    if fallback_reason:
        artifact["fallback_reason"] = fallback_reason

    artifact_path = research_dir / f"{request_id}-findings.json"
    artifact_path.write_text(json.dumps(artifact, indent=2) + "\n")

    raw_path = research_dir / f"{request_id}-raw.txt"
    raw_path.write_text(raw_output)

    return artifact_path


def mark_request_completed(request_path: Path) -> None:
    """Mark a research request as completed."""
    payload = json.loads(request_path.read_text())
    payload["status"] = "completed"
    payload["completed_at"] = int(time.time() * 1000)
    request_path.write_text(json.dumps(payload, indent=2) + "\n")


# ---------------------------------------------------------------------------
# Builder dispatch: implements missing primitives via CLI
# ---------------------------------------------------------------------------


def _find_cli() -> str | None:
    """Find claude or codex CLI."""
    if shutil.which("claude"):
        return "claude"
    if shutil.which("codex"):
        return "codex"
    return None


def build_missing_primitives(root: Path, thesis_id: str) -> dict[str, Any]:
    """Dispatch CLI builder to implement missing primitives for a thesis."""
    compilation_family_name = "orb"
    for candidate_family in ("orb", "ema"):
        candidate_proposal_path = (
            root / load_family(candidate_family).proposals_dirname / f"{thesis_id}.json"
        )
        if candidate_proposal_path.exists():
            compilation_family_name = candidate_family
            break

    family = load_family(compilation_family_name)
    proposal_path = root / family.proposals_dirname / f"{thesis_id}.json"
    compilation_path = root / family.compilations_dirname / f"{thesis_id}.json"
    if not proposal_path.exists():
        return {
            "status": "error",
            "reason": f"missing proposal artifact for {thesis_id}",
            "generated_config": None,
            "validation_passed": False,
        }
    if not compilation_path.exists():
        return {
            "status": "error",
            "reason": f"missing compilation artifact for {thesis_id}",
            "generated_config": None,
            "validation_passed": False,
        }

    proposal = json.loads(proposal_path.read_text())
    compilation = json.loads(compilation_path.read_text())
    family_name = proposal.get("strategy_family") or proposal.get("family", "orb")
    family = load_family(family_name)
    normalized_contract = compilation.get("normalized_contract") or []
    missing_primitives = compilation.get("missing_primitives") or []
    generated_name = (
        f"{family.name}_{thesis_id}.yaml"
        if not thesis_id.startswith(f"{family.name}_")
        else f"{thesis_id}.yaml"
    )
    config_path = f"configs/variants/{generated_name}"
    write_json_artifact(
        root / family.builder_requests_dirname / f"{thesis_id}.json",
        {
            "thesis_id": thesis_id,
            "family": family_name,
            "proposal_path": proposal_path.relative_to(root).as_posix(),
            "compilation_path": compilation_path.relative_to(root).as_posix(),
            "missing_primitives": missing_primitives,
            "normalized_contract": normalized_contract,
            "status": "requested",
            "timestamp": timestamp_ms(),
        },
    )

    config_abspath = root / config_path
    if config_abspath.exists():
        return {
            "status": "completed",
            "reason": "config already exists",
            "generated_config": config_path,
            "validation_passed": True,
        }

    prompt = f"""Goal:
Automatically implement the missing primitive(s) for thesis `{thesis_id}` and create the resulting config artifact.

Context:
- Repo root: {root}
- Proposal artifact: {proposal_path}
- Compilation artifact: {compilation_path}
- Expected config path: {config_path}
- Missing primitives: {json.dumps(missing_primitives, indent=2)}
- Normalized contract: {json.dumps(normalized_contract, indent=2)}
- Hypothesis: {proposal.get("hypothesis", "")}

Requirements:
- Write the necessary code changes in this repo.
- Add or update tests for the new primitive behavior.
- Create the config file at `{config_path}` if it becomes expressible.
- Return a concise final report with:
  1. whether implementation succeeded
  2. files changed
  3. tests run
  4. generated config path

Constraints:
- Preserve existing behavior except for the new primitive support.
- If implementation cannot be completed safely, explain why.
"""

    cli = _find_cli()
    if not cli:
        return {
            "status": "error",
            "reason": "No CLI available for builder dispatch",
            "generated_config": None,
            "validation_passed": False,
        }

    if cli == "claude":
        cmd = [
            "claude",
            "-p",
            "--model",
            "claude-opus-4-6",
            "--allowedTools",
            "Read,LS,Grep,Glob,ApplyPatch,Execute",
            "--no-session-persistence",
            prompt,
        ]
    else:
        cmd = ["codex", "exec", prompt]

    proc = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        cwd=str(root),
        timeout=900,
    )
    result = (proc.stdout or "") + (proc.stderr or "")

    generated = config_path if config_abspath.exists() else None
    return {
        "status": "completed" if generated else "error",
        "reason": result,
        "generated_config": generated,
        "validation_passed": bool(generated),
    }
