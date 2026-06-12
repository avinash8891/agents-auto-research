"""Direct unit tests for autoresearch_research helpers.

The orchestrators (execute_research_sdk, run_research) are exercised
by the characterization tests via execute_once. This module covers the
pure-helper extractions added in audit PR 4 that don't require a full
conductor round to test.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from autoresearch_research import (
    _backfill_artifact_files_from_latest_dir,
    _check_parsed_for_terminal,
    _classify_round_outcome,
    _exhausted_retries_result,
    _structured_validation_failure,
    load_baseline_config,
    queue_variants,
)
from autoresearch_state import BacktestResultRecord
from research_types import ConductorResult
from strategy_family import load_family

# ── _check_parsed_for_terminal ──────────────────────────────────


def test_check_parsed_for_terminal_none_input_is_parse_failed() -> None:
    out = _check_parsed_for_terminal(None)
    assert out is not None
    assert out["status"] == "parse_failed"
    assert out["generated_config"] is None
    assert "should_stop" not in out
    assert "validation_failure_reason" in out


def test_check_parsed_for_terminal_conductor_error_propagates_message() -> None:
    result = ConductorResult(status="conductor_error", error="rate limited at retry 2")
    out = _check_parsed_for_terminal(result)
    assert out is not None
    assert out["status"] == "conductor_error"
    assert "rate limited at retry 2" in out["validation_failure_reason"]


def test_structured_validation_failure_classifies_validator_errors() -> None:
    rejection = _structured_validation_failure(
        source="validator",
        message="missing required config_changes",
    )

    assert rejection == {
        "source": "validator",
        "code": "validator_missing_required_field",
        "message": "missing required config_changes",
    }


def test_check_parsed_for_terminal_conductor_error_falls_back_to_reasoning() -> None:
    """If `error` is absent, the helper falls back to `reasoning`."""
    result = ConductorResult(status="conductor_error", reasoning="model returned junk")
    out = _check_parsed_for_terminal(result)
    assert out is not None
    assert "model returned junk" in out["validation_failure_reason"]


def test_check_parsed_for_terminal_returns_none_when_thesis_present() -> None:
    result = ConductorResult(status="ok", thesis={"thesis_id": "x"})
    assert _check_parsed_for_terminal(result) is None


# ── _classify_round_outcome ─────────────────────────────────────


def test_classify_round_outcome_needs_code() -> None:
    result = {"generated_config_needs_build": True}
    assert _classify_round_outcome(result) == "needs_code"


def test_classify_round_outcome_compiled() -> None:
    result = {"generated_config": "runtime/jobs/job-1/research/round-1/selected_config.json"}
    assert _classify_round_outcome(result) == "compiled"


def test_classify_round_outcome_rejected() -> None:
    result = {"validation_failure_reason": "schema mismatch"}
    assert _classify_round_outcome(result) == "rejected"


def test_classify_round_outcome_completed_without_config_is_completed() -> None:
    result = {"status": "completed", "generated_config": None}
    assert _classify_round_outcome(result) == "completed"


def test_classify_round_outcome_default_is_conductor_error() -> None:
    result: dict = {}
    assert _classify_round_outcome(result) == "conductor_error"


# ── _exhausted_retries_result ───────────────────────────────────


def test_exhausted_retries_result_returns_thesis_id_when_present() -> None:
    result = ConductorResult(status="ok", thesis={"thesis_id": "trailing_stop"})
    out = _exhausted_retries_result(result, "validator failed: x")
    assert out["status"] == "thesis_rejected"
    assert out["generated_thesis_id"] == "trailing_stop"
    assert out["validation_failure_reason"] == "validator failed: x"
    assert "should_stop" not in out


def test_exhausted_retries_result_returns_unknown_when_no_thesis() -> None:
    out = _exhausted_retries_result(None, "no thesis")
    assert out["generated_thesis_id"] == "unknown"
    assert out["reasoning"] == ""


def test_exhausted_retries_result_carries_reasoning_from_parsed() -> None:
    result = ConductorResult(status="ok", thesis={"thesis_id": "t"}, reasoning="ran out of ideas")
    out = _exhausted_retries_result(result, "fb")
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


def test_load_baseline_config_raises_on_yaml_error(tmp_path: Path) -> None:
    fam = load_family("ema")
    (tmp_path / "configs").mkdir()
    # malformed YAML
    (tmp_path / "configs" / "ema_base.yaml").write_text("ema_length: [\n: : :")
    with pytest.raises(ValueError, match="BASELINE_CONFIG_LOAD_FAILED"):
        load_baseline_config(tmp_path, fam)


# ── _backfill_artifact_files_from_latest_dir ────────────────────


def test_backfill_finds_trades_csv_in_artifact_dir(tmp_path: Path) -> None:
    artifact_dir = tmp_path / "runs" / "job-1" / "abc123"
    artifact_dir.mkdir(parents=True)
    (artifact_dir / "first.trades.csv").write_text("a,b\n1,2\n")
    latest = BacktestResultRecord(
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
    latest = BacktestResultRecord("x", 1.0, "keep", "", 100, {"artifact_dir": "runs/abc"})
    controller = SimpleNamespace(root=tmp_path)
    trades, events, diag = _backfill_artifact_files_from_latest_dir(controller, latest, "", "", "")
    assert events.endswith("x.strategy_events.parquet")


def test_backfill_falls_back_to_csv_for_strategy_events(tmp_path: Path) -> None:
    artifact_dir = tmp_path / "runs" / "abc"
    artifact_dir.mkdir(parents=True)
    (artifact_dir / "y.strategy_events.csv").write_text("")
    latest = BacktestResultRecord("x", 1.0, "keep", "", 100, {"artifact_dir": "runs/abc"})
    controller = SimpleNamespace(root=tmp_path)
    _, events, _ = _backfill_artifact_files_from_latest_dir(controller, latest, "", "", "")
    assert events.endswith("y.strategy_events.csv")


def test_backfill_finds_diagnostics_json(tmp_path: Path) -> None:
    artifact_dir = tmp_path / "runs" / "abc"
    artifact_dir.mkdir(parents=True)
    (artifact_dir / "x.diagnostics.json").write_text("{}")
    latest = BacktestResultRecord("x", 1.0, "keep", "", 100, {"artifact_dir": "runs/abc"})
    controller = SimpleNamespace(root=tmp_path)
    _, _, diag = _backfill_artifact_files_from_latest_dir(controller, latest, "", "", "")
    assert diag.endswith("x.diagnostics.json")


def test_backfill_passes_through_when_inputs_already_set(tmp_path: Path) -> None:
    """If the caller already has all three files, the helper returns
    them unchanged."""
    latest = BacktestResultRecord("x", 1.0, "keep", "", 100, {"artifact_dir": "runs/abc"})
    controller = SimpleNamespace(root=tmp_path)
    out = _backfill_artifact_files_from_latest_dir(
        controller, latest, "/preset/trades.csv", "/preset/events.parquet", "/preset/diag.json"
    )
    assert out == ("/preset/trades.csv", "/preset/events.parquet", "/preset/diag.json")


def test_backfill_returns_originals_when_artifact_dir_missing(tmp_path: Path) -> None:
    latest = BacktestResultRecord(
        "x", 1.0, "keep", "", 100, {"artifact_dir": "runs/does-not-exist"}
    )
    controller = SimpleNamespace(root=tmp_path)
    assert _backfill_artifact_files_from_latest_dir(controller, latest, "", "", "") == ("", "", "")


def test_backfill_ignores_absolute_artifact_dir_outside_controller_root(tmp_path: Path) -> None:
    outside = tmp_path.parent / f"{tmp_path.name}-outside"
    outside.mkdir()
    (outside / "trades.csv").write_text("entry_date,pnl_pct\n")
    latest = BacktestResultRecord("x", 1.0, "keep", "", 100, {"artifact_dir": str(outside)})
    controller = SimpleNamespace(root=tmp_path)

    assert _backfill_artifact_files_from_latest_dir(controller, latest, "", "", "") == ("", "", "")


def test_backfill_prefers_matching_artifacts_when_multiple_exist(tmp_path: Path) -> None:
    artifact_dir = tmp_path / "runs" / "abc"
    artifact_dir.mkdir(parents=True)
    (artifact_dir / "other.strategy_events.parquet").write_text("")
    (artifact_dir / "x.strategy_events.parquet").write_text("")
    latest = BacktestResultRecord("x", 1.0, "keep", "", 100, {"artifact_dir": "runs/abc"})
    controller = SimpleNamespace(root=tmp_path)

    _, events, _ = _backfill_artifact_files_from_latest_dir(controller, latest, "", "", "")

    assert events.endswith("x.strategy_events.parquet")


def test_adapter_export_packages_leave_no_tmp_artifacts(tmp_path: Path) -> None:
    from autoresearch_research import _write_export_package

    package = {
        "files": {"a.json": {"hello": "world"}},
        "meta": {"name": "pkg"},
    }

    _write_export_package(tmp_path, "halo", package)

    assert not list(tmp_path.rglob("*.tmp"))


def test_load_data_flat_mode_applies_date_filters(tmp_path: Path) -> None:
    import pandas as pd

    from data_loader import load_data

    df = pd.DataFrame(
        {
            "Symbol": ["SPY", "SPY"],
            "Open": [10.0, 20.0],
            "High": [11.0, 21.0],
            "Low": [9.0, 19.0],
            "Close": [10.5, 20.5],
            "Volume": [100, 200],
        },
        index=pd.to_datetime(["2024-01-01", "2024-01-03"]),
    )
    path = tmp_path / "flat.parquet"
    df.to_parquet(path)

    batch = load_data(str(tmp_path), start_date="2024-01-02", end_date="2024-01-03")

    assert list(batch["close"].index) == [pd.Timestamp("2024-01-03")]


def test_load_data_flat_mode_normalizes_timezone_and_session(tmp_path: Path) -> None:
    import pandas as pd

    from data_loader import load_data

    df = pd.DataFrame(
        {
            "Symbol": ["SPY", "SPY"],
            "Open": [10.0, 20.0],
            "High": [11.0, 21.0],
            "Low": [9.0, 19.0],
            "Close": [10.5, 20.5],
            "Volume": [100, 200],
        },
        index=pd.to_datetime(["2024-01-02 14:30", "2024-01-02 21:00"], utc=True),
    )
    (tmp_path / "flat.parquet").write_bytes(b"")
    df.to_parquet(tmp_path / "flat.parquet")

    batch = load_data(str(tmp_path))

    assert list(batch["close"].index) == [pd.Timestamp("2024-01-02 09:30")]


def test_load_data_per_symbol_mode_normalizes_timezone_and_session(tmp_path: Path) -> None:
    import pandas as pd

    from data_loader import load_data

    sym_dir = tmp_path / "SPY"
    sym_dir.mkdir()
    df = pd.DataFrame(
        {
            "Open": [10.0, 20.0],
            "High": [11.0, 21.0],
            "Low": [9.0, 19.0],
            "Close": [10.5, 20.5],
            "Volume": [100, 200],
        },
        index=pd.to_datetime(["2024-01-02 14:30", "2024-01-02 21:00"], utc=True),
    )
    df.to_parquet(sym_dir / "bars.parquet")

    batch = load_data(str(tmp_path))

    assert list(batch["close"].index) == [pd.Timestamp("2024-01-02 09:30")]


def test_load_data_missing_path_raises_instead_of_exiting() -> None:
    import pytest

    from data_loader import load_data

    with pytest.raises(FileNotFoundError):
        load_data("/definitely/not/a/real/path")


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
    return SimpleNamespace(contract_id="primary_exp_id_xyz")


def test_queue_variants_fails_loudly_for_removed_variant_backtests(
    tmp_path: Path, thesis_stub, primary_contract_stub
) -> None:
    with pytest.raises(RuntimeError, match="no longer supported"):
        queue_variants(
            tmp_path,
            tmp_path / "queue",
            [{"_variant_label": "agg", "_variant_factor": 0.5, "ema_length": 3}],
            thesis_stub,
            primary_contract_stub,
            {"ema_length": 5, "rr_ratio": 3.0},
        )


def test_queue_variants_leaves_no_artifacts_when_rejected(
    tmp_path: Path, thesis_stub, primary_contract_stub
) -> None:
    with pytest.raises(RuntimeError):
        queue_variants(
            tmp_path,
            tmp_path / "queue",
            [{"_variant_label": "agg", "_variant_factor": 0.5, "ema_length": 3}],
            thesis_stub,
            primary_contract_stub,
            {"ema_length": 5, "rr_ratio": 3.0},
        )

    assert not list(tmp_path.rglob("*.tmp"))
    assert not (tmp_path / "experiments").exists()
