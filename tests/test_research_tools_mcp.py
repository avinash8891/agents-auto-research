"""End-to-end probe of every @mcp.tool() in research_tools_mcp.

For each tool: invokes it through ``mcp.call_tool()`` — the same code path the
conductor's MCP client uses — and asserts the response is well-formed.

Fixture strategy:

  * REAL fixtures for tools that read on-disk artifacts the conductor
    actually produces in production:

      - ``rejection.json`` files written by ``rejection_artifact.write_rejection``
      - ``<family>_backtest_runs.db`` populated through ``BacktestRunDB`` —
        the same insert path the controller uses

  * Stubs at the production callable boundary for tools that delegate to
    external services (LLM analyst, web search, MemPalace). These callables
    are passed into ``_build_research_tools_mcp`` by the controller in
    production — stubbing them here tests the contract boundary, not a
    mocked-out implementation.

What this guards:

  * @mcp.tool() decorator registration for every tool
  * argument-schema inference from the tool signatures
  * delegate wiring (each tool reaches its underlying function or callable)
  * return-value propagation back through the MCP layer
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

from backtest_run_db import BacktestRunDB
from persistence_utils import utc_now_iso8601
from rejection_artifact import write_rejection
from research_tools_mcp import _build_research_tools_mcp
from research_types import StructuredRejection

# ---------------------------------------------------------------------------
# Constants drawn from production: strategy families, mechanism dimensions,
# and rejection codes match what the live system emits. Toy names in tests
# mask bugs that only appear with real data.
# ---------------------------------------------------------------------------

STRATEGY_FAMILY = "ema"
MECHANISM_DIMENSION = "entry_timing"
SAMPLE_THESIS_ID = "ema-opening-noise-skip-v1"
SAMPLE_REJECTION_CODE = "thesis_quality_theme_cluster_fixation"
JOB_ID = 42
ROUND_NO = 3


# ---------------------------------------------------------------------------
# Fixtures + helpers
# ---------------------------------------------------------------------------


def _seed_rejection(
    tmp_path: Path,
    *,
    round_no: int = ROUND_NO,
    thesis_id: str = SAMPLE_THESIS_ID,
    code: str = SAMPLE_REJECTION_CODE,
) -> Path:
    rejection = StructuredRejection(
        rejected_at="2026-05-27T10:15:00Z",
        round=round_no,
        thesis_id=thesis_id,
        stage="stage_1",
        rejection_code=code,
        rule_violated="4 of last 7 theses share keywords",
        evidence={"shared_keywords": ["opening_session", "stop_distance"]},
        remediation_hint="propose from a different mechanism dimension",
        validator_version="2.3.0",
    )
    return write_rejection(tmp_path, job=JOB_ID, rejection=rejection)


def _seed_thesis_attempt(tmp_path: Path, *, thesis_id: str = SAMPLE_THESIS_ID) -> None:
    db = BacktestRunDB(tmp_path / f"{STRATEGY_FAMILY}_backtest_runs.db")
    research_round_id = f"job-{JOB_ID}-round-{ROUND_NO}"

    # The thesis attempts JOIN to research_rounds for job_id/round_number, so
    # both rows are required to match the production read path.
    with db._connect() as conn:  # noqa: SLF001 — test fixture, direct SQL is intentional
        conn.execute(
            """
            INSERT OR REPLACE INTO research_rounds (
                research_round_id, job_id, round_number, run_id, hypothesis_id,
                selected_thesis_id, outcome, created_at_utc, usage_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                research_round_id,
                JOB_ID,
                ROUND_NO,
                f"run-{JOB_ID}-{ROUND_NO}",
                thesis_id,
                thesis_id,
                "thesis_accepted",
                utc_now_iso8601(),
                "{}",
            ),
        )
        conn.commit()

    db.add_research_thesis_attempt(
        {
            "research_round_id": research_round_id,
            "attempt_number": 1,
            "thesis_id": thesis_id,
            "strategy_family": STRATEGY_FAMILY,
            "config_changes": {"opening_skip_minutes": 5},
            "validator_status": "compiled",
            "mechanism_dimension": MECHANISM_DIMENSION,
            "hypothesis": "Skipping the first 5 minutes avoids opening-auction noise",
            "mechanism": "Opening liquidity is thin; signals there are mostly noise",
            "thesis_details": {
                "expected_effects": [{"metric": "profit_factor", "direction": "increase"}],
                "evidence": ["microstructure literature on opening volatility"],
                "evidence_strength": "proxy",
            },
            "validation_failure_reason": "",
            "selected_for_execution": 1,
            "created_at_utc": utc_now_iso8601(),
        }
    )


