from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from trace_adapters.artifacts import redact_text

LARGE_TOOL_OUTPUT_CHARS = 12_000


@dataclass
class RoleStats:
    events: int = 0
    tool_calls: int = 0
    tool_results: int = 0
    failed_tool_results: int = 0
    failed_tool_breakdown: Counter[str] = field(default_factory=Counter)
    failed_tool_examples: list[dict[str, Any]] = field(default_factory=list)
    large_tool_results: list[dict[str, Any]] = field(default_factory=list)
    repeated_tool_inputs: list[dict[str, Any]] = field(default_factory=list)
    usage: defaultdict[str, float] = field(default_factory=lambda: defaultdict(float))


def load_trace_events(paths: Iterable[Path]) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for path in paths:
        with path.open(encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, 1):
                stripped = line.strip()
                if not stripped:
                    continue
                try:
                    event = json.loads(stripped)
                except json.JSONDecodeError as exc:
                    raise ValueError(f"{path}:{line_number}: invalid JSONL event: {exc}") from exc
                event["_trace_path"] = str(path)
                event["_line_number"] = line_number
                events.append(event)
    return events


def _payload(event: dict[str, Any]) -> dict[str, Any]:
    payload = event.get("payload")
    return payload if isinstance(payload, dict) else {}


def _role(event: dict[str, Any]) -> str:
    payload = _payload(event)
    for key in ("agent", "role", "agent_name"):
        value = payload.get(key) or event.get(key)
        if isinstance(value, str) and value.strip():
            return _normalize_role(value.strip())
    source = str(event.get("source_module") or "")
    category = str(event.get("category") or "")
    if source.startswith("trace_adapters."):
        return _normalize_role(source.rsplit(".", 1)[-1])
    if source.startswith("trace_"):
        return _normalize_role(source)
    if category in {"halo", "recursive_improve", "reflexio", "quality", "autonomy"}:
        return _normalize_role(category)
    return "unknown"


def _normalize_role(role: str) -> str:
    normalized = role.strip().replace("-", "_")
    aliases = {
        "research_conductor": "conductor",
        "web_research": "web_researcher",
    }
    return aliases.get(normalized, normalized)


def _tool_name(event: dict[str, Any]) -> str:
    payload = _payload(event)
    for key in ("tool", "tool_name", "name"):
        value = payload.get(key) or event.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return "unknown_tool"


def _text_len(value: Any) -> int:
    if value is None:
        return 0
    if isinstance(value, str):
        return len(value)
    try:
        return len(json.dumps(value, sort_keys=True))
    except TypeError:
        return len(str(value))


def _event_len(event: dict[str, Any], *keys: str) -> int:
    payload = _payload(event)
    for key in keys:
        value = payload.get(key)
        if isinstance(value, int):
            return value
        if value is not None:
            return _text_len(value)
    return 0


def _usage_numbers(event: dict[str, Any]) -> dict[str, float]:
    payload = _payload(event)
    usage = payload.get("usage") or payload.get("data") or payload
    if not isinstance(usage, dict):
        return {}

    numbers: dict[str, float] = defaultdict(float)

    def visit(prefix: str, obj: Any) -> None:
        if isinstance(obj, dict):
            for key, value in obj.items():
                child_prefix = f"{prefix}.{key}" if prefix else str(key)
                visit(child_prefix, value)
        elif isinstance(obj, (int, float)) and not isinstance(obj, bool):
            lowered = prefix.lower()
            if "token" in lowered or lowered == "cost_usd" or lowered.endswith(".cost_usd"):
                numbers[prefix] += float(obj)

    visit("", usage)
    return dict(numbers)


