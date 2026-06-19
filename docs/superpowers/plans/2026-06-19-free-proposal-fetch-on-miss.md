# Free Proposal Fetch-On-Miss Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let mechanism proposals request missing primitives, halt for missing data, and safely persist declarative agent-created entry features for future research.

**Architecture:** Keep the first implementation deliberately narrow: schema/prompt changes first, then `needs_data` halt handling, then append-only registries, then feature-table schema integration. Auto-persisted entry features are declarative formulas only; arbitrary generated Python remains behind the existing promotion queue.

**Tech Stack:** Python 3.14, Pydantic models in `research_types.py`, stdlib JSON/Path helpers, pytest, existing artifact/state writers.

---

## Scope Check

The spec touches four subsystems: proposal schema/prompt, halt state, builder registry, and feature-table schema. They are coupled through `requested_primitive`, so this plan keeps them in one sequence, but every task is independently testable and should be committed separately.

## File Structure

- Modify `research_types.py`: add `RequestedPrimitive` and relax `MechanismProposal` actionable validation.
- Modify `research_prompts.py`: document free proposal and `requested_primitive` output.
- Modify `scripts/check_prompt_drift.py`: existing field drift check should catch `requested_primitive`; no new helper unless tests show it misses nested fields.
- Modify `autoresearch_research.py`: carry `requested_primitive` into thesis metadata and route needs-data decisions.
- Modify `autoresearch_orchestration.py`: add `needs_data` state bookkeeping and guarded resume clearing.
- Modify `compiler_builder.py`: append `created_by` promotion metadata and write/read capability registry latest-wins.
- Create `raw_input_manifest.py`: one home for `runtime/raw_input_manifest.json` parsing.
- Create `agent_feature_registry.py`: one home for agent feature registry validation and family status transitions.
- Modify `feature_table.py`: derive active agent feature columns through one schema helper.
- Modify `evidence_pack.py` and prompt callers only if tests show they still read static `ENTRY_TIME_COLUMNS`.
- Test files:
  - `tests/test_research_types.py`
  - `tests/test_research_prompts.py`
  - `tests/test_raw_input_manifest.py`
  - `tests/test_agent_feature_registry.py`
  - `tests/test_autoresearch_orchestration.py`
  - `tests/test_compiler_builder.py`
  - `tests/test_feature_table.py`

## Task 1: Proposal Schema and Prompt Contract

**Files:**
- Modify: `research_types.py`
- Modify: `research_prompts.py`
- Test: `tests/test_research_types.py`
- Test: `tests/test_research_prompts.py`
- Test: `tests/test_conductor_prompt_v3.py`

- [ ] **Step 1: Write failing schema tests**

Append to `tests/test_research_types.py`:

```python
import pytest
from pydantic import ValidationError

from research_types import MechanismProposal


def _prediction(metric: str, value: float) -> dict[str, object]:
    return {
        "metric": metric,
        "direction": "increase",
        "predicted": value,
        "rationale": f"{metric} should improve",
    }


def _actionable_payload() -> dict[str, object]:
    return {
        "story": "Signed volume separates informed entries.",
        "rule": "signed_volume_z > 1.5",
        "competitor_rule": "signed_volume_z <= 1.5",
        "competitor_story": "Signed volume does not matter.",
        "actionable": True,
        "proposed_change": None,
        "predictions": [
            _prediction("profit_factor", 1.4),
            _prediction("trade_count", 20),
        ],
    }


def test_actionable_mechanism_allows_requested_primitive_without_proposed_change() -> None:
    payload = _actionable_payload()
    payload["requested_primitive"] = {
        "name": "signed_volume_z",
        "kind": "entry_feature",
        "description": "Entry-time z-score of signed volume.",
        "required_data": ["trade_signed_volume"],
    }

    proposal = MechanismProposal.model_validate(payload)

    assert proposal.requested_primitive is not None
    assert proposal.requested_primitive.name == "signed_volume_z"
    assert proposal.proposed_change is None


def test_actionable_mechanism_rejects_without_change_or_requested_primitive() -> None:
    payload = _actionable_payload()

    with pytest.raises(ValidationError, match="proposed_change or requested_primitive"):
        MechanismProposal.model_validate(payload)


def test_requested_primitive_requires_snake_case_name() -> None:
    payload = _actionable_payload()
    payload["requested_primitive"] = {
        "name": "Signed Volume",
        "kind": "entry_feature",
        "description": "Entry-time z-score of signed volume.",
        "required_data": ["trade_signed_volume"],
    }

    with pytest.raises(ValidationError, match="snake_case"):
        MechanismProposal.model_validate(payload)
```

- [ ] **Step 2: Run schema tests to verify they fail**

Run:

