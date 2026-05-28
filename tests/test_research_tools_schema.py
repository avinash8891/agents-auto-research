import pytest
from pydantic import ValidationError

from research_tools_schema import (
    FINDING_STATUSES,
    FINDING_TYPES,
    AnalyzeTradesArgs,
    GetPastThesisArgs,
    GetRejectionArgs,
    GetRoundResultArgs,
    ListPastThesesArgs,
    ListRejectionsArgs,
    ListRoundResultsArgs,
    MemoryStatusArgs,
    RejectionPatternSummaryArgs,
    SaveFindingArgs,
    SearchFindingsArgs,
    WebSearchArgs,
)


def test_all_models_instantiate_with_minimal_valid_args():
    """Smoke test: all models construct without error given minimal valid args."""
    AnalyzeTradesArgs(focus_question="test question")
    WebSearchArgs(query="test query")
    SaveFindingArgs(
        finding="EMA crossover on 9/21 shows PF=2.1 across 847 trades",
        finding_type="observation",
        status="unvalidated",
        evidence="round_001, thesis ema_crossover_baseline",
        scope="train_2021-2023",
        expires_if="fails on out-of-sample validation",
    )
    SearchFindingsArgs(query="ema crossover")
    ListPastThesesArgs()
    GetPastThesisArgs(thesis_id="ema_crossover_baseline")
    ListRoundResultsArgs()
    GetRoundResultArgs(research_round_id="job-1-round-3")
    ListRejectionsArgs()
    GetRejectionArgs(round_number=1, thesis_id="ema_crossover_baseline")
    RejectionPatternSummaryArgs()
    MemoryStatusArgs()


def test_analyze_trades_valid():
    AnalyzeTradesArgs(focus_question="Why do gaps fail on Fridays?")


def test_analyze_trades_empty_question():
    with pytest.raises(ValidationError, match="focus_question"):
        AnalyzeTradesArgs(focus_question="")


def test_web_search_valid():
    WebSearchArgs(query="ORB strategy gap filter")


def test_web_search_valid_with_context():
    WebSearchArgs(query="VWAP reversion", context="Investigating Tuesday bias")


def test_web_search_empty_query():
    with pytest.raises(ValidationError, match="query"):
        WebSearchArgs(query="")


def test_save_finding_valid():
    SaveFindingArgs(
        finding="Tuesday PF=1.7 vs Friday PF=2.7 across 3017 trades",
        finding_type="observation",
        status="unvalidated",
        evidence="round_003, thesis entry_window_test",
        scope="train_2020-2023",
        expires_if="fails on validation split",
    )


def test_save_finding_invalid_type():
    with pytest.raises(ValidationError, match="finding_type"):
        SaveFindingArgs(
            finding="some finding",
            finding_type="fact",
            status="unvalidated",
            evidence="round_001",
            scope="full_sample",
            expires_if="never",
        )


def test_save_finding_invalid_status():
    with pytest.raises(ValidationError, match="status"):
        SaveFindingArgs(
            finding="some finding",
            finding_type="observation",
            status="pending",
            evidence="round_001",
            scope="full_sample",
            expires_if="never",
        )


def test_save_finding_empty_finding():
    with pytest.raises(ValidationError, match="finding"):
        SaveFindingArgs(
            finding="",
            finding_type="observation",
            status="unvalidated",
            evidence="round_001",
            scope="full_sample",
            expires_if="never",
        )


def test_save_finding_all_valid_types():
    for ft in FINDING_TYPES:
        SaveFindingArgs(
            finding="test",
            finding_type=ft,
            status="unvalidated",
            evidence="round_001",
            scope="full_sample",
            expires_if="never",
        )


def test_save_finding_all_valid_statuses():
    for s in FINDING_STATUSES:
        SaveFindingArgs(
            finding="test",
            finding_type="observation",
            status=s,
            evidence="round_001",
            scope="full_sample",
            expires_if="never",
        )


def test_search_findings_valid_no_type():
    SearchFindingsArgs(query="gap filter")


def test_search_findings_valid_with_type():
    SearchFindingsArgs(query="gap filter", finding_type="validated_finding")


def test_search_findings_empty_string_type_allowed():
    SearchFindingsArgs(query="gap filter", finding_type="")


def test_search_findings_invalid_type():
    with pytest.raises(ValidationError, match="finding_type"):
        SearchFindingsArgs(query="gap filter", finding_type="bad_type")


def test_search_findings_empty_query():
    with pytest.raises(ValidationError, match="query"):
        SearchFindingsArgs(query="")


def test_list_past_theses_defaults():
    args = ListPastThesesArgs()
    assert args.offset == 0
    assert args.limit == 25


def test_list_past_theses_valid():
    ListPastThesesArgs(offset=10, limit=50)


def test_list_past_theses_negative_offset():
    with pytest.raises(ValidationError, match="offset"):
        ListPastThesesArgs(offset=-1)


def test_list_past_theses_limit_zero():
    with pytest.raises(ValidationError, match="limit"):
        ListPastThesesArgs(limit=0)


def test_list_past_theses_limit_over_max():
    with pytest.raises(ValidationError, match="limit"):
        ListPastThesesArgs(limit=101)


def test_get_past_thesis_valid():
    GetPastThesisArgs(thesis_id="ema_gap_filter_v2")


def test_get_past_thesis_empty_id():
    with pytest.raises(ValidationError, match="thesis_id"):
        GetPastThesisArgs(thesis_id="")


def test_list_round_results_defaults():
    args = ListRoundResultsArgs()
    assert args.order == "latest"
    assert args.offset == 0
    assert args.limit == 10
    assert args.job_id is None
    assert args.family is None


