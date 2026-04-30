"""Unit tests for autoresearch_state.

Project rule G: behavioral assertions with quantitative expectations, real
production names (status="keep"/"discard", real config-path strings).
"""

from __future__ import annotations

import os
from pathlib import Path

from autoresearch_state import (
    ExperimentRecord,
    RunContext,
    best_result,
    coerce_timestamp_to_epoch_ms,
    coerce_timestamp_to_iso8601_utc,
    deduplicate_entries,
    direction,
    is_better,
    iso8601_utc_now,
    latest_result,
    promote_missing_known_results,
    read_results,
    read_state,
    render_current_md,
    write_current_md,
    write_state,
)

# ── State JSON round-trip ────────────────────────────────────────


def test_read_state_returns_running_default_when_file_missing(tmp_path: Path) -> None:
    state = read_state(tmp_path / "missing.json")
    assert state == {"state": "running"}


def test_state_round_trip_preserves_payload_atomically(tmp_path: Path) -> None:
    state_path = tmp_path / "state.json"
    payload = {
        "state": "running",
        "job": 7,
        "research_round": 3,
        "next_action": {"type": "run_experiment", "config": "configs/ema_base.yaml"},
    }
    write_state(state_path, payload)
    assert state_path.exists()
    assert read_state(state_path) == payload
    # tmp file used for atomic replace must be cleaned up.
    assert not state_path.with_name(state_path.name + ".tmp").exists()


def test_write_state_fsyncs_before_replace(tmp_path: Path, monkeypatch) -> None:
    state_path = tmp_path / "state.json"
    calls: list[int] = []
    original_fsync = os.fsync

    def _record_fsync(fd: int) -> None:
        calls.append(fd)
        return original_fsync(fd)

    monkeypatch.setattr(os, "fsync", _record_fsync)

    write_state(state_path, {"state": "running"})

    assert calls


# ── direction / is_better / best_result / latest_result ─────────


def test_direction_defaults_to_higher_when_no_config_header() -> None:
    assert direction([]) == "higher"
    assert direction([{"run": 1, "metric": 1.0}]) == "higher"


def test_direction_reads_best_direction_from_config_header() -> None:
    entries = [{"type": "config", "bestDirection": "lower"}, {"run": 1, "metric": 1.0}]
    assert direction(entries) == "lower"


def test_is_better_higher_direction() -> None:
    assert is_better("higher", 1.5, None) is True
    assert is_better("higher", 1.5, 1.0) is True
    assert is_better("higher", 1.0, 1.5) is False


def test_is_better_lower_direction() -> None:
    assert is_better("lower", 0.10, None) is True
    assert is_better("lower", 0.10, 0.20) is True
    assert is_better("lower", 0.30, 0.20) is False


def test_best_result_picks_highest_keep_metric() -> None:
    results = [
        ExperimentRecord("configs/ema_base.yaml", 1.0, "keep", "", 1, {}),
        ExperimentRecord("configs/variants/ema_a.yaml", 1.5, "keep", "", 2, {}),
        ExperimentRecord("configs/variants/ema_b.yaml", 1.7, "discard", "", 3, {}),
        ExperimentRecord("configs/variants/ema_c.yaml", 1.3, "keep", "", 4, {}),
    ]
    assert best_result(results, "higher") == {
        "config": "configs/variants/ema_a.yaml",
        "metric": 1.5,
    }


def test_best_result_excludes_discard_status() -> None:
    results = [
        ExperimentRecord("configs/ema_base.yaml", 0.9, "keep", "", 1, {}),
        ExperimentRecord("configs/variants/ema_x.yaml", 99.0, "discard", "", 2, {}),
    ]
    assert best_result(results, "higher")["metric"] == 0.9


def test_best_result_returns_empty_when_no_keeps() -> None:
    results = [ExperimentRecord("configs/ema_base.yaml", 1.5, "discard", "", 1, {})]
    assert best_result(results, "higher") == {}


def test_latest_result_picks_largest_timestamp() -> None:
    a = ExperimentRecord("configs/a.yaml", 1.0, "keep", "", 1700000000, {})
    b = ExperimentRecord("configs/b.yaml", 2.0, "keep", "", 1700000005, {})
    c = ExperimentRecord("configs/c.yaml", 3.0, "discard", "", 1700000003, {})
    assert latest_result([a, b, c]).config == "configs/b.yaml"