```bash
pytest tests/test_research_types.py::test_actionable_mechanism_allows_requested_primitive_without_proposed_change tests/test_research_types.py::test_actionable_mechanism_rejects_without_change_or_requested_primitive tests/test_research_types.py::test_requested_primitive_requires_snake_case_name -v
```

Expected: FAIL because `requested_primitive` is not a model field and actionable still requires `proposed_change`.

- [ ] **Step 3: Implement schema model**

In `research_types.py`, add this model immediately before `MechanismProposal`:

```python
class RequestedPrimitive(BaseModel):
    name: str
    kind: Literal["entry_feature", "management"]
    description: str
    required_data: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _validate_requested_primitive(self) -> "RequestedPrimitive":
        if not self.name.replace("_", "").isalnum() or self.name != self.name.lower():
            raise ValueError("requested_primitive.name must be snake_case")
        if not self.description.strip():
            raise ValueError("requested_primitive.description is required")
        if not all(item.strip() for item in self.required_data):
            raise ValueError("requested_primitive.required_data entries must be non-empty")
        return self
```

Update imports at the top of `research_types.py` so `Field` and `Literal` are available. If `Literal` is already imported, do not duplicate it.

Then update `MechanismProposal`:

```python
class MechanismProposal(BaseModel):
    """Conductor output for the causal-engine research path."""

    story: str
    rule: str
    competitor_rule: str
    competitor_story: str
    actionable: bool
    proposed_change: dict[str, Any] | None = None
    requested_primitive: RequestedPrimitive | None = None
    predictions: list[Prediction] | None = None

    @model_validator(mode="after")
    def _validate_actionable_contract(self) -> "MechanismProposal":
        if not self.actionable:
            return self
        if not self.proposed_change and self.requested_primitive is None:
            raise ValueError(
                "proposed_change or requested_primitive is required when actionable is true"
            )
        if self.predictions is None or len(self.predictions) < 2:
            raise ValueError(
                "predictions must contain at least two entries when actionable is true"
            )
        metrics = [prediction.metric for prediction in self.predictions]
        if len(set(metrics)) != len(metrics):
            raise ValueError("predictions must use distinct MetricName values")
        missing_predicted = [
            prediction.metric.value
            for prediction in self.predictions
            if prediction.predicted is None
        ]
        if missing_predicted:
            raise ValueError(
                "predictions must include predicted values for: "
                + ", ".join(sorted(missing_predicted))
            )
        unsupported_metrics = sorted(
            {
                prediction.metric.value
                for prediction in self.predictions
                if prediction.metric not in HARVEST_OBSERVABLE_METRICS
            }
        )
        if unsupported_metrics:
            raise ValueError(
                "predictions use metrics unavailable to harvest evaluator: "
                + ", ".join(unsupported_metrics)
            )
        return self
```

- [ ] **Step 4: Update prompt tests**

Replace `tests/test_research_prompts.py::test_mechanism_prompt_matches_single_change_runtime_contract` with:

```python
def test_mechanism_prompt_documents_requested_primitive_contract() -> None:
    prompt = _build_mechanism_system_prompt()

    assert "requested_primitive" in prompt
    assert "available entry-time columns are what exists today, not a ceiling" in prompt
    assert "- actionable=true requires predictions and either proposed_change or requested_primitive." in prompt
```

- [ ] **Step 5: Update mechanism prompt**

In `research_prompts.py`, replace the ACTIONABLE OUTPUT RULES block and JSON shape with:

```python
    ACTIONABLE OUTPUT RULES
    - available entry-time columns are what exists today, not a ceiling.
    - If the mechanism needs a missing feature, set requested_primitive.
    - actionable=true requires predictions and either proposed_change or requested_primitive.
    - proposed_change must contain exactly one changed key when present.
    - requested_primitive.kind must be "entry_feature" or "management" when present.
    - predictions are required iff actionable=true.
    - predictions must include at least two distinct MetricName values from:
      profit_factor, trade_count, max_drawdown, median_expectancy.

    Return only JSON matching this shape:
    {
      "story": "...",
      "rule": "...",
      "competitor_rule": "...",
      "competitor_story": "...",
      "actionable": false,
      "proposed_change": null,
      "requested_primitive": null,
      "predictions": null
    }
```

- [ ] **Step 6: Run targeted verification**

Run:

```bash
pytest tests/test_research_types.py tests/test_research_prompts.py tests/test_conductor_prompt_v3.py -q
python scripts/check_prompt_drift.py
```

Expected: all tests pass and prompt drift exits 0.

- [ ] **Step 7: Commit**

Run:

```bash
git add research_types.py research_prompts.py tests/test_research_types.py tests/test_research_prompts.py
git commit -m "feat: allow requested primitives in mechanism proposals"
```

