from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from agents.tool_context import ToolContext

import research_conductor as conductor
import research_subagents as subagents
from backtest_run_db import BacktestRunDB, BacktestRunRecord
from rejection_artifact import write_rejection
from research_types import MechanismProposal, StructuredRejection


def _record(
    *, job: int, round_number: int, baseline: bool = False, root: Path
) -> BacktestRunRecord:
    backtest_dir = (
        root / "runtime" / "jobs" / f"job-{job}" / "research" / "round-0-baseline" / "backtest"
        if baseline
        else root
        / "runtime"
        / "jobs"
        / f"job-{job}"
        / "research"
        / f"round-{round_number}"
        / "backtest"
    )
    backtest_dir.mkdir(parents=True, exist_ok=True)
    (backtest_dir / "trades.csv").write_text("entry_date,exit_date\n")
    (backtest_dir / "metrics.json").write_text("{}\n")
    (backtest_dir / "result.json").write_text("{}\n")
    (backtest_dir / "diagnostics.json").write_text("{}\n")
    (backtest_dir / "config.json").write_text('{"strategy_family":"ema","data_universe":"u1"}\n')
    return BacktestRunRecord(
        run_id=f"exp-{job}-{round_number}",
        thesis_id="baseline" if baseline else f"thesis-{round_number}",
        config_path=(
            "configs/ema_base.yaml"
            if baseline
            else f"runtime/jobs/job-{job}/research/round-{round_number}/selected_config.json"
        ),
        runtime_config={},
        code_commit="abc1234",
        data_hash="data",
        train_metrics={},
        validation_metrics={"profit_factor": 1.2 + round_number},
        trade_count=10,
        trades_file=str((backtest_dir / "trades.csv").resolve()),
        strategy_events_file=str((backtest_dir / "strategy_events.parquet").resolve()),
        diagnostics_file=str((backtest_dir / "diagnostics.json").resolve()),
        strategy_diagnostics={},
        accepted=True,
        rejection_reason="",
        verdict_status="accepted",
        verdict_summary="good",
        parent_backtest_run_id="",
        timestamp=f"2026-05-09T00:00:0{round_number}+00:00",
        family="ema",
        hypothesis="h",
        mechanism="m",
        job=job,
        backtest_run_id=f"job-{job}-round-{round_number}-backtest",
        research_round_id=f"job-{job}-round-{round_number}",
        research_round_number=round_number,
        is_baseline=baseline,
    )


class _StreamedResult:
    def __init__(
        self,
        text: str,
        agent: object | None = None,
        tool_calls: list[tuple[str, dict]] | None = None,
        tool_outputs: list[tuple[str, str]] | None = None,
        *,
        final_output: object | None = None,
        final_output_as_error: bool = False,
        stream_event_count: int = 0,
    ) -> None:
        self.final_output = text
        self.agent = agent
        self.tool_calls = tool_calls or []
        self.tool_outputs = tool_outputs if tool_outputs is not None else []
        if final_output is not None:
            self.final_output = final_output
        self.final_output_as_error = final_output_as_error
        self.stream_event_count = stream_event_count

    async def stream_events(self):
        tools = {tool.name: tool for tool in getattr(self.agent, "tools", [])}
        for name, payload in self.tool_calls:
            raw = json.dumps(payload)
            ctx = ToolContext(None, tool_name=name, tool_call_id=f"call-{name}", tool_arguments=raw)
            output = await tools[name].on_invoke_tool(ctx, raw)
            self.tool_outputs.append((name, output))
        for _ in range(self.stream_event_count):
            yield None

    def final_output_as(self, output_type):
        if self.final_output_as_error:
            raise RuntimeError("final output coercion failed")
        return self.final_output


def _patch_conductor_runner(
    monkeypatch: pytest.MonkeyPatch,
    output: dict | str,
    *,
    tool_calls: list[tuple[str, dict]] | None = None,
    tool_outputs: list[tuple[str, str]] | None = None,
    final_output: object | None = None,
    final_output_as_error: bool = False,
    stream_event_count: int = 0,
) -> list[str]:
    captured_prompts: list[str] = []
    text = output if isinstance(output, str) else json.dumps(output)
    monkeypatch.setattr(conductor, "_ensure_oauth_proxy", lambda: None)
    monkeypatch.setattr(conductor, "_get_openai_client", lambda url: object())

    def _run_streamed(agent, user_prompt, max_turns, run_config):
        captured_prompts.append(user_prompt)
        assert agent.name == "research-conductor"
        assert {tool.name for tool in agent.tools} >= {
            "analyze_trades",
            "web_search",
            "list_round_results",
        }
        assert run_config.tracing_disabled is True
        assert run_config.model_settings.store is False
        return _StreamedResult(
            text,
            agent,
            tool_calls,
            tool_outputs,
            final_output=final_output,
            final_output_as_error=final_output_as_error,
            stream_event_count=stream_event_count,
        )

    monkeypatch.setattr(conductor.OAIRunner, "run_streamed", _run_streamed)
    return captured_prompts