def test_list_round_results_valid():
    args = ListRoundResultsArgs(order="best", offset=5, limit=20, job_id=7, family="ema")
    assert args.family == "ema"


def test_list_round_results_rejects_empty_family():
    with pytest.raises(ValidationError, match="family"):
        ListRoundResultsArgs(family="")


def test_list_round_results_worst_order():
    ListRoundResultsArgs(order="worst")


def test_list_round_results_invalid_order():
    with pytest.raises(ValidationError, match="order"):
        ListRoundResultsArgs(order="random")


def test_list_round_results_limit_over_max():
    # Runtime cap in research_memory._clamp_pagination is 50; schema mirrors it.
    with pytest.raises(ValidationError, match="limit"):
        ListRoundResultsArgs(limit=51)


def test_list_round_results_negative_offset():
    with pytest.raises(ValidationError, match="offset"):
        ListRoundResultsArgs(offset=-1)


def test_get_round_result_valid():
    args = GetRoundResultArgs(research_round_id="job-1-round-3")
    assert args.job_id is None
    assert args.family is None


def test_get_round_result_with_detail():
    GetRoundResultArgs(research_round_id="job-1-round-3", detail=True)


def test_get_round_result_with_job_and_family():
    args = GetRoundResultArgs(research_round_id="job-2-round-5", job_id=2, family="orb")
    assert args.job_id == 2
    assert args.family == "orb"


def test_get_round_result_empty_id():
    with pytest.raises(ValidationError, match="research_round_id"):
        GetRoundResultArgs(research_round_id="")


def test_get_round_result_rejects_empty_family():
    with pytest.raises(ValidationError, match="family"):
        GetRoundResultArgs(research_round_id="job-1-round-3", family="")


def test_list_rejections_defaults():
    args = ListRejectionsArgs()
    assert args.round_number is None
    assert args.rejection_code is None
    assert args.limit == 25


def test_list_rejections_valid():
    ListRejectionsArgs(
        round_number=3, rejection_code="thesis_quality_theme_cluster_fixation", limit=10
    )


def test_list_rejections_empty_rejection_code():
    with pytest.raises(ValidationError, match="rejection_code"):
        ListRejectionsArgs(rejection_code="")


def test_list_rejections_negative_round():
    with pytest.raises(ValidationError, match="round_number"):
        ListRejectionsArgs(round_number=-1)


def test_list_rejections_limit_over_max():
    with pytest.raises(ValidationError, match="limit"):
        ListRejectionsArgs(limit=101)


def test_get_rejection_valid():
    GetRejectionArgs(round_number=3, thesis_id="ema_gap_filter_v2")


def test_get_rejection_negative_round():
    with pytest.raises(ValidationError, match="round_number"):
        GetRejectionArgs(round_number=-1, thesis_id="ema_gap_filter_v2")


def test_get_rejection_empty_thesis_id():
    with pytest.raises(ValidationError, match="thesis_id"):
        GetRejectionArgs(round_number=3, thesis_id="")


def test_rejection_pattern_summary_default():
    args = RejectionPatternSummaryArgs()
    assert args.window_rounds == 10


def test_rejection_pattern_summary_valid():
    RejectionPatternSummaryArgs(window_rounds=20)


def test_rejection_pattern_summary_zero():
    with pytest.raises(ValidationError, match="window_rounds"):
        RejectionPatternSummaryArgs(window_rounds=0)


def test_rejection_pattern_summary_over_max():
    with pytest.raises(ValidationError, match="window_rounds"):
        RejectionPatternSummaryArgs(window_rounds=51)


def test_memory_status_no_args():
    MemoryStatusArgs()


# --- _dispatch helper ---


def test_dispatch_valid_returns_none():
    from research_tools_mcp import _TOOL_MODELS, _dispatch  # noqa: F401
    from research_tools_schema import WebSearchArgs as _WebSearchArgs

    result = _dispatch(_WebSearchArgs, {"query": "ORB gap filter on Tuesday open"})
    assert result is None


def test_dispatch_invalid_returns_validation_error_string():
    from research_tools_mcp import _dispatch
    from research_tools_schema import WebSearchArgs as _WebSearchArgs

    result = _dispatch(_WebSearchArgs, {"query": ""})
    assert isinstance(result, str)
    assert result.startswith("VALIDATION ERROR:")


def test_dispatch_missing_required_field():
    from research_tools_mcp import _dispatch
    from research_tools_schema import WebSearchArgs as _WebSearchArgs

    result = _dispatch(_WebSearchArgs, {})
    assert isinstance(result, str)
    assert result.startswith("VALIDATION ERROR:")


def test_enforce_tool_models_raises_for_unregistered_tool():
    """A tool registered via @mcp.tool() but absent from _TOOL_MODELS raises TypeError."""
    from mcp.server.fastmcp import FastMCP

    from research_tools_mcp import _TOOL_MODELS, _enforce_tool_models

    mcp = FastMCP("test-enforcement")

    @mcp.tool()
    async def unregistered_tool(x: str) -> str:
        return x

    with pytest.raises(TypeError, match="unregistered_tool"):
        _enforce_tool_models(mcp, _TOOL_MODELS)


def test_enforce_tool_models_passes_for_empty_mcp():
    """An MCP with no tools registered passes enforcement with any tool_models dict."""
    from mcp.server.fastmcp import FastMCP

    from research_tools_mcp import _TOOL_MODELS, _enforce_tool_models

    mcp = FastMCP("test-empty")
    _enforce_tool_models(mcp, _TOOL_MODELS)  # no tools registered, nothing to check