## Task 2: Carry Requested Primitive Into Thesis Metadata

**Files:**
- Modify: `autoresearch_research.py`
- Test: `tests/test_autoresearch_research.py`

- [ ] **Step 1: Write failing conversion tests**

Append to `tests/test_autoresearch_research.py`:

```python
def test_mechanism_result_carries_requested_primitive_to_thesis_meta() -> None:
    raw = {
        "story": "Signed volume identifies informed entries.",
        "rule": "signed_volume_z > 1.5",
        "competitor_rule": "signed_volume_z <= 1.5",
        "competitor_story": "Signed volume is noise.",
        "actionable": True,
        "proposed_change": None,
        "requested_primitive": {
            "name": "signed_volume_z",
            "kind": "entry_feature",
            "description": "Entry-time signed volume z-score.",
            "required_data": ["trade_signed_volume"],
        },
        "predictions": [
            {
                "metric": "profit_factor",
                "direction": "increase",
                "predicted": 1.4,
                "rationale": "Filter should remove adverse entries.",
            },
            {
                "metric": "trade_count",
                "direction": "decrease",
                "predicted": 20,
                "rationale": "Filter should remove some entries.",
            },
        ],
    }

    out = _thesis_meta_from_mechanism_result(raw, "ema")

    assert out["requested_primitives"] == ["signed_volume_z"]
    assert out["requested_primitive"] == raw["requested_primitive"]
    assert out["requires_code_change"] is True
```

If `_thesis_meta_from_mechanism_result` is not public in the current file, use the existing helper that currently persists `proposed_change`; do not create a duplicate converter.

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
pytest tests/test_autoresearch_research.py::test_mechanism_result_carries_requested_primitive_to_thesis_meta -v
```

Expected: FAIL because requested primitive is not carried into thesis metadata.

- [ ] **Step 3: Implement minimal metadata carry**

In `autoresearch_research.py`, update the mechanism-to-thesis metadata helper so it includes:

```python
requested_primitive = raw_thesis.get("requested_primitive")
if isinstance(requested_primitive, dict) and requested_primitive.get("name"):
    out["requested_primitive"] = dict(requested_primitive)
    out["requested_primitives"] = [str(requested_primitive["name"])]
    out["requires_code_change"] = True
```

Keep existing `proposed_change` behavior unchanged. If the helper uses a different output variable name than `out`, apply the same assignments to the existing output dict.

- [ ] **Step 4: Run targeted verification**

Run:

```bash
pytest tests/test_autoresearch_research.py::test_mechanism_result_carries_requested_primitive_to_thesis_meta -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

Run:

```bash
git add autoresearch_research.py tests/test_autoresearch_research.py
git commit -m "feat: carry requested primitives into thesis metadata"
```

## Task 3: Raw Input Manifest and Needs-Data Halt

**Files:**
- Create: `raw_input_manifest.py`
- Modify: `autoresearch_orchestration.py`
- Test: `tests/test_raw_input_manifest.py`
- Test: `tests/test_autoresearch_orchestration.py`

- [ ] **Step 1: Write raw manifest tests**

Create `tests/test_raw_input_manifest.py`:

```python
from pathlib import Path

import pytest

from raw_input_manifest import RawInputManifestError, available_raw_inputs


def test_available_raw_inputs_reads_manifest(tmp_path: Path) -> None:
    path = tmp_path / "runtime" / "raw_input_manifest.json"
    path.parent.mkdir()
    path.write_text('{"available_raw_inputs": ["ohlcv", "calendar"]}')

    assert available_raw_inputs(tmp_path) == frozenset({"ohlcv", "calendar"})


def test_available_raw_inputs_fails_loud_for_missing_manifest(tmp_path: Path) -> None:
    with pytest.raises(RawInputManifestError, match="runtime/raw_input_manifest.json"):
        available_raw_inputs(tmp_path)


def test_available_raw_inputs_fails_loud_for_bad_shape(tmp_path: Path) -> None:
    path = tmp_path / "runtime" / "raw_input_manifest.json"
    path.parent.mkdir()
    path.write_text('{"available_raw_inputs": "ohlcv"}')

    with pytest.raises(RawInputManifestError, match="available_raw_inputs"):
        available_raw_inputs(tmp_path)
```

- [ ] **Step 2: Run raw manifest tests to verify they fail**

Run:

```bash
pytest tests/test_raw_input_manifest.py -q
```

Expected: FAIL because `raw_input_manifest.py` does not exist.

- [ ] **Step 3: Implement raw manifest reader**

Create `raw_input_manifest.py`:

```python
from __future__ import annotations

import json
from pathlib import Path


class RawInputManifestError(RuntimeError):
    pass


def raw_input_manifest_path(root: Path) -> Path:
    return root / "runtime" / "raw_input_manifest.json"


def available_raw_inputs(root: Path) -> frozenset[str]:
    path = raw_input_manifest_path(root)
    if not path.exists():
        raise RawInputManifestError(f"missing {path.relative_to(root)}")
    try:
        payload = json.loads(path.read_text())
    except json.JSONDecodeError as exc:
        raise RawInputManifestError(f"invalid JSON in {path.relative_to(root)}: {exc}") from exc
    values = payload.get("available_raw_inputs")
    if not isinstance(values, list) or not all(isinstance(item, str) and item for item in values):
        raise RawInputManifestError("available_raw_inputs must be a list of non-empty strings")
    return frozenset(values)
```

- [ ] **Step 4: Write needs-data state test**

Append to `tests/test_autoresearch_orchestration.py`:

```python
def test_mark_needs_data_manual_review_writes_data_request(tmp_path: Path) -> None:
    state = {"state": "running", "job": 7, "research_round": 3}
    thesis = {
        "requested_primitive": {
            "name": "signed_volume_z",
            "kind": "entry_feature",
            "description": "Entry-time signed volume z-score.",
            "required_data": ["trade_signed_volume"],
        }
    }

    out = _mark_needs_data_manual_review(
        root=tmp_path,
        state=state,
        thesis_id="ema-7-3-1",
        thesis=thesis,
        research_round=3,
    )

    request_path = tmp_path / "runtime/jobs/job-7/research/round-3/data_acquisition_request.json"
    assert out["state"] == "halted"
    assert out["halted_reason"] == "needs_data"
    assert out["halted_thesis_id"] == "ema-7-3-1"
    assert out["next_action"]["type"] == "manual_review"
    assert out["data_requests"][0]["path"] == request_path.relative_to(tmp_path).as_posix()
    assert request_path.exists()
```

- [ ] **Step 5: Run needs-data test to verify it fails**

Run:

```bash
pytest tests/test_autoresearch_orchestration.py::test_mark_needs_data_manual_review_writes_data_request -v
```

Expected: FAIL because `_mark_needs_data_manual_review` does not exist.

- [ ] **Step 6: Implement needs-data helper**

In `autoresearch_orchestration.py`, add:

```python
def _mark_needs_data_manual_review(
    *,
    root: Path,
    state: dict[str, Any],
    thesis_id: str,
    thesis: dict[str, Any],
    research_round: int,
) -> dict[str, Any]:
    primitive = thesis.get("requested_primitive") or {}
    request = {
        "feature_name": primitive.get("name", ""),
        "kind": primitive.get("kind", ""),
        "description": primitive.get("description", ""),
        "required_data": [
            {"name": name, "granularity": "unknown"}
            for name in primitive.get("required_data", [])
        ],
        "candidate_sources": [],
        "requesting_thesis_id": thesis_id,
        "created_by": "agent",
        "created_at": iso8601_utc_now(),
    }
    job = int(state.get("job", 0))
    request_path = (
        root
        / "runtime"
        / "jobs"
        / f"job-{job}"
        / "research"
        / f"round-{research_round}"
        / "data_acquisition_request.json"
    )
    request_path.parent.mkdir(parents=True, exist_ok=True)
    write_json_artifact(request_path, request)
    state["state"] = "halted"
    state["halted_reason"] = "needs_data"
    state["halted_thesis_id"] = thesis_id
    state["halted_thesis"] = thesis
    data_requests = list(state.get("data_requests") or [])
    data_requests.append({"path": request_path.relative_to(root).as_posix(), **request})
    state["data_requests"] = data_requests
    state["blockers"] = [{"kind": "manual_review", "detail": f"Data required for {thesis_id}"}]
    state["next_action"] = {
        "type": "manual_review",
        "reason": f"Data required for {thesis_id}",
        "requires_subagent": False,
    }
    return state
```

If `Path`, `Any`, or `write_json_artifact` is not imported in the file, import them from the existing local modules rather than duplicating file-writing code.

- [ ] **Step 7: Run targeted verification**

Run:

```bash
pytest tests/test_raw_input_manifest.py tests/test_autoresearch_orchestration.py::test_mark_needs_data_manual_review_writes_data_request -q
```

Expected: PASS.

- [ ] **Step 8: Commit**

Run:

```bash
git add raw_input_manifest.py autoresearch_orchestration.py tests/test_raw_input_manifest.py tests/test_autoresearch_orchestration.py
git commit -m "feat: add needs-data halt state"
```

## Task 4: Capability Registry Latest-Wins and Promotion Provenance

**Files:**
- Modify: `compiler_builder.py`
- Test: `tests/test_compiler_builder.py`

