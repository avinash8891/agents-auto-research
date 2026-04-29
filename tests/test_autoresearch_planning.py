"""Unit tests for autoresearch_planning.

Project rule G: real production names — real thesis families ("universe",
"entry", "exit", "regime"), real config-path conventions, real status
strings ("keep", "discard", "pending", "completed").
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from autoresearch_planning import (
    COMBINATION_RULES,
    THESIS_FAMILY,
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


def test_list_known_variant_configs_returns_empty_when_no_files(tmp_path: Path) -> None:
    assert list_known_variant_configs(tmp_path) == []


def test_list_known_variant_configs_picks_up_yaml_files_in_variants_dir(tmp_path: Path) -> None:
    variants = tmp_path / "configs" / "variants"
    variants.mkdir(parents=True)
    (variants / "ema_aggressive.yaml").write_text("ema_length: 3\n")
    (variants / "ema_conservative.yaml").write_text("ema_length: 7\n")
    out = list_known_variant_configs(tmp_path)
    assert "configs/variants/ema_aggressive.yaml" in out
    assert "configs/variants/ema_conservative.yaml" in out


def test_list_known_variant_configs_skips_readme_keep(tmp_path: Path) -> None:
    variants = tmp_path / "configs" / "variants"
    variants.mkdir(parents=True)
    (variants / "real.yaml").write_text("k: v\n")
    (variants / "README.keep").write_text("keepalive\n")
    out = list_known_variant_configs(tmp_path)
    assert out == ["configs/variants/real.yaml"]


def test_pending_configs_excludes_already_attempted(tmp_path: Path) -> None:
    variants = tmp_path / "configs" / "variants"
    variants.mkdir(parents=True)
    (variants / "ema_a.yaml").write_text("k: v\n")
    (variants / "ema_b.yaml").write_text("k: v\n")
    results = [
        ExperimentRecord("configs/variants/ema_a.yaml", 1.0, "keep", "", 1, {}),
    ]
    assert pending_configs(tmp_path, results) == ["configs/variants/ema_b.yaml"]


# ── thesis_statuses overlay precedence ──────────────────────────


def test_thesis_statuses_run_queue_overlays_pending_default(tmp_path: Path) -> None:
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

    statuses = thesis_statuses(tmp_path, queue_dir, [])
    assert statuses["configs/variants/ema_a.yaml"]["source"] == "run_queue"
    assert statuses["configs/variants/ema_a.yaml"]["thesis_id"] == "ema_a"


def test_thesis_statuses_result_overlays_run_queue(tmp_path: Path) -> None:
    queue_dir = tmp_path / "queue"
    queue_dir.mkdir()
    (queue_dir / "x.json").write_text(
        json.dumps({"config": "configs/variants/ema_x.yaml", "status": "pending", "thesis_id": "x"})
    )
    results = [
        ExperimentRecord("configs/variants/ema_x.yaml", 1.42, "keep", "kept x", 100, {}),
    ]
    statuses = thesis_statuses(tmp_path, queue_dir, results)
    s = statuses["configs/variants/ema_x.yaml"]
    assert s["status"] == "keep"
    assert s["last_metric"] == 1.42
    assert s["last_timestamp"] == 100


# ── parse_ideas_backlog ─────────────────────────────────────────


def test_parse_ideas_backlog_returns_empty_when_file_missing(tmp_path: Path) -> None:
    assert parse_ideas_backlog(tmp_path / "missing.md") == []


def test_parse_ideas_backlog_extracts_real_thesis_slugs(real_ideas_backlog_path: Path) -> None:
    candidates = parse_ideas_backlog(real_ideas_backlog_path)
    slugs = [c["slug"] for c in candidates]
    assert "spy_only" in slugs
    assert "trailing_stop" in slugs
    assert "skip_chop" in slugs
    # Family inference from THESIS_FAMILY or section header should match.
    by_slug = {c["slug"]: c for c in candidates}
    assert by_slug["spy_only"]["family"] == "universe"
    assert by_slug["trailing_stop"]["family"] == "exit"
    assert by_slug["skip_chop"]["family"] == "regime"


def test_parse_ideas_backlog_emits_orb_prefixed_config_path(real_ideas_backlog_path: Path) -> None:
    # NOTE: this is a known limitation captured in the audit — the
    # ideas-backlog parser hardcodes the orb_ prefix. PR 5 will fix it.
    # The test pins current behavior so the fix is visible.
    candidates = parse_ideas_backlog(real_ideas_backlog_path)
    by_slug = {c["slug"]: c for c in candidates}
    assert by_slug["spy_only"]["config"] == "configs/variants/orb_spy_only.yaml"


# ── thesis_family_for ───────────────────────────────────────────


def test_thesis_family_for_known_slug_uses_thesis_family_map(tmp_path: Path) -> None:
    assert (
        thesis_family_for("configs/variants/orb_spy_only.yaml", tmp_path / "p", tmp_path)
        == "universe"
    )
    assert (
        thesis_family_for("configs/variants/orb_trailing_stop.yaml", tmp_path / "p", tmp_path)
        == "exit"
    )
    assert (
        thesis_family_for("configs/variants/orb_skip_chop.yaml", tmp_path / "p", tmp_path)
        == "regime"
    )


def test_thesis_family_for_falls_back_to_proposal_artifact(tmp_path: Path) -> None:
    proposals = tmp_path / "ema-proposals"
    proposals.mkdir()
    (proposals / "novel_thesis.json").write_text(
        json.dumps({"thesis_id": "novel_thesis", "family": "entry"})
    )
    assert (
        thesis_family_for("configs/variants/orb_novel_thesis.yaml", proposals, tmp_path) == "entry"
    )


def test_thesis_family_for_returns_unknown_when_neither_match(tmp_path: Path) -> None:
    assert (
        thesis_family_for("configs/variants/orb_mystery.yaml", tmp_path / "p", tmp_path)
        == "unknown"
    )


# ── COMBINATION_RULES truth table ───────────────────────────────


def test_combination_rules_universe_plus_exit_allowed() -> None:
    assert COMBINATION_RULES[("universe", "exit")] == "allowed"


def test_combination_rules_disallows_two_universes() -> None:
    assert COMBINATION_RULES[("universe", "universe")] == "disallowed"


def test_combination_rules_review_required_for_two_exits() -> None:
    assert COMBINATION_RULES[("exit", "exit")] == "review_required"


def test_thesis_family_map_covers_known_slugs() -> None:
    # Any future thesis slug added to the loop must be classified.
    assert THESIS_FAMILY["spy_only"] == "universe"
    assert THESIS_FAMILY["trailing_stop"] == "exit"
    assert THESIS_FAMILY["trend_filter"] == "regime"
    assert THESIS_FAMILY["follow_through"] == "entry"


# ── generate_combination_candidates ─────────────────────────────


def test_generate_combination_candidates_requires_at_least_two_keeps(tmp_path: Path) -> None:
    proposals_dir = tmp_path / "p"
    one_keep = [ExperimentRecord("configs/variants/orb_spy_only.yaml", 1.0, "keep", "", 1, {})]
    assert generate_combination_candidates(tmp_path, proposals_dir, one_keep) == []


def test_generate_combination_candidates_skips_disallowed_pairs(tmp_path: Path) -> None:
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
    out = generate_combination_candidates(tmp_path, proposals_dir, results)
    assert out == []


def test_generate_combination_candidates_creates_yaml_for_allowed_pair(tmp_path: Path) -> None:
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
    out = generate_combination_candidates(tmp_path, proposals_dir, results)
    assert out == ["configs/variants/orb_spy_only_x_trailing_stop.yaml"]
    combo_path = tmp_path / out[0]
    assert combo_path.exists()
    proposal_path = proposals_dir / "spy_only_x_trailing_stop.json"
    assert proposal_path.exists()
    proposal = json.loads(proposal_path.read_text())
    assert proposal["family"] == "combination"


# ── should_terminate ────────────────────────────────────────────


def test_should_terminate_false_when_pending_configs_exist(tmp_path: Path) -> None:
    variants = tmp_path / "configs" / "variants"
    variants.mkdir(parents=True)
    (variants / "ema_x.yaml").write_text("k: v\n")
    queue_dir = tmp_path / "queue"
    research_dir = tmp_path / "research"
    assert should_terminate(tmp_path, queue_dir, research_dir, []) is False


def test_should_terminate_false_when_no_research_artifacts(tmp_path: Path) -> None:
    queue_dir = tmp_path / "queue"
    research_dir = tmp_path / "research"
    assert should_terminate(tmp_path, queue_dir, research_dir, []) is False


def test_should_terminate_false_when_research_status_not_completed(tmp_path: Path) -> None:
    research_dir = tmp_path / "research"
    research_dir.mkdir()
    (research_dir / "r.json").write_text(json.dumps({"status": "pending"}))
    assert should_terminate(tmp_path, tmp_path / "queue", research_dir, []) is False


def test_should_terminate_true_only_with_completed_research_and_findings(tmp_path: Path) -> None:
    research_dir = tmp_path / "research"
    research_dir.mkdir()
    (research_dir / "r.json").write_text(
        json.dumps({"status": "completed", "findings": ["no further structural ideas"]})
    )
    assert should_terminate(tmp_path, tmp_path / "queue", research_dir, []) is True


def test_should_terminate_false_when_research_completed_but_suggested_theses_present(
    tmp_path: Path,
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
    assert should_terminate(tmp_path, tmp_path / "queue", research_dir, []) is False


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


def test_check_baseline_rerun_fires_on_periodic_interval(tmp_path, ema_family) -> None:
    tracker = _FakeBaselineTracker(latest=_FakeCheckpoint(code_commit="same", timestamp=100))
    # 5 experiments since checkpoint timestamp (BASELINE_RERUN_INTERVAL = 5).
    results = [
        ExperimentRecord("c1", 1.0, "keep", "", 200, {}),
        ExperimentRecord("c2", 1.0, "keep", "", 300, {}),
        ExperimentRecord("c3", 1.0, "keep", "", 400, {}),
        ExperimentRecord("c4", 1.0, "keep", "", 500, {}),
        ExperimentRecord("c5", 1.0, "keep", "", 600, {}),
    ]
    out = check_baseline_rerun(tmp_path, ema_family, tracker, "same", results)
    assert out is not None
    assert "periodic rerun" in out["rerun_reason"]


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
