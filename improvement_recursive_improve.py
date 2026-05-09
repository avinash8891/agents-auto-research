"""Report-only Recursive Improve batch runner.

This module does not feed prompts and does not apply changes. It prepares
per-agent trace slices from the existing recursive-improve adapter export and
optionally invokes an external report command.
"""

from __future__ import annotations

import argparse
import json
import os
import shlex
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Sequence

from autoresearch_constants import (
    DEFAULT_AGENT_MODEL,
    ENV_RECURSIVE_IMPROVE_COMMAND,
    ENV_RECURSIVE_IMPROVE_INTERVAL,
    ENV_RECURSIVE_IMPROVE_MODEL,
    ENV_RECURSIVE_IMPROVE_TIMEOUT_SECONDS,
)
from autoresearch_logging import get_logger
from autoresearch_runtime_paths import research_round_trace_exports_root
from improvement_flags import recursive_improve_enabled

log = get_logger(__name__)

DEFAULT_INTERVAL = 10
DEFAULT_TIMEOUT_SECONDS = 1800
DEFAULT_AGENTS = ("conductor", "analyst", "web-researcher", "builder")

AGENT_ALIASES: dict[str, set[str]] = {
    "analyst": {"analyst", "codex-diagnostic-analyst"},
    "builder": {"builder", "compiler-builder", "codex-builder"},
    "conductor": {"conductor", "research-conductor"},
    "web-researcher": {
        "web-researcher",
        "web_researcher",
        "openai-web-researcher",
        "research-agent",
    },
}


@dataclass(frozen=True)
class RecursiveImproveReportResult:
    status: str
    output_dir: Path
    rounds: tuple[int, int] | None = None
    job: int | None = None
    agents: tuple[str, ...] = ()
    reason: str = ""
    launched: bool = False


def run_scheduled_recursive_improve_reports(
    root: str | Path,
    *,
    research_round: int,
    job: int | None,
    interval: int | None = None,
    agents: Sequence[str] = DEFAULT_AGENTS,
    execute: bool = True,
) -> RecursiveImproveReportResult:
    """Run report generation every N rounds when the feature flag is enabled."""
    root_path = Path(root)
    if not recursive_improve_enabled():
        return RecursiveImproveReportResult(
            status="skipped",
            output_dir=_default_output_dir(root_path, job, research_round, research_round),
            reason="flag_disabled",
            job=job,
        )
    resolved_interval = interval or _int_env(ENV_RECURSIVE_IMPROVE_INTERVAL, DEFAULT_INTERVAL)
    if resolved_interval <= 0:
        return RecursiveImproveReportResult(
            status="skipped",
            output_dir=_default_output_dir(root_path, job, research_round, research_round),
            reason="invalid_interval",
            job=job,
        )
    if research_round % resolved_interval != 0:
        start_round = max(1, research_round - resolved_interval + 1)
        return RecursiveImproveReportResult(
            status="skipped",
            output_dir=_default_output_dir(root_path, job, start_round, research_round),
            rounds=(start_round, research_round),
            reason="round_not_on_interval",
            job=job,
        )
    start_round = max(1, research_round - resolved_interval + 1)
    return run_manual_recursive_improve_reports(
        root_path,
        start_round=start_round,
        end_round=research_round,
        job=job,
        agents=agents,
        execute=execute,
    )


def run_manual_recursive_improve_reports(
    root: str | Path,
    *,
    start_round: int,
    end_round: int,
    job: int | None = None,
    agents: Sequence[str] = DEFAULT_AGENTS,
    execute: bool = True,
) -> RecursiveImproveReportResult:
    """Prepare and optionally execute reports for any explicit round range."""
    prepared = prepare_recursive_improve_report_batch(
        root,
        start_round=start_round,
        end_round=end_round,
        job=job,
        agents=agents,
    )
    if prepared.status != "prepared" or not execute:
        return prepared
    return _execute_report_batch(prepared)