- [ ] **Step 1: Write failing registry tests**

Append to `tests/test_compiler_builder.py`:

```python
import json
from pathlib import Path

from compiler_builder import BUILDER_CAPABILITY_REGISTRY, _load_builder_capability_registry


def test_builder_capability_registry_latest_entry_wins(tmp_path: Path) -> None:
    path = tmp_path / BUILDER_CAPABILITY_REGISTRY
    path.parent.mkdir(parents=True)
    first = {
        "family_name": "ema",
        "kind": "entry_feature",
        "missing_primitives": ["rvol_spike"],
        "config_change_keys": [],
        "diagnostic_keys": [],
        "promotion_dir": "old",
    }
    second = {**first, "promotion_dir": "new"}
    path.write_text(json.dumps(first) + "\n" + json.dumps(second) + "\n")

    entries = _load_builder_capability_registry(tmp_path)

    assert len(entries) == 1
    assert entries[0]["promotion_dir"] == "new"
```

- [ ] **Step 2: Run registry test to verify it fails**

Run:

```bash
pytest tests/test_compiler_builder.py::test_builder_capability_registry_latest_entry_wins -v
```

Expected: FAIL because loader currently returns both entries.

- [ ] **Step 3: Implement latest-wins dedup**

In `compiler_builder.py`, add:

```python
def _capability_signature(entry: dict[str, Any]) -> tuple[Any, ...]:
    return (
        entry.get("family_name"),
        entry.get("kind"),
        tuple(entry.get("missing_primitives") or []),
        tuple(entry.get("config_change_keys") or []),
        tuple(entry.get("diagnostic_keys") or []),
    )
```

Then replace the return path in `_load_builder_capability_registry`:

```python
    latest: dict[tuple[Any, ...], dict[str, Any]] = {}
    for entry in entries:
        latest[_capability_signature(entry)] = entry
    return list(latest.values())
```

- [ ] **Step 4: Add promotion provenance test**

Append to `tests/test_compiler_builder.py` near existing promotion tests:

```python
def test_promotion_manifest_marks_agent_created(tmp_path: Path) -> None:
    manifest = _record_builder_promotion_candidate(
        source_root=tmp_path,
        workspace_root=tmp_path,
        artifact_root=tmp_path,
        task=BuilderTask(
            family_name="ema",
            thesis_id="ema-thesis",
            config_change_keys=[],
            missing_primitives=["rvol_spike"],
            required_diagnostic_specs=[],
        ),
        thesis_id="ema-thesis",
    )

    assert manifest["created_by"] == "agent"
    assert manifest["created_at"]
```

If `BuilderTask` requires additional fields in the current code, fill them with the same defaults used by nearby tests.

- [ ] **Step 5: Implement promotion provenance**

In `_record_builder_promotion_candidate`, add these keys to `manifest`:

```python
        "created_by": "agent",
        "created_at": timestamp_now(),
```

Keep existing `"timestamp"` until all consumers are migrated.

- [ ] **Step 6: Run targeted verification**

Run:

```bash
pytest tests/test_compiler_builder.py::test_builder_capability_registry_latest_entry_wins tests/test_compiler_builder.py::test_promotion_manifest_marks_agent_created -v
```

Expected: PASS.

- [ ] **Step 7: Commit**

Run:

```bash
git add compiler_builder.py tests/test_compiler_builder.py
git commit -m "feat: dedupe builder capability registry"
```

## Task 5: Agent Feature Registry

**Files:**
- Create: `agent_feature_registry.py`
- Test: `tests/test_agent_feature_registry.py`

- [ ] **Step 1: Write failing registry tests**

Create `tests/test_agent_feature_registry.py`:

```python
from pathlib import Path

import pytest

from agent_feature_registry import (
    AgentFeatureRegistryError,
    active_agent_feature_columns,
    register_agent_feature,
)


def test_register_feature_adds_family_status(tmp_path: Path) -> None:
    register_agent_feature(
        tmp_path,
        column="rvol_spike",
        formula="rvol / rolling_mean(rvol, 20)",
        required_data=["ohlcv"],
        family_name="ema",
        thesis_id="ema-1",
    )
    register_agent_feature(
        tmp_path,
        column="rvol_spike",
        formula="rvol / rolling_mean(rvol, 20)",
        required_data=["ohlcv"],
        family_name="orb",
        thesis_id="orb-1",
    )

    assert active_agent_feature_columns(tmp_path, "ema") == frozenset({"rvol_spike"})
    assert active_agent_feature_columns(tmp_path, "orb") == frozenset({"rvol_spike"})


def test_register_feature_rejects_formula_conflict(tmp_path: Path) -> None:
    register_agent_feature(
        tmp_path,
        column="rvol_spike",
        formula="rvol / rolling_mean(rvol, 20)",
        required_data=["ohlcv"],
        family_name="ema",
        thesis_id="ema-1",
    )

    with pytest.raises(AgentFeatureRegistryError, match="formula conflict"):
        register_agent_feature(
            tmp_path,
            column="rvol_spike",
            formula="rvol > 2",
            required_data=["ohlcv"],
            family_name="ema",
            thesis_id="ema-2",
        )


def test_register_feature_rejects_unknown_dependency(tmp_path: Path) -> None:
    with pytest.raises(AgentFeatureRegistryError, match="unknown dependency"):
        register_agent_feature(
            tmp_path,
            column="rvol_spike",
            formula="rvol / rolling_mean(rvol, 20)",
            required_data=["ohlcv"],
            family_name="ema",
            thesis_id="ema-1",
        )
```