def test_latest_result_returns_none_for_empty_list() -> None:
    assert latest_result([]) is None


# ── read_results filtering ───────────────────────────────────────


def test_read_results_skips_config_and_research_round_entries() -> None:
    entries = [
        {"type": "config", "metricName": "median_expectancy"},
        {"type": "research_round", "outcome": "compiled"},
        {
            "run": 1,
            "metric": 1.2,
            "status": "keep",
            "asi": {"config": "configs/ema_base.yaml"},
        },
    ]
    results = read_results(entries)
    assert len(results) == 1
    assert results[0].config == "configs/ema_base.yaml"
    assert results[0].metric == 1.2


def test_read_results_uses_empty_string_for_missing_asi_config() -> None:
    entries = [{"run": 1, "metric": 1.0, "status": "keep"}]
    results = read_results(entries)
    assert len(results) == 1
    assert results[0].config == ""


# ── deduplicate_entries: richest entry per config wins ──────────


def test_deduplicate_entries_keeps_single_entry_unchanged() -> None:
    entries = [
        {"type": "config"},
        {"run": 1, "metric": 1.0, "status": "keep", "asi": {"config": "configs/ema_base.yaml"}},
    ]
    assert deduplicate_entries(entries) == entries


def test_deduplicate_entries_picks_richer_of_two_for_same_config() -> None:
    sparse = {
        "run": 1,
        "metric": 1.0,
        "status": "keep",
        "asi": {"config": "configs/ema_base.yaml"},
    }
    rich = {
        "run": 2,
        "metric": 1.0,
        "status": "keep",
        "asi": {
            "config": "configs/ema_base.yaml",
            "trade_analysis": {"trade_count": 287},
            "insights": ["metric=1.0", "decision=keep"],
        },
    }
    out = deduplicate_entries([sparse, rich])
    assert len(out) == 1
    assert out[0]["asi"].get("trade_analysis") == {"trade_count": 287}


def test_deduplicate_entries_preserves_non_experiment_rows() -> None:
    config_header = {"type": "config", "metricName": "median_expectancy"}
    no_config_entry = {"run": 9, "metric": 0.0, "status": "keep", "asi": {}}
    out = deduplicate_entries([config_header, no_config_entry])
    assert config_header in out
    assert no_config_entry in out


def test_promote_missing_known_results_is_a_noop_passthrough() -> None:
    entries = [{"a": 1}, {"b": 2}]
    assert promote_missing_known_results(entries) == entries


# ── render_current_md / write_current_md ────────────────────────


def test_render_current_md_includes_best_and_latest_when_present() -> None:
    state = {
        "state": "running",
        "current_best": {"config": "configs/variants/ema_a.yaml", "metric": 1.5},
        "next_action": {"type": "run_experiment", "config": "configs/variants/ema_b.yaml"},
        "pending_configs": [],
        "thesis_statuses": {},
        "blockers": [],
    }
    results = [
        ExperimentRecord("configs/variants/ema_a.yaml", 1.5, "keep", "", 100, {}),
    ]
    md = render_current_md(state, results, family_name="ema")
    assert "# EMA Autoresearch Current State" in md
    assert "# ORB Autoresearch Current State" not in md
    assert "configs/variants/ema_a.yaml" in md
    assert "1.5" in md
    assert "configs/variants/ema_b.yaml" in md
    assert "## Current Best" in md
    assert "## Latest Insights" in md


def test_render_current_md_handles_no_results_and_no_action() -> None:
    md = render_current_md(
        {"state": "blocked", "blockers": []},
        [],
    )
    assert "No experiments logged yet." in md
    assert "## Blockers" in md


def test_render_current_md_renders_blockers() -> None:
    state = {
        "state": "blocked",
        "blockers": [{"kind": "research_required", "detail": "round 5 failed"}],
    }
    md = render_current_md(state, [])
    assert "research_required: round 5 failed" in md


def test_write_current_md_writes_file_to_disk(tmp_path: Path) -> None:
    out = tmp_path / "current.md"
    write_current_md(out, {"state": "running"}, [], family_name="ema")
    assert out.exists()
    body = out.read_text()
    assert "# EMA Autoresearch Current State" in body