def prepare_recursive_improve_report_batch(
    root: str | Path,
    *,
    start_round: int,
    end_round: int,
    job: int | None,
    agents: Sequence[str] = DEFAULT_AGENTS,
) -> RecursiveImproveReportResult:
    if start_round <= 0 or end_round < start_round:
        raise ValueError("start_round must be positive and <= end_round")
    root_path = Path(root)
    output_dir = _default_output_dir(root_path, job, start_round, end_round)
    traces = _find_recursive_improve_traces(root_path, start_round, end_round, job=job)
    if not traces:
        return RecursiveImproveReportResult(
            status="skipped",
            output_dir=output_dir,
            rounds=(start_round, end_round),
            job=job,
            agents=tuple(agents),
            reason="no_traces",
        )
    for agent in agents:
        _write_agent_trace_slice(output_dir, agent, traces)
    _write_manifest(output_dir, start_round, end_round, job, agents, traces)
    return RecursiveImproveReportResult(
        status="prepared",
        output_dir=output_dir,
        rounds=(start_round, end_round),
        job=job,
        agents=tuple(agents),
    )


def _execute_report_batch(
    prepared: RecursiveImproveReportResult,
) -> RecursiveImproveReportResult:
    failures: list[str] = []
    for agent in prepared.agents:
        agent_dir = prepared.output_dir / agent
        traces_dir = agent_dir / "eval" / "traces"
        if not traces_dir.exists() or not any(traces_dir.glob("*.json")):
            continue
        cmd = _build_report_command(agent=agent, traces_dir=traces_dir)
        try:
            completed = subprocess.run(
                cmd,
                cwd=agent_dir,
                text=True,
                capture_output=True,
                timeout=_int_env(ENV_RECURSIVE_IMPROVE_TIMEOUT_SECONDS, DEFAULT_TIMEOUT_SECONDS),
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            failures.append(f"{agent}: {type(exc).__name__}: {exc}")
            continue
        _write_command_log(agent_dir, cmd, completed)
        if completed.returncode != 0:
            failures.append(f"{agent}: exit={completed.returncode}")
    if failures:
        _write_text(
            prepared.output_dir / "recursive-improve-errors.txt",
            "\n".join(failures) + "\n",
        )
        return RecursiveImproveReportResult(
            status="failed",
            output_dir=prepared.output_dir,
            rounds=prepared.rounds,
            job=prepared.job,
            agents=prepared.agents,
            reason="; ".join(failures),
            launched=True,
        )
    return RecursiveImproveReportResult(
        status="completed",
        output_dir=prepared.output_dir,
        rounds=prepared.rounds,
        job=prepared.job,
        agents=prepared.agents,
        launched=True,
    )


def _build_report_command(*, agent: str, traces_dir: Path) -> list[str]:
    prompt = _report_prompt(agent=agent, traces_dir=traces_dir)
    configured = os.environ.get(ENV_RECURSIVE_IMPROVE_COMMAND, "").strip()
    if configured:
        parts = shlex.split(configured)
        if "{prompt}" in parts:
            return [prompt if part == "{prompt}" else part for part in parts]
        return [*parts, prompt]
    model = os.environ.get(ENV_RECURSIVE_IMPROVE_MODEL, DEFAULT_AGENT_MODEL).strip()
    return [
        "codex",
        "exec",
        "--model",
        model,
        "--full-auto",
        "--skip-git-repo-check",
        prompt,
    ]


def _report_prompt(*, agent: str, traces_dir: Path) -> str:
    return (
        "Use the recursive-improve skill in report-only mode. "
        f"Analyze only the {agent} traces under {traces_dir}. "
        "Do not edit production code, do not commit, and do not apply changes. "
        "Write outputs under eval/: stage0_trace_analysis.md, "
        "stage1_insights_summary.md, stage2_domain_context.md, "
        "stage3_metrics_and_baselines.md, stage4_rubric.md, "
        "stage5_prioritized_improvement_plan.md, stage6_review_summary.md, "
        "and recursive_improve_full_report.md. Focus on actionable reliability "
        "or observability improvements that do not reduce research creativity."
    )


def _find_recursive_improve_traces(
    root: Path,
    start_round: int,
    end_round: int,
    *,
    job: int | None,
) -> list[Path]:
    traces: list[Path] = []
    for round_id in range(start_round, end_round + 1):
        if job is None:
            raise ValueError("job id is required for recursive-improve trace discovery")
        trace_root = research_round_trace_exports_root(root, job, round_id)
        traces.extend(
            sorted(
                trace_root.glob(
                    f"round-{round_id:03d}-*/recursive_improve/recursive-improve-trace.json"
                )
            )
        )
    return _dedupe_paths(traces)


def _write_agent_trace_slice(output_dir: Path, agent: str, trace_paths: Iterable[Path]) -> None:
    traces_dir = output_dir / agent / "eval" / "traces"
    traces_dir.mkdir(parents=True, exist_ok=True)
    for trace_path in trace_paths:
        trace = json.loads(trace_path.read_text(encoding="utf-8"))
        sliced = _slice_trace_for_agent(trace, agent)
        if not sliced.get("messages"):
            continue
        target = traces_dir / _safe_trace_name(trace_path)
        _write_text(target, json.dumps(sliced, indent=2, sort_keys=True) + "\n")


def _slice_trace_for_agent(trace: dict[str, Any], agent: str) -> dict[str, Any]:
    messages = trace.get("messages") if isinstance(trace.get("messages"), list) else []
    scoped = [
        message
        for message in messages
        if isinstance(message, dict) and _agent_matches(str(message.get("agent") or ""), agent)
    ]
    sliced = dict(trace)
    sliced["messages"] = scoped
    metadata = dict(trace.get("metadata") or {})
    metadata["agent_slice"] = agent
    metadata["source_message_count"] = len(messages)
    metadata["agent_message_count"] = len(scoped)
    sliced["metadata"] = metadata
    return sliced


def _agent_matches(value: str, wanted: str) -> bool:
    normalized = _normalized_agent(value)
    wanted_normalized = _normalized_agent(wanted)
    aliases = AGENT_ALIASES.get(wanted_normalized, {wanted_normalized})
    return normalized in aliases


def _normalized_agent(agent: str) -> str:
    return agent.strip().lower().replace("_", "-")


def _write_manifest(
    output_dir: Path,
    start_round: int,
    end_round: int,
    job: int | None,
    agents: Sequence[str],
    traces: Sequence[Path],
) -> None:
    manifest = {
        "mode": "report_only",
        "job": job,
        "rounds": {"start": start_round, "end": end_round},
        "agents": list(agents),
        "source_traces": [str(path) for path in traces],
    }
    _write_text(output_dir / "manifest.json", json.dumps(manifest, indent=2, sort_keys=True) + "\n")


def _write_command_log(agent_dir: Path, cmd: Sequence[str], completed: Any) -> None:
    payload = {
        "cmd": list(cmd),
        "returncode": int(getattr(completed, "returncode", -1)),
        "stdout": str(getattr(completed, "stdout", "")),
        "stderr": str(getattr(completed, "stderr", "")),
    }
    _write_text(
        agent_dir / "eval" / "recursive-improve-command.json",
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
    )


def _safe_trace_name(path: Path) -> str:
    round_dir = path.parents[1].name
    return f"{round_dir}.json"


def _default_output_dir(root: Path, job: int | None, start_round: int, end_round: int) -> Path:
    job_part = f"job-{job}" if job is not None else "job-unknown"
    return (
        root
        / "improvement_reports"
        / "recursive_improve"
        / job_part
        / f"rounds-{start_round:03d}-{end_round:03d}"
    )


def _write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _dedupe_paths(paths: Iterable[Path]) -> list[Path]:
    seen: set[Path] = set()
    out: list[Path] = []
    for path in paths:
        resolved = path.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        out.append(path)
    return out


def _int_env(name: str, default: int) -> int:
    raw = os.environ.get(name, "")
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Generate Recursive Improve reports")
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--job", type=int, default=None)
    parser.add_argument("--start-round", type=int, required=True)
    parser.add_argument("--end-round", type=int, required=True)
    parser.add_argument("--agents", nargs="+", default=list(DEFAULT_AGENTS))
    parser.add_argument("--prepare-only", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    result = run_manual_recursive_improve_reports(
        args.root,
        start_round=args.start_round,
        end_round=args.end_round,
        job=args.job,
        agents=args.agents,
        execute=not args.prepare_only,
    )
    print(
        json.dumps(
            {
                "status": result.status,
                "output_dir": str(result.output_dir),
                "rounds": result.rounds,
                "reason": result.reason,
            },
            sort_keys=True,
        )
    )
    return 0 if result.status in {"prepared", "completed", "skipped"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