def _make_spy(return_value: Any) -> tuple[Any, list[dict[str, Any]]]:
    calls: list[dict[str, Any]] = []

    async def _async(*args: Any, **kwargs: Any) -> Any:
        calls.append({"args": args, "kwargs": kwargs})
        return return_value

    return _async, calls


def _make_sync_spy(return_value: Any) -> tuple[Any, list[dict[str, Any]]]:
    calls: list[dict[str, Any]] = []

    def _sync(*args: Any, **kwargs: Any) -> Any:
        calls.append({"args": args, "kwargs": kwargs})
        return return_value

    return _sync, calls


def _build_mcp(
    tmp_path: Path,
    *,
    trades_file: str = "",
    call_analyst: Any = None,
    call_web_researcher: Any = None,
    save_research_finding: Any = None,
    search_research_findings: Any = None,
    palace_status: Any = None,
    current_job: int | None = JOB_ID,
) -> Any:
    analyst = call_analyst or _make_spy("ANALYST_DEFAULT")[0]
    web = call_web_researcher or _make_spy("WEB_DEFAULT")[0]
    save = save_research_finding or _make_sync_spy("SAVED")[0]
    search = search_research_findings or _make_sync_spy([])[0]
    palace = palace_status or _make_sync_spy({"status": "ok"})[0]

    return _build_research_tools_mcp(
        trades_file=trades_file,
        strategy_events_file="",
        diagnostics_file="",
        call_analyst=analyst,
        call_web_researcher=web,
        save_research_finding=save,
        search_research_findings=search,
        palace_status=palace,
        root=tmp_path,
        current_job=current_job,
        # None for the four DB-reading callables triggers the in-tool
        # fallback that lazy-imports the real research_memory functions.
        # Same code path a lightweight caller would hit.
        list_past_theses_for_root=None,
        get_past_thesis_for_root=None,
        list_experiment_results_for_root=None,
        get_experiment_result_for_root=None,
    )


def _invoke(mcp: Any, tool: str, args: dict[str, Any]) -> str:
    """Invoke a tool and return its concatenated text payload.

    FastMCP's call_tool() returns a (list[TextContent], dict) tuple; the
    list is the user-visible content, the dict carries internal metadata
    (e.g. a 'result' echo). Tests only care about the text content.
    """
    content_list, _meta = asyncio.run(mcp.call_tool(tool, args))
    return "".join(part.text for part in content_list if hasattr(part, "text"))


# ---------------------------------------------------------------------------
# Smoke: every advertised tool is actually registered
# ---------------------------------------------------------------------------


EXPECTED_TOOL_NAMES = {
    "analyze_trades",
    "web_search",
    "save_finding",
    "search_findings",
    "memory_status",
    "list_past_theses",
    "get_past_thesis",
    "list_experiment_results",
    "get_experiment_result",
    "list_rejections",
    "get_rejection",
    "rejection_pattern_summary",
}


def test_every_advertised_mcp_tool_is_registered(tmp_path: Path) -> None:
    mcp = _build_mcp(tmp_path)
    tools = asyncio.run(mcp.list_tools())
    registered = {t.name for t in tools}
    missing = EXPECTED_TOOL_NAMES - registered
    assert not missing, f"missing MCP tool registrations: {sorted(missing)}"


def test_research_memory_fallback_imports_are_hoisted() -> None:
    source = __import__("research_tools_mcp").__loader__.get_source("research_tools_mcp")
    assert "from research_memory import get_past_thesis as _get_past_thesis_for_root" in source
    assert "from research_memory import list_past_theses as _list_past_theses_for_root" in source
    function_body = source.split("def _build_research_tools_mcp", 1)[1]
    assert "from research_memory import" not in function_body


# ---------------------------------------------------------------------------
# Delegating tools (LLM / web / memory) — assert wiring + propagation
# ---------------------------------------------------------------------------


def test_analyze_trades_reaches_analyst_with_focus_question(tmp_path: Path) -> None:
    analyst, calls = _make_spy("ANALYST_REPORT: losses cluster at session open")
    mcp = _build_mcp(tmp_path, trades_file="/tmp/trades.csv", call_analyst=analyst)

    text = _invoke(mcp, "analyze_trades", {"focus_question": "when do stops fire before targets?"})

    assert text == "ANALYST_REPORT: losses cluster at session open"
    assert len(calls) == 1
    # The tool body invokes call_analyst(trades_file, focus_question, ...) —
    # focus_question is positional arg 1.
    assert calls[0]["args"][0] == "/tmp/trades.csv"
    assert calls[0]["args"][1] == "when do stops fire before targets?"