def test_analysis_manifest_defaults_to_baseline_when_round_zero_exists(
    tmp_path: Path, monkeypatch
) -> None:
    db = BacktestRunDB(tmp_path / "ema_backtest_runs.db")
    db.init_session(name="ema", metric_name="profit_factor", direction="higher")
    baseline = _record(job=7, round_number=0, baseline=True, root=tmp_path)
    latest = _record(job=7, round_number=1, root=tmp_path)
    db.add(baseline)
    db.add(latest)
    monkeypatch.setattr(
        subagents, "_resolve_backtest_db_path", lambda trades_file, family_name: db.path
    )

    manifest = subagents._analysis_manifest(
        trades_file=baseline.trades_file,
        family_name="ema",
    )

    assert manifest["default_scope"] == "baseline"
    assert manifest["default_round_ref"] == "baseline"
    assert manifest["artifacts"]["trades_csv"] == baseline.trades_file


def test_conductor_prompt_includes_trade_artifacts_and_returns_stop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    trades_file = tmp_path / "trades.csv"
    events_file = tmp_path / "events.parquet"
    diagnostics_file = tmp_path / "diagnostics.json"
    trades_file.write_text("entry_date,pnl_pct\n2026-01-01,1.2\n")
    events_file.write_text("")
    diagnostics_file.write_text("{}\n")
    prompts = _patch_conductor_runner(
        monkeypatch,
        {
            "reasoning": "no statistically defensible next mechanism remains",
            "suggested_theses": [],
            "should_stop": True,
        },
    )

    out = conductor.run_research_conductor_sync(
        str(trades_file),
        "baseline PF=1.2",
        {
            "profit_factor": 1.2,
            "resolution_context": {
                "resolution_config_keys": ["bar_minutes"],
                "resolved_minutes_by_key": {"bar_minutes": 5},
                "minimum_supported_time_bucket_minutes": 5,
            },
        },
        research_round=4,
        family_name="ema",
        strategy_events_file=str(events_file),
        diagnostics_file=str(diagnostics_file),
        rejection_feedback="previous thesis reused the same filter family",
        current_job=1,
    )

    assert out is not None
    assert out.should_stop is True
    assert f"Trades file for analysis: {trades_file}" in prompts[0]
    assert f"Strategy events file: {events_file}" in prompts[0]
    assert f"Diagnostics file: {diagnostics_file}" in prompts[0]
    assert "minimum_supported_time_bucket_minutes: 5" in prompts[0]
    assert "previous thesis reused the same filter family" in prompts[0]


