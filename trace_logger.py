"""Centralized trace logger for autoresearch pipeline.

Logs every agent prompt/response, tool call, SSH command, benchmark result,
and controller decision with timestamps to a dedicated log file.

IDs:
    RUN_ID      — unique per autoresearch session (e.g. "R-20260426-183012")
    HYPOTHESIS  — unique per hypothesis tested within a run (e.g. "H001", "H002")

Usage:
    from trace_logger import trace, trace_agent_prompt, trace_agent_response, trace_ssh
    from trace_logger import get_run_id, begin_hypothesis, current_hypothesis_id
    trace("LOOP", "Starting autoresearch loop")
    hid = begin_hypothesis("baseline")  # -> "H001"
    trace_agent_prompt("diagnostic-analyst", prompt_text)
    trace_agent_response("diagnostic-analyst", response_text, parsed_json)
    trace_ssh("backtest/runner.py --strategy name --config x.json", exit_code, stdout, stderr)
"""
from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path

# Log file lives next to the running script
_LOG_DIR = Path(__file__).resolve().parent / "logs"
_LOG_DIR.mkdir(exist_ok=True)

_SESSION_ID = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
_FAMILY: str = ""
_RUN_ID = f"R-{_SESSION_ID}"
_LOG_FILE = _LOG_DIR / f"trace-{_RUN_ID}.log"
_AGENT_LOG_DIR = _LOG_DIR / f"agents-{_RUN_ID}"
_AGENT_LOG_DIR.mkdir(exist_ok=True)

# Counter for sequential ordering
_seq = 0

# Hypothesis tracking — unique sub-ID per hypothesis within a run
_hypothesis_counter = 0
_current_hypothesis_id: str | None = None
_current_hypothesis_name: str | None = None


def _ts() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3] + "Z"


def _next_seq() -> int:
    global _seq
    _seq += 1
    return _seq


def begin_hypothesis(name: str) -> str:
    """Start tracking a new hypothesis. Returns the hypothesis ID (e.g. 'H001')."""
    global _hypothesis_counter, _current_hypothesis_id, _current_hypothesis_name
    _hypothesis_counter += 1
    _current_hypothesis_id = f"H{_hypothesis_counter:03d}"
    _current_hypothesis_name = name

    # Create a per-hypothesis log directory
    hyp_dir = _AGENT_LOG_DIR / _current_hypothesis_id
    hyp_dir.mkdir(exist_ok=True)

    trace("HYPOTHESIS", f"BEGIN {_current_hypothesis_id} name={name}")
    return _current_hypothesis_id


def begin_round(round_number: int) -> None:
    """Rotate to a new trace file for this research round.

    File naming: trace-{family}-job-{J}-round-{N}-{timestamp}.log
    """
    global _LOG_FILE, _AGENT_LOG_DIR, _RUN_ID, _seq, _hypothesis_counter
    _seq = 0
    _hypothesis_counter = 0
    prefix = f"{_FAMILY}-" if _FAMILY else ""
    job_tag = f"job-{_JOB}-" if _JOB else ""
    ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    _RUN_ID = f"R-{prefix}{job_tag}round-{round_number}-{ts}"
    _LOG_FILE = _LOG_DIR / f"trace-{_RUN_ID}.log"
    _AGENT_LOG_DIR = _LOG_DIR / f"agents-{_RUN_ID}"
    _AGENT_LOG_DIR.mkdir(exist_ok=True)


def end_hypothesis(decision: str = "", metric: float | None = None) -> None:
    """Mark the current hypothesis as complete."""
    trace("HYPOTHESIS", f"END {_current_hypothesis_id} name={_current_hypothesis_name} decision={decision} metric={metric}")


def current_hypothesis_id() -> str | None:
    """Return the active hypothesis ID, or None."""
    return _current_hypothesis_id


def current_hypothesis_name() -> str | None:
    """Return the active hypothesis name, or None."""
    return _current_hypothesis_name


def get_run_id() -> str:
    """Return the unique run ID for this autoresearch session."""
    return _RUN_ID


def _hyp_tag() -> str:
    """Return '[H001]' tag if a hypothesis is active, else ''."""
    return f"[{_current_hypothesis_id}]" if _current_hypothesis_id else ""