def analyze_trace_events(events: Iterable[dict[str, Any]]) -> dict[str, Any]:
    role_stats: dict[str, RoleStats] = defaultdict(RoleStats)
    outcomes: Counter[str] = Counter()
    state_transitions: Counter[str] = Counter()
    tool_inputs: Counter[tuple[str, str, str, str]] = Counter()
    seen_outcomes: set[tuple[str, Any, Any, Any]] = set()
    findings: list[dict[str, Any]] = []

    event_list = list(events)
    for event in event_list:
        role = _role(event)
        stats = role_stats[role]
        stats.events += 1

        action = str(event.get("action") or "")
        category = str(event.get("category") or "")
        payload = _payload(event)

        if action == "research_round":
            outcome = (
                payload.get("loop_state", {}).get("outcome")
                or payload.get("episode", {}).get("outcome")
                or payload.get("iteration_context", {}).get("status")
            )
            if isinstance(outcome, str) and outcome:
                outcome_key = (
                    str(event.get("_trace_path") or event.get("run_id") or ""),
                    payload.get("loop_state", {}).get("research_round")
                    or payload.get("episode", {}).get("round")
                    or payload.get("iteration_context", {}).get("round"),
                    payload.get("loop_state", {}).get("thesis_id")
                    or payload.get("episode", {}).get("thesis_id")
                    or payload.get("iteration_context", {}).get("candidate_id"),
                    outcome,
                )
                if outcome_key not in seen_outcomes:
                    seen_outcomes.add(outcome_key)
                    outcomes[outcome] += 1

        if category == "state" and action == "transition":
            old_state = payload.get("old_state")
            new_state = payload.get("new_state")
            if old_state and new_state:
                state_transitions[f"{old_state}->{new_state}"] += 1

        if action == "tool_call":
            stats.tool_calls += 1
            tool = _tool_name(event)
            input_value = (
                payload.get("input")
                or payload.get("arguments")
                or payload.get("query")
                or payload.get("tool_input_preview")
            )
            input_key = json.dumps(input_value, sort_keys=True, default=str)[:500]
            trace_key = str(event.get("_trace_path") or event.get("run_id") or "")
            tool_inputs[(trace_key, role, tool, input_key)] += 1

        if action == "tool_result":
            stats.tool_results += 1
            tool = _tool_name(event)
            output_len = _event_len(
                event,
                "output_len",
                "tool_output_length",
                "result_len",
                "output",
                "tool_output_preview",
                "result",
            )
            status = str(payload.get("status") or "").lower()
            error = payload.get("error")
            if error or status in {"error", "failed", "failure"}:
                stats.failed_tool_results += 1
                error_type = str(payload.get("error_type") or payload.get("error") or status)
                stats.failed_tool_breakdown[f"{tool}:{error_type}"] += 1
                if len(stats.failed_tool_examples) < 5:
                    stats.failed_tool_examples.append(
                        {
                            "tool": tool,
                            "error_type": redact_text(error_type, max_chars=220),
                            "preview": redact_text(
                                str(payload.get("tool_output_preview") or error or ""),
                                max_chars=220,
                            ),
                            "trace_path": event.get("_trace_path"),
                            "event_id": event.get("event_id"),
                        }
                    )
            if output_len >= LARGE_TOOL_OUTPUT_CHARS:
                stats.large_tool_results.append(
                    {
                        "tool": tool,
                        "output_len": output_len,
                        "event_id": event.get("event_id"),
                        "trace_path": event.get("_trace_path"),
                    }
                )

        if action == "usage" or category == "usage":
            for key, value in _usage_numbers(event).items():
                stats.usage[key] += value

    for (_trace_key, role, tool, _input_key), count in tool_inputs.items():
        if count > 1:
            role_stats[role].repeated_tool_inputs.append({"tool": tool, "count": count})

    for role, stats in sorted(role_stats.items()):
        if stats.tool_calls and not stats.tool_results:
            findings.append(
                {
                    "severity": "P1",
                    "role": role,
                    "issue": "tool calls without matching tool results",
                    "evidence": f"{stats.tool_calls} calls, 0 results",
                    "recommendation": "Trace both call and result at the same integration boundary.",
                }
            )
        if stats.failed_tool_results:
            findings.append(
                {
                    "severity": "P2",
                    "role": role,
                    "issue": "failed tool results",
                    "evidence": f"{stats.failed_tool_results} failed tool results",
                    "recommendation": "Use typed artifact manifests and exact paths before allowing exploratory probes.",
                    "details": dict(stats.failed_tool_breakdown.most_common(4)),
                }
            )
        if stats.large_tool_results:
            largest = max(stats.large_tool_results, key=lambda item: item["output_len"])
            findings.append(
                {
                    "severity": "P2",
                    "role": role,
                    "issue": "large tool output re-entering agent context",
                    "evidence": f"{largest['tool']} returned {largest['output_len']} chars",
                    "recommendation": "Return compact summaries or artifact references instead of raw rows/files.",
                }
            )
        if stats.repeated_tool_inputs:
            repeated = max(stats.repeated_tool_inputs, key=lambda item: item["count"])
            findings.append(
                {
                    "severity": "P3",
                    "role": role,
                    "issue": "repeated identical tool input",
                    "evidence": f"{repeated['tool']} repeated {repeated['count']} times",
                    "recommendation": "Cache tool results within a round or make the prompt forbid repeat probes.",
                }
            )

    if state_transitions.get("running->blocked", 0) > 1:
        findings.append(
            {
                "severity": "P2",
                "role": "controller",
                "issue": "repeated running-to-blocked transitions",
                "evidence": f"{state_transitions['running->blocked']} transitions",
                "recommendation": "Ensure state reflects the active long-running phase and clears stale next_action fields.",
            }
        )

    return {
        "event_count": len(event_list),
        "roles": role_stats,
        "outcomes": outcomes,
        "state_transitions": state_transitions,
        "findings": findings,
    }