- [ ] **Step 2: Run registry tests to verify they fail**

Run:

```bash
pytest tests/test_agent_feature_registry.py -q
```

Expected: FAIL because `agent_feature_registry.py` does not exist.

- [ ] **Step 3: Implement minimal registry**

Create `agent_feature_registry.py`:

```python
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from persistence_utils import write_text_atomic
from strategy_event_logger import timestamp_now

AGENT_FEATURE_REGISTRY = Path("runtime") / "agent_features.jsonl"
_STATIC_FORMULA_NAMES = frozenset({"rvol", "gap_pct", "rolling_mean"})


class AgentFeatureRegistryError(RuntimeError):
    pass


def _path(root: Path) -> Path:
    return root / AGENT_FEATURE_REGISTRY


def _load(root: Path) -> list[dict[str, Any]]:
    path = _path(root)
    if not path.exists():
        return []
    entries: list[dict[str, Any]] = []
    for line in path.read_text().splitlines():
        if not line.strip():
            continue
        payload = json.loads(line)
        if isinstance(payload, dict):
            entries.append(payload)
    return entries


def _write(root: Path, entries: list[dict[str, Any]]) -> None:
    path = _path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    write_text_atomic(path, "".join(json.dumps(entry, sort_keys=True) + "\n" for entry in entries))


def _formula_names(formula: str) -> set[str]:
    return set(re.findall(r"[A-Za-z_][A-Za-z0-9_]*", formula))


def active_agent_feature_columns(root: Path, family_name: str) -> frozenset[str]:
    columns = []
    for entry in _load(root):
        family = (entry.get("families") or {}).get(family_name) or {}
        if family.get("status") in {"exploratory", "validated"}:
            columns.append(str(entry.get("column")))
    return frozenset(columns)


def register_agent_feature(
    root: Path,
    *,
    column: str,
    formula: str,
    required_data: list[str],
    family_name: str,
    thesis_id: str,
) -> None:
    entries = _load(root)
    active_columns = active_agent_feature_columns(root, family_name)
    unknown = _formula_names(formula) - _STATIC_FORMULA_NAMES - active_columns - {column}
    if unknown:
        raise AgentFeatureRegistryError(f"unknown dependency: {sorted(unknown)}")
    for entry in entries:
        if entry.get("column") != column:
            continue
        if entry.get("formula") != formula:
            raise AgentFeatureRegistryError(f"formula conflict for {column}")
        families = entry.setdefault("families", {})
        families[family_name] = {
            "status": "exploratory",
            "requesting_thesis_id": thesis_id,
            "requesting_thesis_verdict": "build_passed",
        }
        _write(root, entries)
        return
    entries.append(
        {
            "column": column,
            "formula": formula,
            "required_data": list(required_data),
            "requesting_thesis_id": thesis_id,
            "families": {
                family_name: {
                    "status": "exploratory",
                    "requesting_thesis_id": thesis_id,
                    "requesting_thesis_verdict": "build_passed",
                }
            },
            "created_by": "agent",
            "created_at": timestamp_now(),
        }
    )
    _write(root, entries)
```

- [ ] **Step 4: Run tests and tighten allowlist if needed**

Run:

```bash
pytest tests/test_agent_feature_registry.py -q
```

Expected: PASS. If the unknown dependency test fails because `rvol` is static, change the test formula to `"signed_volume_z / rolling_mean(signed_volume_z, 20)"`.

- [ ] **Step 5: Commit**

Run:

```bash
git add agent_feature_registry.py tests/test_agent_feature_registry.py
git commit -m "feat: add agent feature registry"
```

## Task 6: Feature Table Schema Integration

**Files:**
- Modify: `feature_table.py`
- Modify: `evidence_pack.py`
- Test: `tests/test_feature_table.py`
- Test: `tests/test_evidence_pack.py`