def _hyp_dir() -> Path:
    """Return per-hypothesis log dir if active, else the session agent dir."""
    if _current_hypothesis_id:
        d = _AGENT_LOG_DIR / _current_hypothesis_id
        d.mkdir(exist_ok=True)
        return d
    return _AGENT_LOG_DIR


def trace(component: str, message: str, data: dict | None = None) -> None:
    """Write a trace line to the main log file."""
    seq = _next_seq()
    htag = _hyp_tag()
    line = f"[{_ts()}] [{_RUN_ID}]{htag} [{seq:05d}] [{component}] {message}"
    if data:
        line += f" | {json.dumps(data, default=str)}"
    line += "\n"
    with open(_LOG_FILE, "a") as f:
        f.write(line)
    # Also print to stdout for real-time visibility
    print(f"TRACE {_RUN_ID}{htag} [{component}] {message}")


def trace_agent_prompt(agent_name: str, prompt: str, system_prompt: str = "") -> str:
    """Log the full prompt sent to an agent. Returns a trace_id for correlation."""
    seq = _next_seq()
    htag = _hyp_tag()
    hid = _current_hypothesis_id or "global"
    trace_id = f"{hid}-{agent_name}-{seq:05d}"

    # Write full prompt to per-hypothesis dir
    prompt_file = _hyp_dir() / f"{trace_id}-prompt.txt"
    with open(prompt_file, "w") as f:
        f.write(f"=== RUN: {_RUN_ID} ===\n")
        f.write(f"=== HYPOTHESIS: {_current_hypothesis_id or 'none'} ({_current_hypothesis_name or 'none'}) ===\n")
        f.write(f"=== AGENT: {agent_name} ===\n")
        f.write(f"=== TIMESTAMP: {_ts()} ===\n")
        f.write(f"=== TRACE_ID: {trace_id} ===\n\n")
        if system_prompt:
            f.write(f"--- SYSTEM PROMPT ---\n{system_prompt}\n\n")
        f.write(f"--- USER PROMPT ---\n{prompt}\n")

    # Summary in main log
    prompt_preview = prompt[:200].replace("\n", " ")
    with open(_LOG_FILE, "a") as f:
        f.write(
            f"[{_ts()}] [{_RUN_ID}]{htag} [{seq:05d}] [AGENT->{agent_name}] "
            f"PROMPT sent (len={len(prompt)}) | preview: {prompt_preview}...\n"
        )
    print(f"TRACE {_RUN_ID}{htag} [AGENT->{agent_name}] PROMPT sent (len={len(prompt)})")
    return trace_id


def trace_agent_response(
    agent_name: str, trace_id: str, raw_text: str, parsed: dict | None = None
) -> None:
    """Log the full response from an agent."""
    seq = _next_seq()
    htag = _hyp_tag()

    # Write full response to per-hypothesis dir
    response_file = _hyp_dir() / f"{trace_id}-response.txt"
    with open(response_file, "w") as f:
        f.write(f"=== RUN: {_RUN_ID} ===\n")
        f.write(f"=== HYPOTHESIS: {_current_hypothesis_id or 'none'} ({_current_hypothesis_name or 'none'}) ===\n")
        f.write(f"=== AGENT: {agent_name} ===\n")
        f.write(f"=== TIMESTAMP: {_ts()} ===\n")
        f.write(f"=== TRACE_ID: {trace_id} ===\n\n")
        f.write(f"--- RAW RESPONSE ---\n{raw_text}\n")
        if parsed:
            f.write(f"\n--- PARSED JSON ---\n{json.dumps(parsed, indent=2, default=str)}\n")

    # Summary in main log
    status = "PARSED_OK" if parsed else "PARSE_FAILED"
    preview = raw_text[:200].replace("\n", " ")
    with open(_LOG_FILE, "a") as f:
        f.write(
            f"[{_ts()}] [{_RUN_ID}]{htag} [{seq:05d}] [AGENT<-{agent_name}] "
            f"RESPONSE {status} (len={len(raw_text)}) | preview: {preview}...\n"
        )
    print(f"TRACE {_RUN_ID}{htag} [AGENT<-{agent_name}] RESPONSE {status} (len={len(raw_text)})")