def test_conductor_returns_timeout_error_when_runner_times_out(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(conductor, "_ensure_oauth_proxy", lambda: None)
    monkeypatch.setattr(conductor, "_get_openai_client", lambda url: object())

    def _raise_timeout(agent, user_prompt, max_turns, run_config):
        assert agent.name == "research-conductor"
        assert run_config.tracing_disabled is True
        raise asyncio.TimeoutError

    monkeypatch.setattr(conductor.OAIRunner, "run_streamed", _raise_timeout)

    out = conductor.run_research_conductor_sync("", "", {}, 1, "ema")

    assert out is not None
    assert out.status == "conductor_error"
    assert out.error == "timeout"


def test_conductor_applies_timeout_to_hung_stream(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _HungResult:
        final_output = ""

        async def stream_events(self):
            await asyncio.sleep(10)
            yield None

    monkeypatch.setenv("AUTORESEARCH_CONDUCTOR_TIMEOUT_SECONDS", "0.01")
    monkeypatch.setattr(conductor, "_ensure_oauth_proxy", lambda: None)
    monkeypatch.setattr(conductor, "_get_openai_client", lambda url: object())
    monkeypatch.setattr(conductor.OAIRunner, "run_streamed", lambda *args, **kwargs: _HungResult())

    out = conductor.run_research_conductor_sync("", "", {}, 1, "ema")

    assert out is not None
    assert out.status == "conductor_error"
    assert out.error == "timeout"


def test_live_conductor_tools_use_shared_schema_validation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tool_outputs: list[tuple[str, str]] = []
    _patch_conductor_runner(
        monkeypatch,
        {"reasoning": "stop", "suggested_theses": [], "should_stop": True},
        tool_calls=[("list_rejections", {"limit": 0})],
        tool_outputs=tool_outputs,
    )

    out = conductor.run_research_conductor_sync("", "", {}, 8, "ema", current_job=1)

    assert out is not None
    assert out.status == "should_stop"
    assert tool_outputs
    assert tool_outputs[0][0] == "list_rejections"
    assert tool_outputs[0][1].startswith("VALIDATION ERROR:")


def test_conductor_marks_oauth_proxy_failure_as_proxy_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        conductor,
        "_ensure_oauth_proxy",
        lambda: (_ for _ in ()).throw(RuntimeError("openai-oauth proxy unavailable")),
    )

    out = conductor.run_research_conductor_sync("", "", {}, 1, "ema")

    assert out is not None
    assert out.status == "conductor_error"
    assert out.error == "proxy_unavailable"


def test_conductor_prompt_without_trades_uses_latest_outcome_without_analyst(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prompts = _patch_conductor_runner(
        monkeypatch,
        {"reasoning": "stop after failed latest run", "suggested_theses": [], "should_stop": True},
    )

    out = conductor.run_research_conductor_sync(
        "",
        "latest run rejected",
        {"status": "rejected", "profit_factor": 0.7},
        research_round=5,
        family_name="unknown-family",
    )

    assert out is not None
    assert out.should_stop is True
    assert "No trades file is available for the latest/current round" in prompts[0]
    assert "Strategy family: unknown-family" not in prompts[0]


# REMOVED: test_conductor_rejects_thesis_when_experiment_results_tool_was_not_called
# The L7 inline conductor gate that this test exercised has been moved to the
# validator as part of the process_validator refactor. Equivalent coverage now
# lives in tests/test_validator_process_gate.py
# (test_process_gate_rejects_when_experiment_results_not_called).


def test_conductor_stream_tools_apply_order_gates_and_return_memory_results(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _web(query: str, context: str = "", reflexion_feedback: str = "") -> str:
        return f"web evidence for {query}"

    async def _analyst(*args, **kwargs) -> str:
        return "analyst found baseline rejection concentration"

    monkeypatch.setattr(conductor, "_call_web_researcher", _web)
    monkeypatch.setattr(conductor, "_call_analyst", _analyst)
    monkeypatch.setattr(conductor, "save_research_finding", lambda **kwargs: "saved finding")
    monkeypatch.setattr(
        conductor,
        "search_research_findings",
        lambda **kwargs: [{"text": "opening gap failures cluster", "room": "ema", "distance": 0.1}],
    )
    monkeypatch.setattr(conductor, "_palace_status", lambda: {"rooms": 3, "healthy": True})
    monkeypatch.setattr(conductor, "list_past_theses_for_root", lambda *args, **kwargs: "past list")
    monkeypatch.setattr(
        conductor,
        "get_past_thesis_for_root",
        lambda *args, **kwargs: json.dumps({"status": "not_found"}),
    )
    monkeypatch.setattr(
        conductor,
        "list_round_results_for_root",
        lambda *args, **kwargs: "experiment list",
    )
    monkeypatch.setattr(
        conductor,
        "get_round_result_for_root",
        lambda *args, **kwargs: json.dumps({"status": "error", "error": "missing thesis"}),
    )
    tool_outputs: list[tuple[str, str]] = []
    _patch_conductor_runner(
        monkeypatch,
        {"reasoning": "tools consulted; stop", "suggested_theses": [], "should_stop": True},
        tool_outputs=tool_outputs,
        tool_calls=[
            ("analyze_trades", {"focus_question": "look before web"}),
            ("web_search", {"query": "EMA opening gap filters", "context": "SPY intraday"}),
            ("analyze_trades", {"focus_question": "gap rejection concentration"}),
            (
                "save_finding",
                {
                    "finding": "Opening gap failures cluster after 10:00.",
                    "finding_type": "observation",
                    "status": "unvalidated",
                    "evidence": "round_008 analyst",
                    "scope": "train_period_only",
                    "expires_if": "baseline changes",
                },
            ),
            ("search_findings", {"query": "opening gap", "finding_type": "observation"}),
            ("memory_status", {}),
            ("list_past_theses", {"offset": 0, "limit": 5}),
            ("get_past_thesis", {"thesis_id": "missing"}),
            ("list_round_results", {"order": "latest", "offset": 0, "limit": 5}),
            ("get_round_result", {"research_round_id": "job-12-round-8", "detail": True}),
            ("list_rejections", {"round_number": None, "rejection_code": None, "limit": 3}),
            ("get_rejection", {"round_number": 8, "thesis_id": "missing"}),
            ("rejection_pattern_summary", {"window_rounds": 4}),
        ],
    )

    out = conductor.run_research_conductor_sync(
        str(Path("tests/fixtures/AAA.csv").resolve()),
        "experiment history",
        {"status": "keep"},
        research_round=8,
        family_name="ema",
    )

    assert out is not None
    assert out.should_stop is True
    output_by_tool = dict(tool_outputs)
    assert output_by_tool["analyze_trades"] == "analyst found baseline rejection concentration"
    # NOTE: removed assertion `"call web_search at least once" in tool_outputs[0][1]`.
    # The L6 inline order gate on analyze_trades has been removed — analyze_trades
    # now dispatches unconditionally. Per-thesis web_search enforcement moved to
    # the validator (see _validate_process / tests/test_validator_process_gate.py).
    assert output_by_tool["web_search"] == "web evidence for EMA opening gap filters"
    assert output_by_tool["save_finding"] == "saved finding"
    assert "opening gap failures cluster" in output_by_tool["search_findings"]
    assert '"healthy": true' in output_by_tool["memory_status"]
    assert output_by_tool["list_round_results"] == "experiment list"
    assert "missing thesis" in output_by_tool["get_round_result"]
    assert '"error": "no current job"' in output_by_tool["list_rejections"]


def test_conductor_tools_read_current_job_rejections_and_error_statuses(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    write_rejection(
        tmp_path,
        job=12,
        rejection=StructuredRejection(
            rejected_at="2026-05-09T13:45:22Z",
            round=8,
            thesis_id="ema-gap-reject",
            stage="stage_1",
            rejection_code="thesis_quality_theme_cluster_fixation",
            rule_violated="4 of last 7 theses share keywords",
            evidence={"shared_keywords": ["gap", "open"]},
            remediation_hint="switch mechanism dimension",
            validator_version="2.3.0",
        ),
    )
    monkeypatch.setattr(conductor, "_ROOT", tmp_path)
    monkeypatch.setattr(conductor, "search_research_findings", lambda **kwargs: [])
    monkeypatch.setattr(
        conductor,
        "get_past_thesis_for_root",
        lambda *args, **kwargs: "not-json",
    )
    monkeypatch.setattr(
        conductor,
        "get_round_result_for_root",
        lambda *args, **kwargs: json.dumps({"status": "not_found"}),
    )

    tool_outputs: list[tuple[str, str]] = []
    _patch_conductor_runner(
        monkeypatch,
        {"reasoning": "tools consulted; stop", "suggested_theses": [], "should_stop": True},
        tool_outputs=tool_outputs,
        tool_calls=[
            ("search_findings", {"query": "gap", "finding_type": "observation"}),
            ("get_past_thesis", {"thesis_id": "ema-gap-reject"}),
            ("get_round_result", {"research_round_id": "job-12-round-8", "detail": False}),
            (
                "list_rejections",
                {
                    "round_number": 8,
                    "rejection_code": "thesis_quality_theme_cluster_fixation",
                    "limit": 5,
                },
            ),
            ("get_rejection", {"round_number": 8, "thesis_id": "ema-gap-reject"}),
            ("get_rejection", {"round_number": 8, "thesis_id": "missing"}),
            ("rejection_pattern_summary", {"window_rounds": 10}),
        ],
    )

    out = conductor.run_research_conductor_sync(
        "",
        "experiment history",
        {"status": "keep"},
        research_round=8,
        family_name="ema",
        current_job=12,
    )

    assert out is not None
    assert out.should_stop is True
    output_by_tool = dict(tool_outputs)
    assert output_by_tool["search_findings"] == "No findings found."
    assert output_by_tool["get_past_thesis"] == "not-json"
    assert output_by_tool["get_round_result"] == json.dumps({"status": "not_found"})
    listed = json.loads(output_by_tool["list_rejections"])
    assert listed[0]["thesis_id"] == "ema-gap-reject"
    assert listed[0]["evidence"]["shared_keywords"] == ["gap", "open"]
    fetched_outputs = [output for name, output in tool_outputs if name == "get_rejection"]
    assert (
        json.loads(fetched_outputs[0])["rejection_code"] == "thesis_quality_theme_cluster_fixation"
    )
    assert json.loads(fetched_outputs[1]) == {"status": "not_found"}
    assert json.loads(output_by_tool["rejection_pattern_summary"]) == [
        {
            "rejection_code": "thesis_quality_theme_cluster_fixation",
            "count": 1,
            "example_thesis_ids": ["ema-gap-reject"],
        }
    ]


def test_conductor_tool_errors_and_final_output_fallback_are_reported(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _web(query: str, context: str = "", reflexion_feedback: str = "") -> str:
        return f"web evidence for {query}"

    monkeypatch.setattr(conductor, "_call_web_researcher", _web)
    monkeypatch.setattr(
        conductor,
        "search_research_findings",
        lambda **kwargs: [{"error": "palace unavailable"}],
    )
    monkeypatch.setattr(
        conductor,
        "get_round_result_for_root",
        lambda *args, **kwargs: "not-json",
    )
    tool_outputs: list[tuple[str, str]] = []
    final_payload = {
        "reasoning": "tool errors observed; stop",
        "suggested_theses": [],
        "should_stop": True,
    }
    _patch_conductor_runner(
        monkeypatch,
        "",
        tool_outputs=tool_outputs,
        tool_calls=[
            ("web_search", {"query": "EMA gap failures", "context": ""}),
            ("analyze_trades", {"focus_question": "after web but no trades"}),
            ("search_findings", {"query": "gap", "finding_type": "observation"}),
            ("get_round_result", {"research_round_id": "job-1-round-10", "detail": False}),
        ],
        final_output=final_payload,
        final_output_as_error=True,
        stream_event_count=1,
    )

    out = conductor.run_research_conductor_sync(
        "",
        "experiment history",
        {"status": "keep"},
        research_round=10,
        family_name="ema",
    )

    assert out is not None
    assert out.should_stop is True
    assert out.reasoning == "tool errors observed; stop"
    output_by_tool = dict(tool_outputs)
    assert output_by_tool["web_search"] == "web evidence for EMA gap failures"
    assert output_by_tool["analyze_trades"] == "ERROR: No trades file available for this round."
    assert output_by_tool["search_findings"] == "SEARCH ERROR: palace unavailable"
    assert output_by_tool["get_round_result"] == "not-json"


def test_conductor_tolerates_rejection_prompt_failure_and_memory_status_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import rejection_artifact

    monkeypatch.setattr(
        rejection_artifact,
        "render_rejection_block",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("artifact corrupt")),
    )
    monkeypatch.setattr(conductor, "_palace_status", lambda: {"error": "palace offline"})
    tool_outputs: list[tuple[str, str]] = []
    final_payload = json.dumps(
        {
            "reasoning": "memory backend unavailable; stop",
            "suggested_theses": [],
            "should_stop": True,
        }
    )
    _patch_conductor_runner(
        monkeypatch,
        "",
        tool_outputs=tool_outputs,
        tool_calls=[("memory_status", {})],
        final_output=final_payload,
        final_output_as_error=True,
    )

    out = conductor.run_research_conductor_sync(
        "",
        "experiment history",
        {"status": "keep"},
        research_round=12,
        family_name="ema",
        current_job=3,
    )

    assert out is not None
    assert out.should_stop is True
    assert dict(tool_outputs)["memory_status"] == "STATUS ERROR: palace offline"


@pytest.mark.parametrize(
    "runner_output, validation_reason",
    [
        ({"suggested_theses": [], "should_stop": False}, "expected exactly one thesis, got 0"),
        (
            {"suggested_theses": [{"thesis_id": "a"}, {"thesis_id": "b"}], "should_stop": False},
            "expected exactly one thesis, got 2",
        ),
        (
            {"suggested_theses": "not-a-list", "should_stop": False},
            "suggested_theses must be a list",
        ),
        (
            {"suggested_theses": ["not-an-object"], "should_stop": False},
            "suggested_theses[0] must be an object",
        ),
    ],
)
def test_conductor_reports_structural_validation_failures_after_required_tool_gate(
    monkeypatch: pytest.MonkeyPatch, runner_output: dict, validation_reason: str
) -> None:
    _patch_conductor_runner(
        monkeypatch,
        runner_output,
        tool_calls=[("list_round_results", {"order": "latest", "offset": 0, "limit": 1})],
    )

    out = conductor.run_research_conductor_sync(
        "",
        "full experiment history consulted by fake runner",
        {"status": "keep"},
        research_round=7,
        family_name="ema",
    )

    assert out is not None
    assert out.status == "conductor_error"
    assert out.error == "validation_failed"
    assert out.validation_reason == validation_reason


def test_conductor_accepts_single_valid_thesis_after_required_tool_gate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}
    thesis = {
        "thesis_id": "gap_rejection_timing",
        "hypothesis": "Delaying entries after opening gap failures improves signal quality.",
        "mechanism": "Wait for the first failed opening-gap rejection cluster to clear.",
        "mechanism_dimension": "signal_quality",
        "config_changes": {"entry_delay_minutes": 15},
    }

    def fake_validate(candidate, tools_called=None, **kwargs):
        captured["require_analyst_evidence"] = kwargs.get("require_analyst_evidence")
        captured["evidence_context"] = kwargs.get("evidence_context")
        captured["require_analyst_tool"] = kwargs.get("require_analyst_tool")
        return type("Validated", (), {"thesis_id": candidate["thesis_id"]})()

    monkeypatch.setattr(
        conductor,
        "validate_thesis_dict",
        fake_validate,
    )
    _patch_conductor_runner(
        monkeypatch,
        {
            "reasoning": "experiment history supports testing this mechanism",
            "suggested_theses": [thesis],
            "should_stop": False,
        },
        tool_calls=[("list_round_results", {"order": "latest", "offset": 0, "limit": 1})],
    )

    out = conductor.run_research_conductor_sync(
        "",
        "full experiment history consulted by fake runner",
        {"status": "keep"},
        research_round=9,
        family_name="ema",
    )

    assert out is not None
    assert out.thesis is not None
    assert out.thesis["thesis_id"] == "gap_rejection_timing"
    assert captured["require_analyst_evidence"] is False
    assert captured["evidence_context"] == "no_trades"
    assert captured["require_analyst_tool"] is False


def test_conductor_marks_cold_start_evidence_context_without_latest_outcome(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}
    thesis = {
        "thesis_id": "first_web_grounded_thesis",
        "hypothesis": "First-round web evidence supports testing EMA smoothing.",
        "mechanism": "EMA smoothing filters noisy first-round entry signals.",
        "mechanism_dimension": "signal_quality",
        "config_changes": {"ema_length": 8},
    }

    def fake_validate(candidate, tools_called=None, **kwargs):
        captured["evidence_context"] = kwargs.get("evidence_context")
        captured["require_analyst_tool"] = kwargs.get("require_analyst_tool")
        return type("Validated", (), {"thesis_id": candidate["thesis_id"]})()

    monkeypatch.setattr(conductor, "validate_thesis_dict", fake_validate)
    _patch_conductor_runner(
        monkeypatch,
        {
            "reasoning": "cold-start web evidence supports a first thesis",
            "suggested_theses": [thesis],
            "should_stop": False,
        },
        tool_calls=[("list_round_results", {"order": "latest", "offset": 0, "limit": 1})],
    )

    out = conductor.run_research_conductor_sync(
        "",
        "",
        {},
        research_round=1,
        family_name="ema",
    )

    assert out is not None
    assert out.thesis is not None
    assert captured["evidence_context"] == "cold_start"
    assert captured["require_analyst_tool"] is False


def test_conductor_mechanism_path_uses_rendered_corpus_only_and_skips_thesis_validator(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rendered_corpus = "## Corpus\n- family: ema\n\n## Causal Factors\n- f001\n"
    captured: dict[str, object] = {}
    monkeypatch.setattr(conductor, "_ensure_oauth_proxy", lambda: None)
    monkeypatch.setattr(conductor, "_get_openai_client", lambda url: object())
    monkeypatch.setattr(
        conductor,
        "validate_thesis_dict",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("old thesis validator must not run")
        ),
    )

    def _run_streamed(agent, user_prompt, max_turns, run_config):
        captured["user_prompt"] = user_prompt
        captured["system_prompt"] = agent.instructions
        captured["output_type"] = getattr(agent, "output_type", None)
        return _StreamedResult(
            json.dumps(
                {
                    "story": "Gap-down entries reveal loss-prone inventory.",
                    "rule": "gap_pct < 0",
                    "competitor_rule": "gap_pct > 0",
                    "competitor_story": "Gap-up entries are the real adverse-selection source.",
                    "actionable": False,
                    "proposed_change": None,
                    "predictions": None,
                }
            ),
            agent,
        )

    monkeypatch.setattr(conductor.OAIRunner, "run_streamed", _run_streamed)

    out = conductor.run_research_conductor_sync(
        "",
        "legacy round results must not be included",
        {"status": "keep"},
        research_round=12,
        family_name="ema",
        rejection_feedback="prior screening killed this rule",
        rendered_corpus=rendered_corpus,
    )

    assert out is not None
    assert out.status == "ok"
    assert out.thesis is not None
    assert out.thesis["story"] == "Gap-down entries reveal loss-prone inventory."
    assert out.thesis["competitor_rule"] == "gap_pct > 0"
    assert captured["user_prompt"] == rendered_corpus
    assert "prior screening killed this rule" not in str(captured["user_prompt"])
    assert "legacy round results" not in str(captured["user_prompt"])
    assert "feature_table" not in str(captured["system_prompt"])
    assert "residual" in str(captured["system_prompt"]).lower()
    assert "story, rule, competitor_rule" in str(captured["system_prompt"])
    assert getattr(captured["output_type"], "__name__", "") == "MechanismProposal"


def test_mechanism_proposal_schema_requires_actionable_change_and_distinct_predictions() -> None:
    with pytest.raises(ValueError, match="proposed_change"):
        MechanismProposal(
            story="Harvest this now.",
            rule="gap_pct < 0",
            competitor_rule="gap_pct > 0",
            competitor_story="Opposite gap sign explains the effect.",
            actionable=True,
            proposed_change=None,
            predictions=None,
        )

    with pytest.raises(ValueError, match="distinct"):
        MechanismProposal(
            story="Harvest this now.",
            rule="gap_pct < 0",
            competitor_rule="gap_pct > 0",
            competitor_story="Opposite gap sign explains the effect.",
            actionable=True,
            proposed_change={"gap_filter": True},
            predictions=[
                {"metric": "profit_factor", "direction": "increase", "predicted": 1.2},
                {"metric": "profit_factor", "direction": "increase", "predicted": 1.3},
            ],
        )

    proposal = MechanismProposal(
        story="Harvest this now.",
        rule="gap_pct < 0",
        competitor_rule="gap_pct > 0",
        competitor_story="Opposite gap sign explains the effect.",
        actionable=True,
        proposed_change={"gap_filter": True},
        predictions=[
            {"metric": "profit_factor", "direction": "increase", "predicted": 1.2},
            {"metric": "pnl_weighted_accuracy", "direction": "increase", "predicted": 0.65},
        ],
    )

    assert proposal.predictions is not None
    assert {prediction.metric.value for prediction in proposal.predictions} == {
        "profit_factor",
        "pnl_weighted_accuracy",
    }


def test_mechanism_proposal_schema_allows_declared_orb_coupled_change() -> None:
    proposal = MechanismProposal(
        story="Volatility trails should protect failed ORB extensions.",
        rule="or_width_pctile > 0.8",
        competitor_rule="or_width_pctile <= 0.8",
        competitor_story="Narrow ranges may explain the edge instead.",
        actionable=True,
        proposed_change={"use_volatility_trail": True, "vol_trail_atr_mult": 1.5},
        predictions=[
            {"metric": "profit_factor", "direction": "increase", "predicted": 1.4},
            {"metric": "max_drawdown", "direction": "decrease", "predicted": 0.2},
        ],
    )

    assert proposal.proposed_change == {"use_volatility_trail": True, "vol_trail_atr_mult": 1.5}


def test_conductor_reports_thesis_validator_failure_after_required_tool_gate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    thesis = {
        "thesis_id": "gap_rejection_timing",
        "hypothesis": "Delaying entries after opening gap failures improves signal quality.",
        "mechanism": "Wait for the first failed opening-gap rejection cluster to clear.",
        "mechanism_dimension": "signal_quality",
        "config_changes": {"entry_delay_minutes": 15},
    }
    monkeypatch.setattr(
        conductor,
        "validate_thesis_dict",
        lambda candidate, tools_called=None, **kwargs: (_ for _ in ()).throw(
            ValueError("mechanism_dimension is stale")
        ),
    )
    _patch_conductor_runner(
        monkeypatch,
        {
            "reasoning": "experiment history supports testing this mechanism",
            "suggested_theses": [thesis],
            "should_stop": False,
        },
        tool_calls=[("list_round_results", {"order": "latest", "offset": 0, "limit": 1})],
    )

    out = conductor.run_research_conductor_sync(
        "",
        "full experiment history consulted by fake runner",
        {"status": "keep"},
        research_round=11,
        family_name="ema",
    )

    assert out is not None
    assert out.status == "conductor_error"
    assert out.error == "validation_failed"
    assert out.validation_reason == "mechanism_dimension is stale"
    # The conductor now attaches the parsed thesis on validation failure so
    # _check_parsed_for_terminal can defer to _try_one_validation_attempt's
    # Stage 1 retry budget instead of treating the round as terminal.
    assert out.thesis is not None
    assert out.thesis["thesis_id"] == "gap_rejection_timing"


def test_conductor_reports_parse_failed_for_non_json_runner_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_conductor_runner(monkeypatch, "not json")

    out = conductor.run_research_conductor_sync(
        "",
        "",
        {},
        research_round=1,
        family_name="ema",
    )

    assert out is not None
    assert out.status == "conductor_error"
    assert out.error == "parse_failed"


def test_conductor_accepts_thesis_returned_directly_without_suggested_theses_wrapper(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """v3 prompt tells conductor 'Return ONLY the JSON object'; harness must handle
    direct thesis response (no suggested_theses wrapper) — regression for bug introduced
    in ccba643 (2026-05-10) when the 436-line prompt was replaced with v3."""
    thesis = {
        "thesis_id": "open_alert_regime_filter",
        "hypothesis": "Early-open alerts (<=09:35) are dominated by adverse selection.",
        "mechanism": "Opening auction noise causes systematic stop-outs in first two bars.",
        "mechanism_dimension": "time_regime_microstructure",
        "config_changes": {"min_alert_time": "09:40"},
    }
    monkeypatch.setattr(
        conductor,
        "validate_thesis_dict",
        lambda candidate, tools_called=None, **kwargs: type(
            "Validated", (), {"thesis_id": candidate["thesis_id"]}
        )(),
    )
    # Conductor returns thesis directly — NO suggested_theses wrapper (v3 prompt contract)
    _patch_conductor_runner(
        monkeypatch,
        thesis,
        tool_calls=[("list_round_results", {"order": "latest", "offset": 0, "limit": 1})],
    )

    out = conductor.run_research_conductor_sync(
        "",
        "full experiment history consulted",
        {"status": "keep"},
        research_round=1,
        family_name="ema",
    )

    assert out is not None
    assert out.thesis is not None
    assert out.thesis["thesis_id"] == "open_alert_regime_filter"


def test_conductor_default_job_context_preserves_round_id_for_thesis_assignment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}
    thesis = {
        "thesis_id": "open_alert_regime_filter",
        "hypothesis": "Early-open alerts are dominated by adverse selection.",
        "mechanism": "Opening auction noise causes systematic stop-outs.",
        "mechanism_dimension": "time_regime_microstructure",
        "config_changes": {"min_alert_time": "09:40"},
    }

    def _validate(candidate, **kwargs):
        captured.update(kwargs)
        assigned = kwargs["assign_thesis_id"](
            kwargs["research_round_id"],
            kwargs["attempt_number"],
        )
        captured["assigned_thesis_id"] = assigned
        return type("Validated", (), {"thesis_id": assigned})()

    monkeypatch.setattr(conductor, "validate_thesis_dict", _validate)
    _patch_conductor_runner(
        monkeypatch,
        thesis,
        tool_calls=[("list_round_results", {"order": "latest", "offset": 0, "limit": 1})],
    )

    out = conductor.run_research_conductor_sync(
        "",
        "full experiment history consulted",
        {"status": "keep"},
        research_round=3,
        family_name="ema",
    )

    assert out is not None
    assert out.thesis is not None
    assert captured["research_round_id"] == "job-0-round-3"
    assert captured["assigned_thesis_id"] == "job-0-round-3-attempt-1"


def test_build_round_index_exposes_round_refs_and_latest_round(tmp_path: Path, monkeypatch) -> None:
    db = BacktestRunDB(tmp_path / "ema_backtest_runs.db")
    db.init_session(name="ema", metric_name="profit_factor", direction="higher")
    baseline = _record(job=7, round_number=0, baseline=True, root=tmp_path)
    round_one = _record(job=7, round_number=1, root=tmp_path)
    db.add(baseline)
    db.add(round_one)
    monkeypatch.setattr(
        subagents, "_resolve_backtest_db_path", lambda trades_file, family_name: db.path
    )

    index = subagents._build_round_index(trades_file=round_one.trades_file, family_name="ema")

    assert index is not None
    assert index.round_ref_map["round:0"] == "baseline"
    assert "round:1" in index.round_ref_map

    resolved_ref, artifacts, summary = subagents._resolve_artifacts_for_ref(index, "round:1")
    assert resolved_ref.startswith("sequence:")
    assert artifacts["trades_csv"] == round_one.trades_file
    assert summary["research_round_number"] == 1

    _, latest_artifacts, latest_summary = subagents._resolve_artifacts_for_ref(index, "latest")
    assert latest_artifacts["trades_csv"] == round_one.trades_file
    assert latest_summary["research_round_number"] == 1