def render_markdown_report(analysis: dict[str, Any], trace_paths: Iterable[Path]) -> str:
    paths = [str(path) for path in trace_paths]
    lines = [
        "# Trace Improvement Report",
        "",
        "## Inputs",
        "",
    ]
    lines.extend(f"- `{path}`" for path in paths)
    lines.extend(
        [
            "",
            "## Summary",
            "",
            f"- Events analyzed: {analysis['event_count']}",
            f"- Roles seen: {', '.join(sorted(analysis['roles'])) or 'none'}",
            "",
            "## Outcomes",
            "",
        ]
    )
    outcomes: Counter[str] = analysis["outcomes"]
    if outcomes:
        lines.extend(f"- `{outcome}`: {count}" for outcome, count in outcomes.most_common())
    else:
        lines.append("- No research-round outcome events found.")

    lines.extend(
        [
            "",
            "## Role Tool Usage",
            "",
            "| Role | Events | Tool calls | Tool results | Failed results | Usage fields |",
            "|---|---:|---:|---:|---:|---|",
        ]
    )
    for role, stats in sorted(analysis["roles"].items()):
        usage_fields = ", ".join(
            f"{key}={value:g}"
            for key, value in sorted(stats.usage.items(), key=lambda item: item[1], reverse=True)[
                :4
            ]
        )
        lines.append(
            f"| {role} | {stats.events} | {stats.tool_calls} | {stats.tool_results} | "
            f"{stats.failed_tool_results} | {usage_fields or '-'} |"
        )

    lines.extend(["", "## Findings", ""])
    findings = analysis["findings"]
    if not findings:
        lines.append("- No trace-level improvement findings detected by deterministic checks.")
    else:
        for finding in findings:
            detail_text = ""
            if finding.get("details"):
                detail_text = " Details: " + ", ".join(
                    f"{key}={value}" for key, value in finding["details"].items()
                )
            lines.append(
                f"- `{finding['severity']}` `{finding['role']}`: {finding['issue']}. "
                f"Evidence: {finding['evidence']}.{detail_text} "
                f"Recommendation: {finding['recommendation']}"
            )

    lines.extend(["", "## Failed Tool Examples", ""])
    wrote_example = False
    for role, stats in sorted(analysis["roles"].items()):
        for example in stats.failed_tool_examples:
            wrote_example = True
            lines.append(
                f"- `{role}` `{example['tool']}` `{example['error_type']}` "
                f"at `{example.get('trace_path')}` event `{example.get('event_id')}`: "
                f"{example['preview'] or '(no preview)'}"
            )
    if not wrote_example:
        lines.append("- No failed tool examples captured.")

    lines.extend(["", "## State Transitions", ""])
    transitions: Counter[str] = analysis["state_transitions"]
    if transitions:
        lines.extend(f"- `{name}`: {count}" for name, count in transitions.most_common())
    else:
        lines.append("- No state transitions found.")
    lines.append("")
    return "\n".join(lines)


def write_trace_improvement_report(trace_paths: Iterable[Path], output_path: Path) -> Path:
    path_list = list(trace_paths)
    events = load_trace_events(path_list)
    analysis = analyze_trace_events(events)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(render_markdown_report(analysis, path_list), encoding="utf-8")
    return output_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Generate report-only agent trace improvement findings."
    )
    parser.add_argument(
        "trace_jsonl", nargs="+", type=Path, help="trace-events.jsonl files to analyze"
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("improvement_reports/trace/trace-improvement-report.md"),
        help="Markdown report path",
    )
    args = parser.parse_args(argv)
    write_trace_improvement_report(args.trace_jsonl, args.output)
    print(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