def trace_agent_tool_call(agent_name: str, trace_id: str, tool_name: str, tool_input: str = "") -> None:
    """Log a tool call made by an agent."""
    seq = _next_seq()
    htag = _hyp_tag()
    input_preview = tool_input[:300].replace("\n", " ") if tool_input else ""
    with open(_LOG_FILE, "a") as f:
        f.write(
            f"[{_ts()}] [{_RUN_ID}]{htag} [{seq:05d}] [AGENT.TOOL {agent_name}] "
            f"{tool_name} | {input_preview}\n"
        )


def trace_ssh(command: str, exit_code: int, stdout: str = "", stderr: str = "") -> None:
    """Log an SSH/VPS command execution."""
    seq = _next_seq()
    htag = _hyp_tag()
    stdout_preview = stdout[:500].replace("\n", " | ") if stdout else ""
    stderr_preview = stderr[:300].replace("\n", " | ") if stderr else ""

    # Write full output to per-hypothesis dir
    ssh_file = _hyp_dir() / f"ssh-{seq:05d}.txt"
    with open(ssh_file, "w") as f:
        f.write(f"=== RUN: {_RUN_ID} ===\n")
        f.write(f"=== HYPOTHESIS: {_current_hypothesis_id or 'none'} ({_current_hypothesis_name or 'none'}) ===\n")
        f.write(f"=== SSH COMMAND ===\n{command}\n")
        f.write(f"=== TIMESTAMP: {_ts()} ===\n")
        f.write(f"=== EXIT CODE: {exit_code} ===\n\n")
        f.write(f"--- STDOUT ---\n{stdout}\n")
        if stderr:
            f.write(f"\n--- STDERR ---\n{stderr}\n")

    with open(_LOG_FILE, "a") as f:
        f.write(
            f"[{_ts()}] [{_RUN_ID}]{htag} [{seq:05d}] [SSH] cmd='{command}' exit={exit_code} "
            f"| stdout: {stdout_preview}\n"
        )
        if stderr_preview:
            f.write(f"[{_ts()}] [{_RUN_ID}]{htag} [{seq:05d}] [SSH.ERR] {stderr_preview}\n")
    print(f"TRACE {_RUN_ID}{htag} [SSH] exit={exit_code} cmd='{command[:80]}'")


def trace_benchmark(config: str, metric: float | None, decision: str, details: dict | None = None) -> None:
    """Log a benchmark result."""
    seq = _next_seq()
    htag = _hyp_tag()
    with open(_LOG_FILE, "a") as f:
        f.write(
            f"[{_ts()}] [{_RUN_ID}]{htag} [{seq:05d}] [BENCHMARK] config={config} metric={metric} "
            f"decision={decision}\n"
        )
        if details:
            f.write(f"[{_ts()}] [{_RUN_ID}]{htag} [{seq:05d}] [BENCHMARK.DETAIL] {json.dumps(details, default=str)}\n")
    print(f"TRACE {_RUN_ID}{htag} [BENCHMARK] {config} -> metric={metric} decision={decision}")


def trace_state_change(old_state: str, new_state: str, reason: str = "") -> None:
    """Log a controller state transition."""
    seq = _next_seq()
    htag = _hyp_tag()
    with open(_LOG_FILE, "a") as f:
        f.write(
            f"[{_ts()}] [{_RUN_ID}]{htag} [{seq:05d}] [STATE] {old_state} -> {new_state}"
            + (f" | reason: {reason}" if reason else "")
            + "\n"
        )
    print(f"TRACE {_RUN_ID}{htag} [STATE] {old_state} -> {new_state}" + (f" ({reason})" if reason else ""))


_JOB: int = 0


def set_family(family: str, job: int = 0) -> None:
    """Set the strategy family and job number. Log files will be per-family per-round."""
    global _FAMILY, _JOB
    _FAMILY = family
    _JOB = job


def get_log_file() -> Path:
    """Return the path to the current session's trace log."""
    return _LOG_FILE


def get_session_id() -> str:
    """Return the current session ID."""
    return _SESSION_ID
