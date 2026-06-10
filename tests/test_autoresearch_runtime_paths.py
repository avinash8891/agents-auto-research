from __future__ import annotations

from pathlib import Path

import pytest

from autoresearch_runtime_paths import (
    AutoresearchRuntimeContext,
    iter_family_backtest_db_paths,
    research_round_id,
    research_round_id_or_empty,
)


def test_runtime_context_separates_code_root_runtime_root_and_family_db(
    tmp_path: Path,
) -> None:
    code_root = tmp_path / "code"
    runtime_root = tmp_path / "runtime"
    code_root.mkdir()
    runtime_root.mkdir()

    ctx = AutoresearchRuntimeContext.for_family(
        code_root=code_root,
        runtime_root=runtime_root,
        family_name="ema",
    )

    assert ctx.code_root == code_root.resolve()
    assert ctx.runtime_root == runtime_root.resolve()
    assert ctx.family_name == "ema"
    assert ctx.state_path == runtime_root.resolve() / "ema_autoresearch.next.json"
    assert ctx.current_md_path == runtime_root.resolve() / "ema_autoresearch.current.md"
    assert ctx.jobs_root == runtime_root.resolve() / "runtime" / "jobs"
    assert ctx.backtest_db_path == runtime_root.resolve() / "ema_backtest_runs.db"
    assert ctx.baseline_checkpoints_path == runtime_root.resolve() / "ema_baseline_checkpoints.json"


def test_runtime_context_preserves_legacy_orb_unprefixed_state_files(
    tmp_path: Path,
) -> None:
    ctx = AutoresearchRuntimeContext.for_family(
        code_root=tmp_path,
        runtime_root=tmp_path,
        family_name="orb",
    )

    assert ctx.state_path == tmp_path.resolve() / "autoresearch.next.json"
    assert ctx.current_md_path == tmp_path.resolve() / "autoresearch.current.md"


def test_runtime_context_resolves_job_scoped_paths(tmp_path: Path) -> None:
    ctx = AutoresearchRuntimeContext.for_family(
        code_root=tmp_path / "code",
        runtime_root=tmp_path / "runtime",
        family_name="ema",
    )

    assert ctx.job_runtime_root(7) == ctx.jobs_root / "job-7"
    assert ctx.research_dir(7) == ctx.jobs_root / "job-7" / "research"
    assert ctx.builder_requests_dir(7) == ctx.jobs_root / "job-7" / "builder-requests"
    assert (
        ctx.research_round_root(7, 0) == ctx.jobs_root / "job-7" / "research" / "round-0-baseline"
    )
    assert ctx.research_round_root(7, 3) == ctx.jobs_root / "job-7" / "research" / "round-3"


def test_iter_family_backtest_db_paths_prefers_runtime_root_and_filters_family(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    code_root = tmp_path / "code"
    runtime_root = tmp_path / "runtime"
    code_root.mkdir()
    runtime_root.mkdir()
    monkeypatch.setenv("AUTORESEARCH_RUNTIME_ROOT", str(runtime_root))
    (code_root / "ema_backtest_runs.db").write_text("")
    (runtime_root / "ema_backtest_runs.db").write_text("")
    (runtime_root / "orb_backtest_runs.db").write_text("")

    paths = iter_family_backtest_db_paths(code_root, family="ema")

    assert paths == [runtime_root / "ema_backtest_runs.db"]


def test_iter_family_backtest_db_paths_keeps_code_root_local_backcompat(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("AUTORESEARCH_RUNTIME_ROOT", raising=False)
    (tmp_path / "ema_backtest_runs.db").write_text("")

    paths = iter_family_backtest_db_paths(tmp_path, family="ema")

    assert paths == [tmp_path / "ema_backtest_runs.db"]


class TestResearchRoundId:
    def test_canonical_format(self) -> None:
        assert research_round_id(12, 5) == "job-12-round-5"

    def test_baseline_round_zero_allowed(self) -> None:
        assert research_round_id(1, 0) == "job-1-round-0"

    def test_zero_job_rejected(self) -> None:
        with pytest.raises(ValueError, match="job id must be >= 1"):
            research_round_id(0, 1)

    def test_negative_round_rejected(self) -> None:
        with pytest.raises(ValueError, match="round number must be >= 0"):
            research_round_id(1, -1)


class TestResearchRoundIdOrEmpty:
    def test_valid_inputs_return_helper_result(self) -> None:
        assert research_round_id_or_empty(7, 3) == "job-7-round-3"

    def test_job_zero_returns_empty(self) -> None:
        assert research_round_id_or_empty(0, 1) == ""

    def test_negative_round_returns_empty(self) -> None:
        assert research_round_id_or_empty(1, -1) == ""

    def test_non_int_job_returns_empty(self) -> None:
        assert research_round_id_or_empty("not-a-number", 1) == ""

    def test_none_job_returns_empty(self) -> None:
        assert research_round_id_or_empty(None, 1) == ""

    def test_none_round_returns_empty(self) -> None:
        assert research_round_id_or_empty(1, None) == ""

    def test_non_int_round_returns_empty(self) -> None:
        assert research_round_id_or_empty(1, "abc") == ""

    def test_stringified_int_round_accepted(self) -> None:
        assert research_round_id_or_empty(1, "5") == "job-1-round-5"