def test_analyze_trades_returns_error_when_no_trades_file(tmp_path: Path) -> None:
    analyst, calls = _make_spy("never called")
    mcp = _build_mcp(tmp_path, trades_file="", call_analyst=analyst)

    text = _invoke(mcp, "analyze_trades", {"focus_question": "anything"})

    assert "ERROR" in text
    assert calls == [], "analyst must not be called when there is no trades file"


def test_web_search_reaches_web_researcher(tmp_path: Path) -> None:
    web, calls = _make_spy("WEB_RESULT: opening-minute liquidity thin")
    mcp = _build_mcp(tmp_path, call_web_researcher=web)

    text = _invoke(
        mcp,
        "web_search",
        {"query": "EMA crossover false signals opening session", "context": "midday"},
    )

    assert text == "WEB_RESULT: opening-minute liquidity thin"
    assert len(calls) == 1
    assert "EMA crossover" in (calls[0]["kwargs"].get("query") or calls[0]["args"][0])


def test_save_finding_propagates_all_required_fields(tmp_path: Path) -> None:
    save, calls = _make_sync_spy("SAVED: finding-id-001")
    mcp = _build_mcp(tmp_path, save_research_finding=save)

    text = _invoke(
        mcp,
        "save_finding",
        {
            "finding": "Tuesdays show PF=1.7 vs Friday PF=2.7 over 3017 trades",
            "finding_type": "observation",
            "status": "unvalidated",
            "evidence": "round_001, thesis ema-opening-noise-skip-v1",
            "scope": "train_2020-2023",
            "expires_if": "fails on validation split",
        },
    )

    assert text == "SAVED: finding-id-001"
    assert len(calls) == 1
    forwarded = calls[0]["kwargs"]
    assert forwarded["finding_type"] == "observation"
    assert forwarded["status"] == "unvalidated"
    assert forwarded["scope"] == "train_2020-2023"


def test_search_findings_formats_dict_results_as_text(tmp_path: Path) -> None:
    search, _ = _make_sync_spy(
        [
            {
                "text": "opening minute losses dominate the PF gap",
                "room": "ema_research",
                "distance": 0.12,
            }
        ]
    )
    mcp = _build_mcp(tmp_path, search_research_findings=search)

    text = _invoke(mcp, "search_findings", {"query": "opening minute"})

    assert "opening minute losses" in text
    assert "ema_research" in text


def test_memory_status_returns_json_of_palace_status(tmp_path: Path) -> None:
    palace, calls = _make_sync_spy({"rooms": ["ema_research"], "findings_count": 17})
    mcp = _build_mcp(tmp_path, palace_status=palace)

    text = _invoke(mcp, "memory_status", {})
    payload = json.loads(text)

    assert payload == {"rooms": ["ema_research"], "findings_count": 17}
    assert len(calls) == 1


# ---------------------------------------------------------------------------
# Real-disk tools — past theses (SQLite)
# ---------------------------------------------------------------------------


def test_list_past_theses_returns_seeded_thesis_from_sqlite(tmp_path: Path) -> None:
    _seed_thesis_attempt(tmp_path)
    mcp = _build_mcp(tmp_path)

    text = _invoke(mcp, "list_past_theses", {"offset": 0, "limit": 25})
    payload = json.loads(text)

    assert payload["total"] >= 1
    thesis_ids = [entry["thesis_id"] for entry in payload["entries"]]
    assert SAMPLE_THESIS_ID in thesis_ids


def test_get_past_thesis_returns_full_record_from_sqlite(tmp_path: Path) -> None:
    _seed_thesis_attempt(tmp_path)
    mcp = _build_mcp(tmp_path)

    text = _invoke(mcp, "get_past_thesis", {"thesis_id": SAMPLE_THESIS_ID})

    assert "ema-opening-noise-skip-v1" in text
    assert "opening" in text.lower()


# ---------------------------------------------------------------------------
# Real-disk tools — experiment results (SQLite, empty case is meaningful)
# ---------------------------------------------------------------------------