# ── RunContext defaults ──────────────────────────────────────────


def test_iso8601_utc_now_includes_timezone_marker() -> None:
    """Rule J: stored timestamps must be UTC with timezone."""
    now = iso8601_utc_now()
    # Python's datetime.isoformat() emits +00:00 for UTC offset.
    assert now.endswith("+00:00")
    # Parseable back to datetime.
    from datetime import datetime

    parsed = datetime.fromisoformat(now)
    assert parsed.tzinfo is not None


def test_coerce_timestamp_to_iso8601_passes_string_through() -> None:
    iso = "2026-04-29T12:00:00+00:00"
    assert coerce_timestamp_to_iso8601_utc(iso) == iso


def test_coerce_timestamp_to_iso8601_rejects_naive_string() -> None:
    assert coerce_timestamp_to_iso8601_utc("2026-04-29T12:00:00") is None


def test_coerce_timestamp_to_iso8601_rejects_non_timestamp_string() -> None:
    assert coerce_timestamp_to_iso8601_utc("unknown") is None


def test_coerce_timestamp_to_iso8601_converts_epoch_ms() -> None:
    # 2024-01-01 00:00:00 UTC == 1704067200000 ms.
    iso = coerce_timestamp_to_iso8601_utc(1704067200000)
    assert iso == "2024-01-01T00:00:00+00:00"


def test_coerce_timestamp_to_iso8601_returns_none_for_unknown_type() -> None:
    assert coerce_timestamp_to_iso8601_utc(None) is None
    assert coerce_timestamp_to_iso8601_utc({"not": "a timestamp"}) is None


def test_coerce_timestamp_to_epoch_ms_passes_int_through() -> None:
    assert coerce_timestamp_to_epoch_ms(1700000000000) == 1700000000000


def test_coerce_timestamp_to_epoch_ms_parses_iso_string() -> None:
    assert coerce_timestamp_to_epoch_ms("2024-01-01T00:00:00+00:00") == 1704067200000


def test_coerce_timestamp_to_epoch_ms_returns_zero_for_unparseable() -> None:
    assert coerce_timestamp_to_epoch_ms("not an iso string") == 0
    assert coerce_timestamp_to_epoch_ms(None) == 0


def test_read_results_handles_both_legacy_int_and_iso_timestamps() -> None:
    """Back-compat: pre-rule-J files have int epoch ms; new files have ISO
    strings. read_results must normalise both to ISO-8601 UTC strings and
    order them correctly via coerce_timestamp_to_epoch_ms in latest_result."""
    legacy_entry = {
        "run": 1,
        "metric": 1.0,
        "status": "keep",
        "timestamp": 1700000000000,  # Nov 14 2023 → 2023-11-14T22:13:20+00:00
        "asi": {"config": "configs/legacy.yaml"},
    }
    new_entry = {
        "run": 2,
        "metric": 2.0,
        "status": "keep",
        "timestamp": "2024-01-01T00:00:00+00:00",
        "asi": {"config": "configs/new.yaml"},
    }
    results = read_results([legacy_entry, new_entry])
    assert len(results) == 2
    # Both timestamps are now ISO-8601 UTC strings (rule J).
    assert results[0].timestamp == "2023-11-14T22:13:20+00:00"
    assert results[1].timestamp == "2024-01-01T00:00:00+00:00"
    # Ordering by timestamp must work across the two formats.
    assert latest_result(results).config == "configs/new.yaml"


def test_run_context_defaults_are_empty_and_isolate_per_instance() -> None:
    ctx_a = RunContext()
    ctx_b = RunContext()
    assert ctx_a.current_contract is None
    assert ctx_a.parent_experiment_id == ""
    assert ctx_a.current_artifact_dir is None
    assert ctx_a.latest_trades_file == ""
    assert ctx_a.latest_strategy_events_file == ""
    assert ctx_a.latest_diagnostics_file == ""
    assert ctx_a.latest_config_contents == {}

    # Mutate one — the default_factory should not bleed into the other.
    ctx_a.latest_config_contents["ema_length"] = 5
    assert ctx_b.latest_config_contents == {}
