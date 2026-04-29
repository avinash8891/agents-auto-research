"""Direct unit tests for autoresearch_research helpers.

The orchestrators (execute_research_sdk, run_research) are exercised
by the characterization tests via execute_once. This module covers the
pure-helper extractions added in audit PR 4 that don't require a full
conductor round to test.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from autoresearch_research import (
    _backfill_artifact_files_from_latest_dir,
    _check_parsed_for_terminal,
    _classify_round_outcome,
    _exhausted_retries_result,
    load_baseline_config,
    queue_variants,
)
from autoresearch_state import ExperimentRecord
from strategy_family import load_family

# ── _check_parsed_for_terminal ──────────────────────────────────


def test_check_parsed_for_terminal_none_input_is_parse_failed() -> None:
    out = _check_parsed_for_terminal(None)
    assert out is not None
    assert out["status"] == "parse_failed"
    assert out["generated_config"] is None
    assert out["should_stop"] is False
    assert "rejection_reason" in out


def test_check_parsed_for_terminal_conductor_error_propagates_message() -> None:
    parsed = {"status": "conductor_error", "error": "rate limited at retry 2"}
    out = _check_parsed_for_terminal(parsed)
    assert out is not None
    assert out["status"] == "conductor_error"
    assert "rate limited at retry 2" in out["rejection_reason"]


def test_check_parsed_for_terminal_conductor_error_falls_back_to_reasoning() -> None:
    """If `error` is absent, the helper falls back to `reasoning`."""
    parsed = {"status": "conductor_error", "reasoning": "model returned junk"}
    out = _check_parsed_for_terminal(parsed)
    assert out is not None
    assert "model returned junk" in out["rejection_reason"]


def test_check_parsed_for_terminal_no_suggested_theses_returns_completed() -> None:
    parsed = {"reasoning": "nothing more to try"}
    out = _check_parsed_for_terminal(parsed)
    assert out is not None
    assert out["status"] == "completed"
    assert out["should_stop"] is False
    assert "nothing more to try" in out["reasoning"]


def test_check_parsed_for_terminal_passes_should_stop_through() -> None:
    parsed = {"should_stop": True}
    out = _check_parsed_for_terminal(parsed)
    assert out is not None
    assert out["should_stop"] is True


def test_check_parsed_for_terminal_returns_none_when_thesis_present() -> None:
    parsed = {"suggested_theses": [{"thesis_id": "x"}]}
    assert _check_parsed_for_terminal(parsed) is None


# ── _classify_round_outcome ─────────────────────────────────────


def test_classify_round_outcome_should_stop_takes_precedence() -> None:
    result = {"should_stop": True, "generated_config": "x", "generated_config_needs_build": True}
    assert _classify_round_outcome(result) == "stopped"


def test_classify_round_outcome_needs_code() -> None:
    result = {"generated_config_needs_build": True}
    assert _classify_round_outcome(result) == "needs_code"


def test_classify_round_outcome_compiled() -> None:
    result = {"generated_config": "experiments/x/runtime_config.json"}
    assert _classify_round_outcome(result) == "compiled"


def test_classify_round_outcome_rejected() -> None:
    result = {"rejection_reason": "schema mismatch"}
    assert _classify_round_outcome(result) == "rejected"


def test_classify_round_outcome_default_is_conductor_error() -> None:
    result: dict = {}
    assert _classify_round_outcome(result) == "conductor_error"


# ── _exhausted_retries_result ───────────────────────────────────


def test_exhausted_retries_result_returns_thesis_id_when_present() -> None:
    parsed = {"suggested_theses": [{"thesis_id": "trailing_stop"}]}
    out = _exhausted_retries_result(parsed, "validator failed: x")
    assert out["status"] == "thesis_rejected"
    assert out["generated_thesis_id"] == "trailing_stop"
    assert out["rejection_reason"] == "validator failed: x"
    assert out["should_stop"] is False


def test_exhausted_retries_result_returns_unknown_when_no_thesis() -> None:
    out = _exhausted_retries_result(None, "no thesis")
    assert out["generated_thesis_id"] == "unknown"
    assert out["reasoning"] == ""


def test_exhausted_retries_result_carries_reasoning_from_parsed() -> None:
    parsed = {"suggested_theses": [{"thesis_id": "t"}], "reasoning": "ran out of ideas"}
    out = _exhausted_retries_result(parsed, "fb")
    assert out["reasoning"] == "ran out of ideas"


# ── load_baseline_config ────────────────────────────────────────


def test_load_baseline_config_returns_none_when_missing(tmp_path: Path) -> None:
    fam = load_family("ema")
    assert load_baseline_config(tmp_path, fam) is None


def test_load_baseline_config_loads_valid_yaml(tmp_path: Path) -> None:
    fam = load_family("ema")
    (tmp_path / "configs").mkdir()
    (tmp_path / "configs" / "ema_base.yaml").write_text("ema_length: 5\nrr_ratio: 3.0\n")
    out = load_baseline_config(tmp_path, fam)
    assert out == {"ema_length": 5, "rr_ratio": 3.0}


def test_load_baseline_config_returns_none_on_yaml_error(tmp_path: Path) -> None:
    fam = load_family("ema")
    (tmp_path / "configs").mkdir()
    # malformed YAML
    (tmp_path / "configs" / "ema_base.yaml").write_text("ema_length: [\n: : :")
    assert load_baseline_config(tmp_path, fam) is None


# ── _backfill_artifact_files_from_latest_dir ────────────────────


def test_backfill_finds_trades_csv_in_artifact_dir(tmp_path: Path) -> None:
    artifact_dir = tmp_path / "runs" / "job-1" / "abc123"
    artifact_dir.mkdir(parents=True)
    (artifact_dir / "first.trades.csv").write_text("a,b\n1,2\n")
    latest = ExperimentRecord(
        config="x",
        metric=1.0,
        status="keep",
        description="",
        timestamp=100,
        asi={"artifact_dir": "runs/job-1/abc123"},
    )
    controller = SimpleNamespace(root=tmp_path)
    trades, events, diag = _backfill_artifact_files_from_latest_dir(controller, latest, "", "", "")
    assert trades.endswith("first.trades.csv")
    assert events == ""
    assert diag == ""


def test_backfill_finds_strategy_events_parquet(tmp_path: Path) -> None:
    artifact_dir = tmp_path / "runs" / "abc"
    artifact_dir.mkdir(parents=True)
    (artifact_dir / "x.strategy_events.parquet").write_text("")
    latest = ExperimentRecord("x", 1.0, "keep", "", 100, {"artifact_dir": "runs/abc"})
    controller = SimpleNamespace(root=tmp_path)
    trades, events, diag = _backfill_artifact_files_from_latest_dir(controller, latest, "", "", "")
    assert events.endswith("x.strategy_events.parquet")


def test_backfill_falls_back_to_csv_for_strategy_events(tmp_path: Path) -> None:
    artifact_dir = tmp_path / "runs" / "abc"
    artifact_dir.mkdir(parents=True)
    (artifact_dir / "y.strategy_events.csv").write_text("")
    latest = ExperimentRecord("x", 1.0, "keep", "", 100, {"artifact_dir": "runs/abc"})
    controller = SimpleNamespace(root=tmp_path)
    _, events, _ = _backfill_artifact_files_from_latest_dir(controller, latest, "", "", "")
    assert events.endswith("y.strategy_events.csv")


def test_backfill_finds_diagnostics_json(tmp_path: Path) -> None:
    artifact_dir = tmp_path / "runs" / "abc"
    artifact_dir.mkdir(parents=True)
    (artifact_dir / "x.diagnostics.json").write_text("{}")
    latest = ExperimentRecord("x", 1.0, "keep", "", 100, {"artifact_dir": "runs/abc"})
    controller = SimpleNamespace(root=tmp_path)
    _, _, diag = _backfill_artifact_files_from_latest_dir(controller, latest, "", "", "")
    assert diag.endswith("x.diagnostics.json")


def test_backfill_passes_through_when_inputs_already_set(tmp_path: Path) -> None:
    """If the caller already has all three files, the helper returns
    them unchanged."""
    latest = ExperimentRecord("x", 1.0, "keep", "", 100, {"artifact_dir": "runs/abc"})
    controller = SimpleNamespace(root=tmp_path)
    out = _backfill_artifact_files_from_latest_dir(
        controller, latest, "/preset/trades.csv", "/preset/events.parquet", "/preset/diag.json"
    )
    assert out == ("/preset/trades.csv", "/preset/events.parquet", "/preset/diag.json")


def test_backfill_returns_originals_when_artifact_dir_missing(tmp_path: Path) -> None:
    latest = ExperimentRecord("x", 1.0, "keep", "", 100, {"artifact_dir": "runs/does-not-exist"})
    controller = SimpleNamespace(root=tmp_path)
    assert _backfill_artifact_files_from_latest_dir(controller, latest, "", "", "") == ("", "", "")


# ── queue_variants ──────────────────────────────────────────────


@pytest.fixture
def thesis_stub():
    """A pydantic-model-like stub that has the .thesis_id attribute and
    the .model_dump() method that queue_variants calls."""

    class _ThesisStub:
        thesis_id = "stub_thesis"

        def model_dump(self):
            return {"thesis_id": self.thesis_id, "hypothesis": "stub h"}

    return _ThesisStub()


@pytest.fixture
def primary_contract_stub():
    return SimpleNamespace(experiment_id="primary_exp_id_xyz")


def test_queue_variants_skips_factor_one(
    tmp_path: Path, thesis_stub, primary_contract_stub
) -> None:
    """Variants with factor=1.0 are the primary itself and must be skipped."""
    queue_dir = tmp_path / "queue"
    variants = [
        {"_variant_label": "primary", "_variant_factor": 1.0, "ema_length": 5},
        {"_variant_label": "aggressive", "_variant_factor": 0.5, "ema_length": 3},
    ]
    queue_variants(
        tmp_path, queue_dir, variants, thesis_stub, primary_contract_stub, {"ema_length": 5}
    )
    queued_files = sorted(queue_dir.glob("*.json"))
    # Only the aggressive variant gets queued.
    assert len(queued_files) == 1
    artifact = json.loads(queued_files[0].read_text())
    assert artifact["thesis_id"] == "stub_thesis_aggressive"
    assert artifact["status"] == "pending"
    assert artifact["source"] == "multi_variant_probe"
    assert artifact["variant_label"] == "aggressive"
    assert artifact["variant_factor"] == 0.5


def test_queue_variants_writes_runtime_config_per_variant(
    tmp_path: Path, thesis_stub, primary_contract_stub
) -> None:
    queue_dir = tmp_path / "queue"
    variants = [
        {"_variant_label": "agg", "_variant_factor": 0.5, "ema_length": 3},
    ]
    queue_variants(
        tmp_path, queue_dir, variants, thesis_stub, primary_contract_stub, {"ema_length": 5}
    )
    # The runtime_config and thesis.json land under experiments/<hash>/.
    experiments_dirs = list((tmp_path / "experiments").iterdir())
    assert len(experiments_dirs) == 1
    runtime = json.loads((experiments_dirs[0] / "runtime_config.json").read_text())
    # Variant overlays the baseline.
    assert runtime["ema_length"] == 3
    thesis = json.loads((experiments_dirs[0] / "thesis.json").read_text())
    assert thesis["thesis_id"] == "stub_thesis_agg"
    assert thesis["_variant_label"] == "agg"
    assert thesis["_variant_factor"] == 0.5
    assert thesis["_variant_of"] == "primary_exp_id_xyz"