- [ ] **Step 1: Write failing feature-table schema test**

Append to `tests/test_feature_table.py`:

```python
from agent_feature_registry import register_agent_feature


def test_entry_time_columns_for_family_include_active_agent_features(tmp_path: Path) -> None:
    register_agent_feature(
        tmp_path,
        column="rvol_spike",
        formula="rvol / rolling_mean(rvol, 20)",
        required_data=["ohlcv"],
        family_name="ema",
        thesis_id="ema-1",
    )

    assert "rvol_spike" in entry_time_columns_for_family(tmp_path, "ema")
    assert "rvol_spike" not in entry_time_columns_for_family(tmp_path, "orb")
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
pytest tests/test_feature_table.py::test_entry_time_columns_for_family_include_active_agent_features -v
```

Expected: FAIL because `entry_time_columns_for_family` does not exist.

- [ ] **Step 3: Implement schema helper**

In `feature_table.py`, import:

```python
from agent_feature_registry import active_agent_feature_columns
```

Add:

```python
STATIC_ENTRY_TIME_COLUMNS = ENTRY_TIME_COLUMNS


def entry_time_columns_for_family(runtime_root: Path, family_name: str) -> frozenset[str]:
    return STATIC_ENTRY_TIME_COLUMNS | active_agent_feature_columns(runtime_root, family_name)
```

Do not remove `ENTRY_TIME_COLUMNS` in this task; existing code depends on it. This task adds the family-aware helper without a large call-site rewrite.

- [ ] **Step 4: Run targeted test**

Run:

```bash
pytest tests/test_feature_table.py::test_entry_time_columns_for_family_include_active_agent_features -v
```

Expected: PASS.

- [ ] **Step 5: Update evidence rendering only if static list is still used for family prompts**

Search:

```bash
rg -n "ENTRY_TIME_COLUMNS|entry_time_columns_for_family" evidence_pack.py research_prompts.py research_conductor.py
```

If `evidence_pack.py` renders entry columns from `ENTRY_TIME_COLUMNS`, change that call site to accept `runtime_root` and `family_name`, then call `entry_time_columns_for_family(runtime_root, family_name)`. Add a test in `tests/test_evidence_pack.py`:

```python
def test_rendered_feature_columns_include_family_agent_features(tmp_path: Path) -> None:
    register_agent_feature(
        tmp_path,
        column="rvol_spike",
        formula="rvol / rolling_mean(rvol, 20)",
        required_data=["ohlcv"],
        family_name="ema",
        thesis_id="ema-1",
    )

    rendered = render_entry_filter_columns(runtime_root=tmp_path, family_name="ema")

    assert "rvol_spike" in rendered
```

Use the actual rendering helper name from `evidence_pack.py`; do not introduce a second renderer if one already exists.

- [ ] **Step 6: Run targeted verification**

Run:

```bash
pytest tests/test_feature_table.py tests/test_evidence_pack.py -q
```

Expected: PASS.

- [ ] **Step 7: Commit**

Run:

```bash
git add feature_table.py evidence_pack.py tests/test_feature_table.py tests/test_evidence_pack.py
git commit -m "feat: surface active agent features in feature schema"
```

## Task 7: Resume Guard for Needs-Data

**Files:**
- Modify: `autoresearch_orchestration.py`
- Test: `tests/test_autoresearch_orchestration.py`

- [ ] **Step 1: Write resume guard tests**

Append to `tests/test_autoresearch_orchestration.py`:

```python
def test_needs_data_resume_clears_when_manifest_satisfies_request(tmp_path: Path) -> None:
    request_path = tmp_path / "runtime/jobs/job-7/research/round-3/data_acquisition_request.json"
    request_path.parent.mkdir(parents=True)
    request_path.write_text(
        '{"requesting_thesis_id":"ema-7-3-1","required_data":[{"name":"trade_signed_volume"}]}'
    )
    manifest = tmp_path / "runtime/raw_input_manifest.json"
    manifest.write_text('{"available_raw_inputs":["trade_signed_volume"]}')
    state = {
        "state": "halted",
        "job": 7,
        "research_round": 3,
        "halted_reason": "needs_data",
        "halted_thesis_id": "ema-7-3-1",
        "data_requests": [{"path": "runtime/jobs/job-7/research/round-3/data_acquisition_request.json"}],
    }

    assert _needs_data_can_resume(tmp_path, state) is True


def test_needs_data_resume_stays_halted_on_thesis_mismatch(tmp_path: Path) -> None:
    request_path = tmp_path / "runtime/jobs/job-7/research/round-3/data_acquisition_request.json"
    request_path.parent.mkdir(parents=True)
    request_path.write_text(
        '{"requesting_thesis_id":"other","required_data":[{"name":"trade_signed_volume"}]}'
    )
    manifest = tmp_path / "runtime/raw_input_manifest.json"
    manifest.write_text('{"available_raw_inputs":["trade_signed_volume"]}')
    state = {
        "state": "halted",
        "job": 7,
        "research_round": 3,
        "halted_reason": "needs_data",
        "halted_thesis_id": "ema-7-3-1",
        "data_requests": [{"path": "runtime/jobs/job-7/research/round-3/data_acquisition_request.json"}],
    }

    assert _needs_data_can_resume(tmp_path, state) is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
pytest tests/test_autoresearch_orchestration.py::test_needs_data_resume_clears_when_manifest_satisfies_request tests/test_autoresearch_orchestration.py::test_needs_data_resume_stays_halted_on_thesis_mismatch -v
```