def test_list_experiment_results_handles_empty_db_without_crashing(tmp_path: Path) -> None:
    BacktestRunDB(tmp_path / f"{STRATEGY_FAMILY}_backtest_runs.db")
    mcp = _build_mcp(tmp_path)

    text = _invoke(mcp, "list_experiment_results", {"order": "latest", "limit": 5})

    assert text  # non-empty string (either JSON payload or "no results" message)


def test_get_experiment_result_returns_message_when_thesis_unknown(tmp_path: Path) -> None:
    BacktestRunDB(tmp_path / f"{STRATEGY_FAMILY}_backtest_runs.db")
    mcp = _build_mcp(tmp_path)

    text = _invoke(
        mcp,
        "get_experiment_result",
        {"thesis_id": "thesis-that-does-not-exist", "detail": False},
    )

    assert text  # tool returns a string; the test that asserts content needs a seeded run


# ---------------------------------------------------------------------------
# Real-disk tools — rejection artifacts (the three newly wired tools)
# ---------------------------------------------------------------------------


def test_list_rejections_returns_seeded_rejection_json(tmp_path: Path) -> None:
    _seed_rejection(tmp_path)
    mcp = _build_mcp(tmp_path)

    text = _invoke(mcp, "list_rejections", {"limit": 10})
    payload = json.loads(text)

    assert isinstance(payload, list) and len(payload) == 1
    assert payload[0]["thesis_id"] == SAMPLE_THESIS_ID
    assert payload[0]["rejection_code"] == SAMPLE_REJECTION_CODE
    assert payload[0]["evidence"]["shared_keywords"] == [
        "opening_session",
        "stop_distance",
    ]


def test_list_rejections_filters_by_rejection_code(tmp_path: Path) -> None:
    _seed_rejection(tmp_path, code=SAMPLE_REJECTION_CODE)
    _seed_rejection(
        tmp_path,
        thesis_id="ema-vol-regime-v2",
        code="config_validity_neighboring_threshold",
    )
    mcp = _build_mcp(tmp_path)

    text = _invoke(
        mcp,
        "list_rejections",
        {"rejection_code": "config_validity_neighboring_threshold"},
    )
    payload = json.loads(text)

    assert len(payload) == 1
    assert payload[0]["rejection_code"] == "config_validity_neighboring_threshold"


def test_get_rejection_returns_specific_record(tmp_path: Path) -> None:
    _seed_rejection(tmp_path)
    mcp = _build_mcp(tmp_path)

    text = _invoke(
        mcp,
        "get_rejection",
        {"round_number": ROUND_NO, "thesis_id": SAMPLE_THESIS_ID},
    )
    payload = json.loads(text)

    assert payload["thesis_id"] == SAMPLE_THESIS_ID
    assert payload["rule_violated"] == "4 of last 7 theses share keywords"


def test_get_rejection_returns_error_message_when_record_missing(tmp_path: Path) -> None:
    mcp = _build_mcp(tmp_path)

    text = _invoke(
        mcp,
        "get_rejection",
        {"round_number": 99, "thesis_id": "ema-nonexistent-v9"},
    )

    assert "ERROR" in text
    assert "ema-nonexistent-v9" in text


def test_rejection_pattern_summary_groups_by_code(tmp_path: Path) -> None:
    _seed_rejection(tmp_path, round_no=1, thesis_id="ema-a", code=SAMPLE_REJECTION_CODE)
    _seed_rejection(tmp_path, round_no=2, thesis_id="ema-b", code=SAMPLE_REJECTION_CODE)
    _seed_rejection(
        tmp_path,
        round_no=3,
        thesis_id="ema-c",
        code="config_validity_neighboring_threshold",
    )
    mcp = _build_mcp(tmp_path)

    text = _invoke(mcp, "rejection_pattern_summary", {"window_rounds": 10})
    payload = json.loads(text)

    by_code = {row["rejection_code"]: row for row in payload}
    assert by_code[SAMPLE_REJECTION_CODE]["count"] == 2
    assert by_code["config_validity_neighboring_threshold"]["count"] == 1


# ---------------------------------------------------------------------------
# Guard: tools that need a job ID must fail soft when current_job is None
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "tool,args",
    [
        ("list_rejections", {}),
        ("get_rejection", {"round_number": 1, "thesis_id": "ema-x"}),
        ("rejection_pattern_summary", {}),
    ],
)
def test_rejection_tools_fail_soft_when_no_active_job(
    tmp_path: Path, tool: str, args: dict[str, Any]
) -> None:
    mcp = _build_mcp(tmp_path, current_job=None)

    text = _invoke(mcp, tool, args)

    assert "ERROR" in text
    assert "No active job" in text
