"""Baseline promotion + walk-forward-gated compounding.

A validated edge becomes the live family baseline overlay so the next thesis
compounds on it instead of the frozen committed seed. Promotion is gated on
walk-forward graduation (out-of-sample), then a re-baseline runs so research
resumes against the compounded baseline.

Metrics mirror the real EMA job-9 run (committed baseline profit_factor 1.8886;
the validated opening-window edge reached 2.0635)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

import autoresearch_orchestration as orch
import compiler_research
from autoresearch_controller import AutoresearchController
from autoresearch_paths import PROMOTED_BASELINE_DIRNAME
from autoresearch_planning import should_terminate
from autoresearch_runtime_paths import research_round_id
from causal_model import save_model
from research_types import AccuracyPoint, CausalModel, ResearchThesis
from screening import ScreeningResult, write_screenings
from strategies import STRATEGIES
from strategy_family import load_family

OVERLAY_REL = f"{PROMOTED_BASELINE_DIRNAME}/ema_base.yaml"


def _ema_runtime_config(**overrides: object) -> dict:
    config = dict(STRATEGIES["ema"].get_defaults())
    config.update(overrides)
    return config


def _controller(tmp_path: Path) -> AutoresearchController:
    (tmp_path / "configs").mkdir(exist_ok=True)
    source = Path(__file__).resolve().parents[1] / "configs" / "ema_base.yaml"
    (tmp_path / "configs" / "ema_base.yaml").write_text(source.read_text())
    controller = AutoresearchController(
        root=tmp_path,
        runtime_root=tmp_path,
        family=load_family("ema"),
        state_path=tmp_path / "ema_autoresearch.next.json",
        current_md_path=tmp_path / "ema_autoresearch.current.md",
        jobs_root=tmp_path / "runtime" / "jobs",
    )
    controller.backtest_run_db.init_session(
        name="ema", metric_name="profit_factor", direction="higher"
    )
    return controller


def _write_walkforward_report(tmp_path: Path, thesis_id: str, *, graduated: bool) -> None:
    wf_dir = tmp_path / "walkforward"
    wf_dir.mkdir(parents=True, exist_ok=True)
    (wf_dir / f"{thesis_id}.json").write_text(
        json.dumps({"thesis_id": thesis_id, "graduated": graduated, "verdict": "graduated"})
    )


def _log_backtest(
    controller: AutoresearchController,
    *,
    thesis_id: str,
    runtime_config: dict,
    metric: float,
    job: int,
    round_number: int,
) -> None:
    """Log one accepted research-round backtest with a real runtime config."""
    controller.backtest_run_db.add_from_sqlite_fields(
        run_id=f"run-{thesis_id}",
        thesis_id=thesis_id,
        config_path=f"runtime/jobs/job-{job}/research/round-{round_number}/selected_config.json",
        runtime_config=runtime_config,
        code_commit="deadbeef",
        data_hash="hash",
        metrics={"profit_factor": metric},
        trade_analysis={},
        strategy_diagnostics={},
        decision_status="keep",
        verdict_status="accepted",
        verdict_summary="validated",
        family="ema",
        job_id=job,
        primary_metric_name="profit_factor",
        primary_metric_value=metric,
        research_round_id=research_round_id(job, round_number),
        research_round_number=round_number,
    )


# ── write_baseline_overlay (the promotion primitive) ──────────────────────────


def test_write_baseline_overlay_persists_validated_config(tmp_path: Path) -> None:
    controller = _controller(tmp_path)
    improved = _ema_runtime_config(ema_length=9)

    overlay_rel = orch.write_baseline_overlay(controller, improved)

    assert overlay_rel == OVERLAY_REL
    overlay = tmp_path / PROMOTED_BASELINE_DIRNAME / "ema_base.yaml"
    assert yaml.safe_load(overlay.read_text()) == improved


def test_write_baseline_overlay_skips_empty_config(tmp_path: Path) -> None:
    controller = _controller(tmp_path)
    assert orch.write_baseline_overlay(controller, {}) is None
    assert not (tmp_path / PROMOTED_BASELINE_DIRNAME / "ema_base.yaml").exists()


def test_write_baseline_overlay_skips_config_invalid_on_this_release(tmp_path: Path) -> None:
    controller = _controller(tmp_path)
    bad = _ema_runtime_config(primitive_not_in_this_release=True)
    assert orch.write_baseline_overlay(controller, bad) is None
    assert not (tmp_path / PROMOTED_BASELINE_DIRNAME / "ema_base.yaml").exists()


# ── promote_graduated_and_rebaseline (the walk-forward-gated step) ─────────────


def test_promote_graduated_picks_best_overlay_and_forces_rebaseline(tmp_path: Path) -> None:
    controller = _controller(tmp_path)
    controller.write_state({"job": 9, "research_round": 3})
    # Two graduated candidates; the higher in-sample profit_factor should win.
    _log_backtest(
        controller,
        thesis_id="opening-window-short",
        runtime_config=_ema_runtime_config(ema_length=9),
        metric=2.0635,
        job=9,
        round_number=1,
    )
    _log_backtest(
        controller,
        thesis_id="gap-filter",
        runtime_config=_ema_runtime_config(rr_ratio=2.5),
        metric=1.95,
        job=9,
        round_number=2,
    )
    _write_walkforward_report(tmp_path, "opening-window-short", graduated=True)
    _write_walkforward_report(tmp_path, "gap-filter", graduated=True)

    record = orch.promote_graduated_and_rebaseline(controller, controller.read_state())

    assert record is not None
    assert record["promoted_from"] == "opening-window-short"
    overlay = tmp_path / PROMOTED_BASELINE_DIRNAME / "ema_base.yaml"
    assert yaml.safe_load(overlay.read_text())["ema_length"] == 9
    # Re-baseline was forced on the promoted overlay, research_round reset to 0.
    state = controller.read_state()
    assert state["state"] == "running"
    assert state["next_action"]["type"] == "run_round"
    assert state["next_action"]["config"] == OVERLAY_REL
    assert state["research_round"] == 0


def test_promote_graduated_noop_when_nothing_graduated(tmp_path: Path) -> None:
    controller = _controller(tmp_path)
    controller.write_state({"job": 9, "research_round": 3})
    _log_backtest(
        controller,
        thesis_id="opening-window-short",
        runtime_config=_ema_runtime_config(ema_length=9),
        metric=2.0635,
        job=9,
        round_number=1,
    )
    _write_walkforward_report(tmp_path, "opening-window-short", graduated=False)  # demoted

    assert orch.promote_graduated_and_rebaseline(controller, controller.read_state()) is None
    assert not (tmp_path / PROMOTED_BASELINE_DIRNAME / "ema_base.yaml").exists()


# ── no-loop guarantee: promotion resets the plateau so walk-forward can't re-fire ──


def _plateau_model() -> CausalModel:
    """A causal model whose holdout skill has flatlined for plateau_rounds (5) — the
    condition that, with all-fail screenings, triggers the plateau → walk-forward path."""
    return CausalModel(
        family="ema",
        version=5,
        accuracy_history=[
            AccuracyPoint(
                round_number=n,
                model_version=n,
                pnl_weighted_accuracy=0.50 + skill,
                naive_accuracy=0.50,
                skill=skill,
                holdout_trade_count=100,
            )
            for n, skill in [(1, 0.100), (2, 0.104), (3, 0.108), (4, 0.109), (5, 0.109)]
        ],
    )


def test_promotion_resets_plateau_so_walkforward_does_not_re_fire(tmp_path: Path) -> None:
    controller = _controller(tmp_path)
    controller.write_state({"job": 9, "research_round": 5})
    save_model(_plateau_model(), runtime_root=tmp_path)
    db_path = tmp_path / "ema_backtest_runs.db"
    for round_number in range(1, 6):
        write_screenings(
            db_path,
            [
                ScreeningResult(
                    rule="side == 'short' and bars_since_open == 1",
                    verdict="kill_no_lift",
                    sample_count=40,
                    flagged_loss_rate=0.51,
                    base_loss_rate=0.50,
                    lift=0.01,
                    p_value=0.80,
                    overlap_with=None,
                )
            ],
            round_number=round_number,
            job_id=9,
        )
    family = load_family("ema")

    # Plateau holds → the engine would (correctly) go to walk-forward.
    assert should_terminate(tmp_path, family, tmp_path / "q", tmp_path / "r", [], job=9) is True

    _log_backtest(
        controller,
        thesis_id="opening-window-short",
        runtime_config=_ema_runtime_config(ema_length=9),
        metric=2.0635,
        job=9,
        round_number=1,
    )
    _write_walkforward_report(tmp_path, "opening-window-short", graduated=True)
    assert orch.promote_graduated_and_rebaseline(controller, controller.read_state()) is not None

    # After promotion the plateau window is reset, so should_terminate flips to False:
    # research resumes against the compounded baseline instead of re-entering
    # walk-forward forever.
    assert should_terminate(tmp_path, family, tmp_path / "q", tmp_path / "r", [], job=9) is False


# ── base-config reads prefer the overlay ──────────────────────────────────────


def test_load_base_runtime_config_prefers_overlay_over_committed_seed(tmp_path: Path) -> None:
    (tmp_path / "configs").mkdir()
    committed = _ema_runtime_config(ema_length=21)
    (tmp_path / "configs" / "ema_base.yaml").write_text(yaml.safe_dump(committed))
    overlay_dir = tmp_path / PROMOTED_BASELINE_DIRNAME
    overlay_dir.mkdir()
    promoted = _ema_runtime_config(ema_length=9)
    (overlay_dir / "ema_base.yaml").write_text(yaml.safe_dump(promoted))

    thesis = ResearchThesis(
        thesis_id="compounds-on-promoted",
        strategy_family="ema",
        hypothesis="Shorter EMA after the open reduces noisy crosses.",
        mechanism="Build on the already-validated opening-window config.",
        config_changes={"rr_ratio": 2.0},
    )

    loaded = compiler_research._load_base_runtime_config(tmp_path, thesis, runtime_root=tmp_path)
    assert loaded["ema_length"] == 9

    seed = compiler_research._load_base_runtime_config(tmp_path, thesis)
    assert seed["ema_length"] == 21


def test_load_base_runtime_config_fails_loud_when_overlay_needs_absent_builder_code(
    tmp_path: Path,
) -> None:
    # D guard: an overlay promoted on another release carries a key this release's
    # strategy can't honor (the builder code is absent). Reading it must fail loud
    # with remediation, not silently or with a misleading thesis error.
    (tmp_path / "configs").mkdir()
    (tmp_path / "configs" / "ema_base.yaml").write_text(yaml.safe_dump(_ema_runtime_config()))
    overlay_dir = tmp_path / PROMOTED_BASELINE_DIRNAME
    overlay_dir.mkdir()
    needs_builder_code = _ema_runtime_config(primitive_not_in_this_release=True)
    (overlay_dir / "ema_base.yaml").write_text(yaml.safe_dump(needs_builder_code))

    thesis = ResearchThesis(
        thesis_id="redeployed-without-code",
        strategy_family="ema",
        hypothesis="Compound on a baseline that needs a built primitive.",
        mechanism="Reads the promoted overlay on a release missing the code.",
        config_changes={"rr_ratio": 2.0},
    )

    with pytest.raises(ValueError, match="not supported by this release"):
        compiler_research._load_base_runtime_config(tmp_path, thesis, runtime_root=tmp_path)