Expected: FAIL because `_needs_data_can_resume` does not exist.

- [ ] **Step 3: Implement guard**

In `autoresearch_orchestration.py`, add:

```python
def _needs_data_can_resume(root: Path, state: dict[str, Any]) -> bool:
    if state.get("halted_reason") != "needs_data":
        return False
    requests = state.get("data_requests") or []
    if not requests:
        return False
    request_rel = str(requests[-1].get("path") or "")
    expected_prefix = f"runtime/jobs/job-{state.get('job')}/research/round-{state.get('research_round')}/"
    if not request_rel.startswith(expected_prefix):
        return False
    request_path = root / request_rel
    try:
        request = json.loads(request_path.read_text())
    except (OSError, json.JSONDecodeError):
        return False
    if request.get("requesting_thesis_id") != state.get("halted_thesis_id"):
        return False
    try:
        available = available_raw_inputs(root)
    except RawInputManifestError:
        return False
    required = {
        item.get("name")
        for item in request.get("required_data") or []
        if isinstance(item, dict)
    }
    return bool(required) and required <= available
```

Import `json`, `available_raw_inputs`, and `RawInputManifestError` if not already present.

- [ ] **Step 4: Wire guard into resume path**

Find the existing resume logic that checks `halted_reason == "requires_code_change"`. Add a branch before it:

```python
if state.get("halted_reason") == "needs_data":
    if not _needs_data_can_resume(controller.root, state):
        return state
    state.pop("halted_reason", None)
    state.pop("halted_thesis_id", None)
    state.pop("next_action", None)
    state["state"] = "running"
```

Use the existing state-writing pattern in that function; do not create a parallel resume engine.

- [ ] **Step 5: Run targeted verification**

Run:

```bash
pytest tests/test_autoresearch_orchestration.py::test_needs_data_resume_clears_when_manifest_satisfies_request tests/test_autoresearch_orchestration.py::test_needs_data_resume_stays_halted_on_thesis_mismatch -v
```

Expected: PASS.

- [ ] **Step 6: Commit**

Run:

```bash
git add autoresearch_orchestration.py tests/test_autoresearch_orchestration.py
git commit -m "feat: guard needs-data resume"
```

## Final Verification

- [ ] **Run targeted suite**

Run:

```bash
pytest tests/test_research_types.py tests/test_research_prompts.py tests/test_autoresearch_research.py tests/test_raw_input_manifest.py tests/test_agent_feature_registry.py tests/test_autoresearch_orchestration.py tests/test_compiler_builder.py tests/test_feature_table.py tests/test_evidence_pack.py -q
python scripts/check_prompt_drift.py
```

Expected: all pass.

- [ ] **Run formatting and hooks**

Run:

```bash
pre-commit run --all-files
```

Expected: all hooks pass.

- [ ] **Push and use CI as full-suite source of truth**

Run:

```bash
git push origin HEAD
gh run list --branch "$(git branch --show-current)" --workflow CI --limit 1
gh run watch --exit-status
```

Expected: CI exits 0. Report the run URL and final summary.

## Self-Review

Spec coverage:
- Free proposal prompt and schema: Task 1.
- Requested primitive carried to thesis: Task 2.
- Fetch-on-miss and `needs_data`: Tasks 3 and 7.
- Capability registry latest-wins and provenance: Task 4.
- Agent feature registry, global column identity, per-family status, reactivation, dependencies: Task 5.
- Feature-table and prompt/evidence surfacing: Task 6.
- Full verification: Final Verification.

Placeholder scan:
- Literal placeholder scan passes for the banned planning phrases.
- Two steps instruct the implementer to use existing helper names if the local code differs; those are bounded adaptation points, not missing requirements.

Type consistency:
- `requested_primitive`, `RequestedPrimitive`, `raw_input_manifest.json`, `agent_features.jsonl`, `needs_data`, `data_requests`, and `families.<family>.status` use the same names throughout.
