from __future__ import annotations

import json
from pathlib import Path

from agnost_mcp import config, track

from trace_sdk import trace


def _build_research_tools_mcp(
    trades_file: str,
    strategy_events_file: str = "",
    diagnostics_file: str = "",
    *,
    call_analyst,
    call_web_researcher,
    save_research_finding,
    search_research_findings,
    palace_status,
    root: Path,
    current_job: int | None = None,
    list_past_theses_for_root,
    get_past_thesis_for_root=None,
    list_experiment_results_for_root=None,
    get_experiment_result_for_root=None,
):
    """Create an in-process MCP server with research tools."""
    from mcp.server.fastmcp import FastMCP

    mcp = FastMCP("research-tools")

    @mcp.tool()
    async def analyze_trades(focus_question: str) -> str:
        """Dispatch an independent analyst to analyze the strategy run.

        The analyst has access to:
        - trades.csv: completed/executed trades and outcomes
        - strategy_events.parquet: every setup the strategy considered, including rejected ones
        - diagnostics.json: event counts and rejection breakdown summary

        Give it a SPECIFIC focus question. For rejected signals, ask about
        rejection reasons found in diagnostics.json.

        Args:
            focus_question: What specific pattern to investigate.
        """
        if not trades_file:
            return "ERROR: No trades file available for this round."
        return await call_analyst(
            trades_file,
            focus_question,
            strategy_events_file=strategy_events_file,
            diagnostics_file=diagnostics_file,
        )

    @mcp.tool()
    async def web_search(query: str, context: str = "") -> str:
        """Search the web for external evidence about trading strategies.

        Args:
            query: Specific search query.
            context: Brief context about why you're searching.
        """
        return await call_web_researcher(query, context)

    @mcp.tool()
    async def save_finding(
        finding: str,
        finding_type: str,
        status: str,
        evidence: str,
        scope: str,
        expires_if: str,
    ) -> str:
        """Save a structured research finding to persistent memory.

        Every finding MUST have all fields. The system will reject saves
        that lack proper classification.

        Args:
            finding: The factual observation (e.g. "Tuesdays have PF=1.7 vs Friday PF=2.7 across 3017 trades")
            finding_type: One of: observation, hypothesis, validated_finding, rejected_finding, open_question, implementation_note
            status: One of: unvalidated, validated, rejected, stale
            evidence: Which round/experiment produced this (e.g. "round_003, thesis entry_window_test")
            scope: What data this applies to (e.g. "train_2020-2023", "full_sample", "SPY_only")
            expires_if: Condition that invalidates this (e.g. "fails on validation split", "baseline drift >5%")
        """
        trace(
            "CONDUCTOR",
            f"save_finding type={finding_type} status={status} finding='{finding[:80]}'",
        )
        result = save_research_finding(
            finding=finding,
            finding_type=finding_type,
            status=status,
            evidence=evidence,
            scope=scope,
            expires_if=expires_if,
        )
        if result.startswith("REJECTED"):
            if "finding_type" in result:
                trace("CONDUCTOR", f"save_finding REJECTED: bad type '{finding_type}'")
            elif "status must" in result:
                trace("CONDUCTOR", f"save_finding REJECTED: bad status '{status}'")
            return result
        if result.startswith("SAVED (local):"):
            trace(
                "CONDUCTOR",
                f"save_finding OK (local fallback): {finding_type}/{status}",
            )
            return result
        trace("CONDUCTOR", f"save_finding OK: {finding_type}/{status}")
        return result

    @mcp.tool()
    async def search_findings(query: str, finding_type: str = "") -> str:
        """Search persistent memory for previously saved findings.

        Args:
            query: What to search for (e.g. "gap filter", "Tuesday PF").
            finding_type: Optional filter by type (observation, validated_finding, etc.).
        """
        results = search_research_findings(
            query=query,
            n_results=10,
            finding_type=finding_type,
        )
        if not results:
            return "No findings found."
        if len(results) == 1 and "error" in results[0]:
            return f"SEARCH ERROR: {results[0]['error']}"
        lines = []
        for r in results:
            text = r.get("text", "")[:300]
            room_name = r.get("room", "")
            dist = r.get("distance", "?")
            lines.append(f"[{room_name}] (dist={dist}) {text}")
        return "\n---\n".join(lines)

    @mcp.tool()
    async def memory_status() -> str:
        """Get an overview of the persistent memory palace."""
        info = palace_status()
        if "error" in info:
            return f"STATUS ERROR: {info['error']}"
        return json.dumps(info, indent=2, default=str)

    @mcp.tool()
    async def list_past_theses(offset: int = 0, limit: int = 25) -> str:
        """List a bounded index of previously proposed theses with outcomes.

        Check this BEFORE proposing a new thesis. Use get_past_thesis for full
        details on relevant prior thesis IDs.
        """
        return list_past_theses_for_root(root, job_id=current_job, offset=offset, limit=limit)

    @mcp.tool()
    async def get_past_thesis(thesis_id: str) -> str:
        """Fetch full stored details for one prior thesis ID."""
        if get_past_thesis_for_root is None:
            from research_memory import get_past_thesis as _get_past_thesis_for_root

            return _get_past_thesis_for_root(root, thesis_id, job_id=current_job)
        return get_past_thesis_for_root(root, thesis_id, job_id=current_job)

    @mcp.tool()
    async def list_experiment_results(
        order: str = "latest", offset: int = 0, limit: int = 10
    ) -> str:
        """List a bounded index of experiment/backtest outcomes."""
        if list_experiment_results_for_root is None:
            from research_memory import list_experiment_results as _list_results_for_root

            return _list_results_for_root(
                root, job_id=current_job, order=order, offset=offset, limit=limit
            )
        return list_experiment_results_for_root(
            root, job_id=current_job, order=order, offset=offset, limit=limit
        )

    @mcp.tool()
    async def get_experiment_result(thesis_id: str, detail: bool = False) -> str:
        """Fetch stored details for one experiment/thesis result.

        Defaults to a compact result for context efficiency. Pass detail=true
        only when the compact result is insufficient for the current decision.
        """
        if get_experiment_result_for_root is None:
            from research_memory import get_experiment_result as _get_result_for_root

            return _get_result_for_root(root, thesis_id, job_id=current_job, detail=detail)
        return get_experiment_result_for_root(root, thesis_id, job_id=current_job, detail=detail)

    track(
        mcp,
        "a042226c-b858-46f3-9756-b1e675c03c13",
        config(
            endpoint="https://api.agnost.ai",
            identify=lambda req, env: (
                {
                    "userId": (
                        (req or {}).get("headers", {}).get("x-user-id")
                        or (req or {}).get("headers", {}).get("x-user-email")
                        or (env or {}).get("USER_ID")
                        or (env or {}).get("USER_EMAIL")
                        or (env or {}).get("USER")
                    ),
                    "sessionId": (
                        (req or {}).get("headers", {}).get("mcp-session-id")
                        or (req or {}).get("session_id")
                        or (env or {}).get("MCP_SESSION_ID")
                    ),
                    "conversationId": (
                        (req or {}).get("headers", {}).get("mcp-session-id")
                        or (req or {}).get("session_id")
                        or (env or {}).get("MCP_SESSION_ID")
                    ),
                    "email": (req or {}).get("headers", {}).get("x-user-email")
                    or (env or {}).get("USER_EMAIL"),
                    "clientId": (req or {}).get("headers", {}).get("x-client-id")
                    or (req or {}).get("client_id"),
                }
                if (
                    (req or {}).get("headers", {}).get("x-user-id")
                    or (req or {}).get("headers", {}).get("x-user-email")
                    or (env or {}).get("USER_ID")
                    or (env or {}).get("USER_EMAIL")
                    or (env or {}).get("USER")
                )
                else None
            ),
        ),
    )

    return mcp
