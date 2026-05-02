"""Unit tests for autoresearch_planning.

Project rule G: real production names — real thesis families ("universe",
"entry", "exit", "regime"), real config-path conventions, real status
strings ("keep", "discard", "pending", "completed").
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from autoresearch_planning import (
    build_research_failure_state,
    check_baseline_rerun,
    generate_combination_candidates,
    list_known_variant_configs,
    parse_ideas_backlog,
    pending_configs,
    should_terminate,
    thesis_family_for,
    thesis_statuses,
)
from autoresearch_state import ExperimentRecord
from strategy_family import load_family


@pytest.fixture
def ema_family():
    return load_family("ema")


@pytest.fixture
def orb_family():
    return load_family("orb")


# ── list_known_variant_configs / pending_configs ────────────────


def test_list_known_variant_configs_returns_empty_when_no_files(tmp_path: Path, ema_family) -> None:
    assert list_known_variant_configs(tmp_path, ema_family) == []


def test_list_known_variant_configs_picks_up_yaml_files_in_variants_dir(
    tmp_path: Path, ema_family
) -> None:
    variants = tmp_path / "configs" / "variants"
    variants.mkdir(parents=True)
    (variants / "ema_aggressive.yaml").write_text("ema_length: 3\n")
    (variants / "ema_conservative.yaml").write_text("ema_length: 7\n")
    out = list_known_variant_configs(tmp_path, ema_family)
    assert "configs/variants/ema_aggressive.yaml" in out
    assert "configs/variants/ema_conservative.yaml" in out


def test_list_known_variant_configs_skips_readme_keep(tmp_path: Path, ema_family) -> None:
    variants = tmp_path / "configs" / "variants"
    variants.mkdir(parents=True)
    (variants / "ema_real.yaml").write_text("k: v\n")
    (variants / "README.keep").write_text("keepalive\n")
    out = list_known_variant_configs(tmp_path, ema_family)
    assert out == ["configs/variants/ema_real.yaml"]


def test_pending_configs_returns_only_pending_run_queue_configs(tmp_path: Path, ema_family) -> None:
    run_queue_dir = tmp_path / "queue"
    run_queue_dir.mkdir(parents=True)
    config_a = "configs/variants/ema_a.json"
    config_b = "configs/variants/ema_b.json"
    (tmp_path / config_a).parent.mkdir(parents=True, exist_ok=True)
    (tmp_path / config_a).write_text('{"runtime_config": {"ema_length": 5}}\n')
    (tmp_path / config_b).write_text('{"runtime_config": {"ema_length": 7}}\n')
    (run_queue_dir / "a.json").write_text(
        json.dumps({"config": config_a, "status": "pending", "thesis_id": "a"})
    )
    (run_queue_dir / "b.json").write_text(
        json.dumps({"config": config_b, "status": "pending", "thesis_id": "b"})
    )
    results = [
        ExperimentRecord(config_a, 1.0, "keep", "", 1, {}),
    ]
    assert pending_configs(tmp_path, ema_family, run_queue_dir, results) == [config_b]


# ── thesis_statuses overlay precedence ──────────────────────────


def test_thesis_statuses_run_queue_overlays_pending_default(tmp_path: Path, ema_family) -> None:
    variants = tmp_path / "configs" / "variants"
    variants.mkdir(parents=True)
    (variants / "ema_a.yaml").write_text("k: v\n")

    queue_dir = tmp_path / "queue"
    queue_dir.mkdir()
    (queue_dir / "ema_a.json").write_text(
        json.dumps(
            {
                "config": "configs/variants/ema_a.yaml",
                "status": "pending",
                "thesis_id": "ema_a",
            }
        )
    )

    statuses = thesis_statuses(tmp_path, ema_family, queue_dir, [])
    assert statuses["configs/variants/ema_a.yaml"]["source"] == "run_queue"
    assert statuses["configs/variants/ema_a.yaml"]["thesis_id"] == "ema_a"


def test_thesis_statuses_result_overlays_run_queue(tmp_path: Path, ema_family) -> None:
    queue_dir = tmp_path / "queue"
    queue_dir.mkdir()
    (queue_dir / "x.json").write_text(
        json.dumps({"config": "configs/variants/ema_x.yaml", "status": "pending", "thesis_id": "x"})
    )
    results = [
        ExperimentRecord("configs/variants/ema_x.yaml", 1.42, "keep", "kept x", 100, {}),
    ]
    statuses = thesis_statuses(tmp_path, ema_family, queue_dir, results)
    s = statuses["configs/variants/ema_x.yaml"]
    assert s["status"] == "keep"
    assert s["last_metric"] == 1.42
    assert s["last_timestamp"] == 100


# ── parse_ideas_backlog ─────────────────────────────────────────


def test_parse_ideas_backlog_returns_empty_when_file_missing(tmp_path: Path, ema_family) -> None:
    assert parse_ideas_backlog(tmp_path / "missing.md", ema_family) == []


def test_parse_ideas_backlog_extracts_real_thesis_slugs(
    real_ideas_backlog_path: Path, orb_family
) -> None:
    candidates = parse_ideas_backlog(real_ideas_backlog_path, orb_family)
    slugs = [c["slug"] for c in candidates]
    assert "spy_only" in slugs
    assert "trailing_stop" in slugs
    assert "skip_chop" in slugs
    # Family inference from THESIS_FAMILY or section header should match.
    by_slug = {c["slug"]: c for c in candidates}
    assert by_slug["spy_only"]["family"] == "universe"
    assert by_slug["trailing_stop"]["family"] == "exit"
    assert by_slug["skip_chop"]["family"] == "regime"


def test_parse_ideas_backlog_uses_orb_prefix_for_orb_family(
    real_ideas_backlog_path: Path, orb_family
) -> None:
    """For the orb family, ideas backlog candidates emit orb_-prefixed paths."""
    candidates = parse_ideas_backlog(real_ideas_backlog_path, orb_family)
    by_slug = {c["slug"]: c for c in candidates}
    assert by_slug["spy_only"]["config"] == "configs/variants/orb_spy_only.yaml"
    assert by_slug["trailing_stop"]["config"] == "configs/variants/orb_trailing_stop.yaml"


def test_parse_ideas_backlog_uses_ema_prefix_for_ema_family(
    real_ideas_backlog_path: Path, ema_family
) -> None:
    """PR 5 fix: for the ema family, ideas backlog candidates now emit
    ema_-prefixed paths instead of orb_-prefixed (the audit's family-
    correctness bug)."""
    candidates = parse_ideas_backlog(real_ideas_backlog_path, ema_family)
    by_slug = {c["slug"]: c for c in candidates}
    assert by_slug["spy_only"]["config"] == "configs/variants/ema_spy_only.yaml"
    assert by_slug["trailing_stop"]["config"] == "configs/variants/ema_trailing_stop.yaml"


# ── thesis_family_for ───────────────────────────────────────────


def test_thesis_family_for_known_slug_uses_thesis_family_map(tmp_path: Path, orb_family) -> None:
    assert (
        thesis_family_for(
            "configs/variants/orb_spy_only.yaml", orb_family, tmp_path / "p", tmp_path
        )
        == "universe"
    )
    assert (
        thesis_family_for(
            "configs/variants/orb_trailing_stop.yaml", orb_family, tmp_path / "p", tmp_path
        )
        == "exit"
    )
    assert (
        thesis_family_for(
            "configs/variants/orb_skip_chop.yaml", orb_family, tmp_path / "p", tmp_path
        )
        == "regime"
    )


def test_thesis_family_for_falls_back_to_proposal_artifact(tmp_path: Path, orb_family) -> None:
    proposals = tmp_path / "ema-proposals"
    proposals.mkdir()
    (proposals / "novel_thesis.json").write_text(
        json.dumps({"thesis_id": "novel_thesis", "family": "entry"})
    )
    assert (
        thesis_family_for("configs/variants/orb_novel_thesis.yaml", orb_family, proposals, tmp_path)
        == "entry"
    )


def test_generate_theses_from_ideas_compiles_family_keyed_proposal(
    tmp_path: Path, orb_family
) -> None:
    ideas_path = tmp_path / "autoresearch.ideas.md"
    ideas_path.write_text("### Entry theses\n- `novel_thesis`\n")
    variants = tmp_path / "configs" / "variants"
    variants.mkdir(parents=True)
    (variants / "orb_novel_thesis.yaml").write_text("opening_range_minutes: 5\n")
    proposals_dir = tmp_path / orb_family.proposals_dirname
    run_queue_dir = tmp_path / orb_family.run_queue_dirname

    from autoresearch_planning import generate_theses_from_ideas

    generated = generate_theses_from_ideas(
        tmp_path, orb_family, ideas_path, run_queue_dir, proposals_dir, []
    )

    assert generated == ["configs/variants/orb_novel_thesis.yaml"]
    assert (proposals_dir / "novel_thesis.json").exists()
    assert (tmp_path / orb_family.compilations_dirname / "novel_thesis.json").exists()


def test_generate_theses_from_ideas_leaves_no_tmp_artifacts(tmp_path: Path, orb_family) -> None:
    ideas_path = tmp_path / "autoresearch.ideas.md"
    ideas_path.write_text("### Entry theses\n- `novel_thesis`\n")
    variants = tmp_path / "configs" / "variants"
    variants.mkdir(parents=True)
    (variants / "orb_novel_thesis.yaml").write_text("opening_range_minutes: 5\n")
    proposals_dir = tmp_path / orb_family.proposals_dirname
    run_queue_dir = tmp_path / orb_family.run_queue_dirname

    from autoresearch_planning import generate_theses_from_ideas

    generate_theses_from_ideas(tmp_path, orb_family, ideas_path, run_queue_dir, proposals_dir, [])

    assert not list(tmp_path.rglob("*.tmp"))


def test_thesis_family_for_returns_unknown_when_neither_match(tmp_path: Path, orb_family) -> None:
    assert (
        thesis_family_for("configs/variants/orb_mystery.yaml", orb_family, tmp_path / "p", tmp_path)
        == "unknown"
    )


# ── COMBINATION_RULES truth table ───────────────────────────────


def test_combination_rules_universe_plus_exit_allowed() -> None:
    assert load_family("orb").combination_rules[("universe", "exit")] == "allowed"


def test_combination_rules_disallows_two_universes() -> None:
    assert load_family("orb").combination_rules[("universe", "universe")] == "disallowed"


def test_combination_rules_review_required_for_two_exits() -> None:
    assert load_family("orb").combination_rules[("exit", "exit")] == "review_required"


def test_thesis_family_map_covers_known_slugs() -> None:
    # Any future thesis slug added to the loop must be classified.
    thesis_family_by_slug = load_family("orb").thesis_family_by_slug
    assert thesis_family_by_slug["spy_only"] == "universe"
    assert thesis_family_by_slug["trailing_stop"] == "exit"
    assert thesis_family_by_slug["trend_filter"] == "regime"
    assert thesis_family_by_slug["follow_through"] == "entry"


# ── generate_combination_candidates ─────────────────────────────


def test_generate_combination_candidates_requires_at_least_two_keeps(
    tmp_path: Path, orb_family
) -> None:
    proposals_dir = tmp_path / "p"
    one_keep = [ExperimentRecord("configs/variants/orb_spy_only.yaml", 1.0, "keep", "", 1, {})]
    assert generate_combination_candidates(tmp_path, orb_family, proposals_dir, one_keep) == []


def test_generate_combination_candidates_skips_disallowed_pairs(tmp_path: Path, orb_family) -> None:
    """Two universe-family configs cannot combine (rule = disallowed)."""
    variants = tmp_path / "configs" / "variants"
    variants.mkdir(parents=True)
    (variants / "orb_spy_only.yaml").write_text("symbols: SPY\n")
    (variants / "orb_stocks_in_play.yaml").write_text("symbols: stocks_in_play\n")
    proposals_dir = tmp_path / "ema-proposals"

    results = [
        ExperimentRecord("configs/variants/orb_spy_only.yaml", 1.0, "keep", "", 1, {}),
        ExperimentRecord("configs/variants/orb_stocks_in_play.yaml", 1.1, "keep", "", 2, {}),
    ]
    out = generate_combination_candidates(tmp_path, orb_family, proposals_dir, results)
    assert out == []


def test_generate_combination_candidates_creates_yaml_for_allowed_pair(
    tmp_path: Path, orb_family
) -> None:
    """Allowed pair (universe + exit) produces a merged YAML and a proposal JSON."""
    variants = tmp_path / "configs" / "variants"
    variants.mkdir(parents=True)
    (variants / "orb_spy_only.yaml").write_text("symbols: SPY\n")
    (variants / "orb_trailing_stop.yaml").write_text("trailing_stop: 0.5\n")
    proposals_dir = tmp_path / "orb-proposals"

    results = [
        ExperimentRecord("configs/variants/orb_spy_only.yaml", 1.0, "keep", "", 1, {}),
        ExperimentRecord("configs/variants/orb_trailing_stop.yaml", 1.2, "keep", "", 2, {}),
    ]
    out = generate_combination_candidates(tmp_path, orb_family, proposals_dir, results)
    assert out == ["configs/variants/orb_spy_only_x_trailing_stop.yaml"]
    combo_path = tmp_path / out[0]
    assert combo_path.exists()
    proposal_path = proposals_dir / "spy_only_x_trailing_stop.json"
    assert proposal_path.exists()
    proposal = json.loads(proposal_path.read_text())
    assert proposal["family"] == "combination"


def test_generate_combination_candidates_does_not_publish_invalid_yaml_combo(
    tmp_path: Path, orb_family
) -> None:
    variants = tmp_path / "configs" / "variants"
    variants.mkdir(parents=True)
    (variants / "orb_spy_only.yaml").write_text("symbols: SPY\n")
    (variants / "orb_trailing_stop.yaml").write_text("use_time_stop: maybe\n")
    proposals_dir = tmp_path / "orb-proposals"

    results = [
        ExperimentRecord("configs/variants/orb_spy_only.yaml", 1.0, "keep", "", 1, {}),
        ExperimentRecord("configs/variants/orb_trailing_stop.yaml", 1.2, "keep", "", 2, {}),
    ]
    out = generate_combination_candidates(tmp_path, orb_family, proposals_dir, results)

    assert out == []
    assert not (tmp_path / "configs" / "variants" / "orb_spy_only_x_trailing_stop.yaml").exists()


def test_generate_combination_candidates_leaves_no_tmp_or_partial_yaml_on_failed_publish(
    tmp_path: Path, orb_family, monkeypatch: pytest.MonkeyPatch
) -> None:
    variants = tmp_path / "configs" / "variants"
    variants.mkdir(parents=True)
    (variants / "orb_spy_only.json").write_text('[{"type":"universe_symbols","symbols":["SPY"]}]\n')
    (variants / "orb_trend_filter.json").write_text(
        '[{"type":"regime_filter","require_regimes":["trend_day"]}]\n'
    )
    proposals_dir = tmp_path / "orb-proposals"

    (proposals_dir / "spy_only.json").parent.mkdir(parents=True, exist_ok=True)
    (proposals_dir / "spy_only.json").write_text(
        json.dumps({"thesis_id": "spy_only", "family": "universe"})
    )
    (proposals_dir / "trend_filter.json").write_text(
        json.dumps({"thesis_id": "trend_filter", "family": "regime"})
    )

    results = [
        ExperimentRecord("configs/variants/orb_spy_only.json", 1.0, "keep", "", 1, {}),
        ExperimentRecord("configs/variants/orb_trend_filter.json", 1.2, "keep", "", 2, {}),
    ]

    original_replace = os.replace

    def _crash_on_combo_publish(src, dst):
        if str(dst).endswith("contracts/spy_only_x_trend_filter.json"):
            raise RuntimeError("combo publish failed")
        return original_replace(src, dst)

    monkeypatch.setattr("autoresearch_planning.os.replace", _crash_on_combo_publish)

    with pytest.raises(RuntimeError, match="combo publish failed"):
        generate_combination_candidates(tmp_path, orb_family, proposals_dir, results)

    assert not (tmp_path / "contracts" / "spy_only_x_trend_filter.json").exists()
    assert not list(tmp_path.rglob("*.tmp"))


def test_build_research_failure_state_marks_interrupted_research_failure(tmp_path: Path) -> None:
    research_dir = tmp_path / "research"
    out = build_research_failure_state(tmp_path, research_dir, "round 5 failed")

    assert out["state"] == "interrupted"
    assert out["next_action"]["type"] == "terminated"
    assert any(b["kind"] == "research_failed" for b in out["blockers"])


# ── should_terminate ────────────────────────────────────────────


def test_should_terminate_false_when_pending_configs_exist(tmp_path: Path, ema_family) -> None:
    queue_dir = tmp_path / "queue"
    queue_dir.mkdir(parents=True)
    config = "configs/variants/ema_x.yaml"
    (tmp_path / config).parent.mkdir(parents=True, exist_ok=True)
    (tmp_path / config).write_text("k: v\n")
    (queue_dir / "ema_x.json").write_text(
        json.dumps({"config": config, "status": "pending", "thesis_id": "ema_x"})
    )
    research_dir = tmp_path / "research"
    assert should_terminate(tmp_path, ema_family, queue_dir, research_dir, []) is False


def test_should_terminate_false_when_no_research_artifacts(tmp_path: Path, ema_family) -> None:
    queue_dir = tmp_path / "queue"
    research_dir = tmp_path / "research"
    assert should_terminate(tmp_path, ema_family, queue_dir, research_dir, []) is False


def test_should_terminate_false_when_research_status_not_completed(
    tmp_path: Path, ema_family
) -> None:
    research_dir = tmp_path / "research"
    research_dir.mkdir()
    (research_dir / "r.json").write_text(json.dumps({"status": "pending"}))
    assert should_terminate(tmp_path, ema_family, tmp_path / "queue", research_dir, []) is False


def test_should_terminate_true_only_with_completed_research_and_findings(
    tmp_path: Path, ema_family
) -> None:
    research_dir = tmp_path / "research"
    research_dir.mkdir()
    (research_dir / "r.json").write_text(
        json.dumps({"status": "completed", "findings": ["no further structural ideas"]})
    )
    assert should_terminate(tmp_path, ema_family, tmp_path / "queue", research_dir, []) is True


def test_should_terminate_false_when_research_completed_but_suggested_theses_present(
    tmp_path: Path, ema_family
) -> None:
    research_dir = tmp_path / "research"
    research_dir.mkdir()
    (research_dir / "r.json").write_text(
        json.dumps(
            {
                "status": "completed",
                "findings": ["x"],
                "suggested_theses": [{"thesis_id": "more_to_try"}],
            }
        )
    )
    assert should_terminate(tmp_path, ema_family, tmp_path / "queue", research_dir, []) is False


# ── check_baseline_rerun ────────────────────────────────────────


class _FakeCheckpoint:
    def __init__(self, code_commit: str, timestamp: int) -> None:
        self.code_commit = code_commit
        self.timestamp = timestamp


class _FakeBaselineTracker:
    def __init__(self, latest):
        self._latest = latest

    def latest(self):
        return self._latest


def test_check_baseline_rerun_returns_none_when_no_checkpoint(tmp_path, ema_family) -> None:
    tracker = _FakeBaselineTracker(latest=None)
    assert check_baseline_rerun(tmp_path, ema_family, tracker, "abc123", []) is None


def test_check_baseline_rerun_fires_on_code_commit_change(tmp_path, ema_family) -> None:
    tracker = _FakeBaselineTracker(latest=_FakeCheckpoint(code_commit="old", timestamp=100))
    out = check_baseline_rerun(tmp_path, ema_family, tracker, "new", [])
    assert out is not None
    assert out["source"] == "baseline"
    assert out["baseline_rerun_for_commit"] == "new"
    assert "code changed old -> new" in out["rerun_reason"]


def test_check_baseline_rerun_does_not_fire_on_periodic_interval(tmp_path, ema_family) -> None:
    tracker = _FakeBaselineTracker(latest=_FakeCheckpoint(code_commit="same", timestamp=100))
    # Same commit must not trigger baseline rerun, regardless of experiment count.
    results = [
        ExperimentRecord("c1", 1.0, "keep", "", 200, {}),
        ExperimentRecord("c2", 1.0, "keep", "", 300, {}),
        ExperimentRecord("c3", 1.0, "keep", "", 400, {}),
        ExperimentRecord("c4", 1.0, "keep", "", 500, {}),
        ExperimentRecord("c5", 1.0, "keep", "", 600, {}),
    ]
    assert check_baseline_rerun(tmp_path, ema_family, tracker, "same", results) is None


def test_check_baseline_rerun_skips_when_already_reran_for_commit(tmp_path, ema_family) -> None:
    tracker = _FakeBaselineTracker(latest=_FakeCheckpoint(code_commit="old", timestamp=100))
    results = [
        ExperimentRecord(
            "configs/ema_base.yaml",
            1.0,
            "keep",
            "",
            150,
            {"baseline_rerun_for_commit": "new"},
        ),
    ]
    assert check_baseline_rerun(tmp_path, ema_family, tracker, "new", results) is None
