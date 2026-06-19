"""Baseline promotion: a validated best config becomes the live family baseline
overlay so the next thesis compounds on it instead of the frozen committed seed.

Metrics mirror the real EMA job-9 run (committed baseline profit_factor 1.8886;
the validated opening-window edge reached 2.0635)."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import yaml

import autoresearch_orchestration as orch
import compiler_research
from autoresearch_paths import PROMOTED_BASELINE_DIRNAME
from research_types import ResearchThesis
from strategies import STRATEGIES
from strategy_family import load_family

COMMITTED_BASELINE = "configs/ema_base.yaml"
OVERLAY_REL = f"{PROMOTED_BASELINE_DIRNAME}/ema_base.yaml"


def _ema_runtime_config(**overrides: object) -> dict:
    config = dict(STRATEGIES["ema"].get_defaults())
    config.update(overrides)
    return config


def _controller(root: Path) -> SimpleNamespace:
    return SimpleNamespace(root=root, runtime_root=root, family=load_family("ema"))


def _write_validated_round_config(root: Path, runtime_config: dict) -> str:
    round_dir = root / "runtime" / "jobs" / "job-9" / "research" / "round-1"
    round_dir.mkdir(parents=True, exist_ok=True)
    (round_dir / "selected_config.json").write_text(json.dumps(runtime_config) + "\n")
    return "runtime/jobs/job-9/research/round-1/selected_config.json"


def test_promote_writes_overlay_when_research_round_beats_baseline(tmp_path: Path) -> None:
    controller = _controller(tmp_path)
    improved = _ema_runtime_config(ema_length=9)
    config_rel = _write_validated_round_config(tmp_path, improved)
    state = {"current_best": {"config": config_rel, "metric": 2.0635}}

    record = orch.promote_baseline_if_improved(controller, state)

    overlay = tmp_path / PROMOTED_BASELINE_DIRNAME / "ema_base.yaml"
    assert record == {
        "promoted_config": OVERLAY_REL,
        "promoted_from": config_rel,
        "metric": 2.0635,
    }
    assert state["promoted_baseline"] == record
    assert yaml.safe_load(overlay.read_text()) == improved


def test_promote_noop_when_best_is_the_committed_baseline(tmp_path: Path) -> None:
    controller = _controller(tmp_path)
    state = {"current_best": {"config": COMMITTED_BASELINE, "metric": 1.8886}}

    record = orch.promote_baseline_if_improved(controller, state)

    assert record is None
    assert "promoted_baseline" not in state
    assert not (tmp_path / PROMOTED_BASELINE_DIRNAME / "ema_base.yaml").exists()


def test_promote_noop_when_best_already_the_overlay(tmp_path: Path) -> None:
    controller = _controller(tmp_path)
    state = {"current_best": {"config": OVERLAY_REL, "metric": 2.0635}}

    assert orch.promote_baseline_if_improved(controller, state) is None
    assert not (tmp_path / PROMOTED_BASELINE_DIRNAME / "ema_base.yaml").exists()


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

    # Without runtime_root the committed seed is used (back-compat).
    seed = compiler_research._load_base_runtime_config(tmp_path, thesis)
    assert seed["ema_length"] == 21
