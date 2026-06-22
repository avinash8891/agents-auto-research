from __future__ import annotations

import hashlib
import json
import logging as _logging
import sys
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterator

from openinference.instrumentation import using_attributes
from opentelemetry import trace as otel_trace
from opentelemetry.context.context import Context
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SpanExporter

from persistence_utils import write_text_atomic
from trace_engine import (
    _TRACELOOP_INSTRUMENTS,
    DEFAULT_EXPORTER_NAME,
    JsonLineTraceExporter,
    TraceEngine,
    TraceRuntimeState,
    _canonical_ts,
    _ExporterRegistry,
    _render_artifact_header,
    _render_ssh_artifact,
)

# Traceloop SDK is the OpenLLMetry layer used for model/workflow instrumentation.

_LOG_DIR = Path(__file__).resolve().parent / "logs"
_LOG_DIR.mkdir(exist_ok=True)

_SESSION_ID = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")

_log = _logging.getLogger(__name__)

_EXPORTER_REGISTRY = _ExporterRegistry()
_EXPORTER_REGISTRY.register(
    DEFAULT_EXPORTER_NAME,
    lambda: JsonLineTraceExporter(_STATE.canonical_event_file),
)

_ENGINE = TraceEngine(
    log_dir=_LOG_DIR,
    session_id=_SESSION_ID,
    registry=_EXPORTER_REGISTRY,
    instruments=_TRACELOOP_INSTRUMENTS,
    provider_builder=lambda: _build_provider(),
    initializer=lambda: _initialize_tracing(),
)
_STATE = TraceRuntimeState(_ENGINE)


def _write_text(path: Path, content: str) -> str:
    try:
        write_text_atomic(path, content)
    except OSError as exc:
        _log.debug("trace artifact write failed (suppressed): %s", exc)
        return ""
    return str(path)


def _render_prompt_artifact(
    *,
    run_id: str,
    hypothesis_id: str | None,
    hypothesis_name: str | None,
    agent_name: str,
    timestamp: str,
    trace_id: str,
    prompt: str,
    system_prompt: str,
) -> str:
    lines = _render_artifact_header(
        run_id=run_id,
        hypothesis_id=hypothesis_id,
        hypothesis_name=hypothesis_name,
        timestamp=timestamp,
        agent_name=agent_name,
        trace_id=trace_id,
    ) + [
        "",
        "--- SYSTEM PROMPT ---",
        system_prompt,
        "",
        "--- USER PROMPT ---",
        prompt,
        "",
    ]
    return "\n".join(lines)


def _render_response_artifact(
    *,
    run_id: str,
    hypothesis_id: str | None,
    hypothesis_name: str | None,
    agent_name: str,
    timestamp: str,
    trace_id: str,
    raw_text: str,
    parsed: dict[str, Any] | None,
) -> str:
    lines = _render_artifact_header(
        run_id=run_id,
        hypothesis_id=hypothesis_id,
        hypothesis_name=hypothesis_name,
        timestamp=timestamp,
        agent_name=agent_name,
        trace_id=trace_id,
    ) + [
        "",
        "--- RAW RESPONSE ---",
        raw_text,
        "",
        "--- PARSED JSON ---",
        json.dumps(parsed, indent=2, sort_keys=True) if parsed is not None else "",
        "",
    ]
    return "\n".join(lines)


def _log_line(component: str, message: str, data: dict[str, Any] | None, seq: int) -> None:
    htag = _hyp_tag()
    timestamp = _canonical_ts()
    line = f"[{timestamp}] [{_STATE.run_id}]{htag} [{seq:05d}] [{component}] {message}"
    if data:
        line += f" | {json.dumps(data, default=str)}"
    line += "\n"
    try:
        handle = _STATE.get_log_handle()
        handle.write(line)
        handle.flush()
    except OSError as exc:
        _log.debug("trace log write failed (suppressed): %s", exc)
    _safe_console_write(f"TRACE {_STATE.run_id}{htag} [{component}] {message}\n")


def _safe_console_write(line: str) -> None:
    try:
        sys.stdout.write(line)
        sys.stdout.flush()
    except BrokenPipeError:
        return
    except ValueError as exc:
        if "closed file" in str(exc).lower():
            return
        raise
    except OSError as exc:
        if getattr(exc, "errno", None) == 32:
            return
        raise


def _build_resource() -> Resource:
    return _ENGINE.build_resource()


def _build_provider() -> TracerProvider:
    # Thin module-level seam over the engine's provider-build logic. Kept as a
    # standalone function because existing tests monkeypatch ``_build_provider``
    # to assert init/reset call-count semantics (transaction-mode never builds).
    return _ENGINE.build_provider()


def _initialize_tracing() -> None:
    _ENGINE.initialize()


_initialize_tracing()


def register_exporter(
    name: str, exporter_or_factory: SpanExporter | Callable[[], SpanExporter]
) -> None:
    """Register an alternate span exporter under ``name``.

    The value may be a ready ``SpanExporter`` instance or a zero-arg factory the
    registry calls on selection. Registered exporters are selectable via
    ``select_exporter(name)`` WITHOUT editing ``_initialize_tracing``. The
    default ``JsonLineTraceExporter`` ships registered under
    ``DEFAULT_EXPORTER_NAME``.
    """
    _ENGINE.register_exporter(name, exporter_or_factory)


def select_exporter(name: str) -> None:
    """Select a registered exporter by name and rebuild the provider around it."""
    _ENGINE.select_exporter(name)


def registered_exporters() -> list[str]:
    """Return the sorted list of registered exporter names."""
    return _EXPORTER_REGISTRY.names()


def configure_exporter(exporter: SpanExporter) -> None:
    """Swap the active span exporter and rebuild the provider around it.

    This is the public seam for tests and alternate backends to inject a
    ``SpanExporter`` without monkeypatching module internals. The new exporter
    takes effect immediately for spans started through ``_tracer()``. Routes
    through the engine's exporter registry seam.
    """
    _ENGINE.configure_exporter(exporter)


def reset_tracing(*, exporter: SpanExporter | None = None) -> None:
    """Reset the init-once lifecycle, optionally swapping the exporter first.

    Clears the ``initialized`` flag and provider so the next
    ``_initialize_tracing()`` re-runs from scratch with the same env-driven
    semantics (transaction-mode skip, tracing-disabled, ``Traceloop.init``).
    """
    _ENGINE.reset(exporter=exporter)


def _reset_provider_for_current_state() -> None:
    _ENGINE.reset_provider_for_current_state()


def _tracer():
    return _ENGINE.tracer()


def _parent_context_for_event(
    *, category: str, action: str, payload: dict[str, Any]
) -> Context | None:
    trace_id = str(payload.get("trace_id") or "")
    if action in {"tool_call", "tool_result", "response"} and trace_id:
        agent_context = _STATE.agent_contexts.get(trace_id)
        if agent_context is not None:
            return agent_context
    if _STATE.current_hypothesis_context is not None:
        return _STATE.current_hypothesis_context
    return _STATE.round_context


def _remember_event_context(
    *,
    category: str,
    action: str,
    payload: dict[str, Any],
    span_context: Context,
) -> None:
    if _STATE.round_context is None:
        _STATE.round_context = span_context
    if _STATE.current_hypothesis_id and _STATE.current_hypothesis_context is None:
        _STATE.current_hypothesis_context = span_context
    trace_id = str(payload.get("trace_id") or "")
    if action == "prompt" and trace_id:
        _STATE.agent_contexts[trace_id] = span_context


@contextmanager
def _event_span(
    *,
    span_name: str,
    source_module: str,
    category: str,
    action: str,
    summary: str,
    payload: dict[str, Any] | None = None,
    artifact_paths: list[str] | None = None,
    model_provider: str = "",
    model_name: str = "",
) -> Iterator[None]:
    payload = payload or {}
    artifact_paths = list(artifact_paths or [])
    event_id = _STATE.next_event_id()
    seq = _STATE.seq
    timestamp = _canonical_ts()
    parent_context = _parent_context_for_event(category=category, action=action, payload=payload)
    with using_attributes(
        session_id=_STATE.session_id,
        metadata={
            "run_id": _STATE.run_id,
            "family": _STATE.family,
            "job": _STATE.job,
            "model_provider": model_provider,
            "model_name": model_name,
            "hypothesis_id": _STATE.current_hypothesis_id or "",
            "hypothesis_name": _STATE.current_hypothesis_name or "",
            "category": category,
            "action": action,
        },
    ):
        with _tracer().start_as_current_span(span_name, context=parent_context) as span:
            span.set_attribute("autoresearch.event_id", event_id)
            span.set_attribute("autoresearch.schema_version", 1)
            span.set_attribute("autoresearch.timestamp", timestamp)
            span.set_attribute("autoresearch.source_module", source_module)
            span.set_attribute("autoresearch.run_id", _STATE.run_id)
            span.set_attribute("autoresearch.session_id", _STATE.session_id)
            span.set_attribute("autoresearch.family", _STATE.family)
            span.set_attribute("autoresearch.job", _STATE.job)
            span.set_attribute("autoresearch.model_provider", model_provider)
            span.set_attribute("autoresearch.model_name", model_name)
            span.set_attribute("autoresearch.hypothesis_id", _STATE.current_hypothesis_id or "")
            span.set_attribute("autoresearch.hypothesis_name", _STATE.current_hypothesis_name or "")
            span.set_attribute("autoresearch.seq", seq)
            span.set_attribute("autoresearch.category", category)
            span.set_attribute("autoresearch.action", action)
            span.set_attribute("autoresearch.summary", summary)
            span.set_attribute("autoresearch.payload_json", json.dumps(payload, default=str))
            if artifact_paths:
                span.set_attribute("autoresearch.artifact_paths", artifact_paths)
            _remember_event_context(
                category=category,
                action=action,
                payload=payload,
                span_context=otel_trace.set_span_in_context(span),
            )
            yield


def begin_hypothesis(name: str) -> str:
    hypothesis_id = _STATE.begin_hypothesis(name)
    trace("HYPOTHESIS", f"BEGIN {hypothesis_id} name={name}")
    # advance seq so the lifecycle event has a distinct ordinal from the
    # preceding trace() call; _event_span reads _STATE.seq passively
    _STATE.next_seq()
    _record_event(
        source_module="trace_sdk",
        category="lifecycle",
        action="hypothesis",
        summary=f"BEGIN {hypothesis_id} name={name}",
        payload={"hypothesis_id": hypothesis_id, "hypothesis_name": name, "status": "begin"},
    )
    return hypothesis_id


def begin_round(round_number: int) -> None:
    # Engine installs a fresh immutable RoundContext (new identity, seq reset)
    # and rebuilds the provider around the round's fresh exporter.
    _ENGINE.begin_round(round_number)


def end_hypothesis(decision: str = "", metric: float | None = None) -> None:
    ended_id = _STATE.current_hypothesis_id
    ended_name = _STATE.current_hypothesis_name
    trace(
        "HYPOTHESIS",
        f"END {ended_id} name={ended_name} decision={decision} metric={metric}",
    )
    # Swap a fresh empty HypothesisContext wholesale — clears id/name/context and
    # the per-agent context cache in one immutable-identity replacement.
    _ENGINE.end_hypothesis()


def current_hypothesis_id() -> str | None:
    return _STATE.current_hypothesis_id


def get_run_id() -> str:
    return _STATE.run_id


def _hyp_tag() -> str:
    return f"[{_STATE.current_hypothesis_id}]" if _STATE.current_hypothesis_id else ""


def _record_event(
    *,
    source_module: str,
    category: str,
    action: str,
    summary: str,
    payload: dict[str, Any] | None = None,
    artifact_paths: list[str] | None = None,
    model_provider: str = "",
    model_name: str = "",
) -> None:
    with _event_span(
        span_name=f"{category}.{action}",
        source_module=source_module,
        category=category,
        action=action,
        summary=summary,
        payload=payload,
        artifact_paths=artifact_paths,
        model_provider=model_provider,
        model_name=model_name,
    ):
        pass


def trace(
    component: str,
    message: str,
    data: dict | None = None,
    *,
    model_provider: str = "",
    model_name: str = "",
) -> None:
    seq = _STATE.next_seq()
    _log_line(component, message, data, seq)
    _record_event(
        source_module="trace_sdk",
        category="trace",
        action=component.lower(),
        summary=message,
        payload={"component": component, "data": data or {}},
        model_provider=model_provider,
        model_name=model_name,
    )


def record_usage_event(
    agent: str,
    *,
    model_provider: str = "",
    model_name: str = "",
    input_tokens: int = 0,
    output_tokens: int = 0,
    total_tokens: int = 0,
    cost_usd: float = 0.0,
    dedupe_key: str | None = None,
    cached_input_tokens: int = 0,
    reasoning_output_tokens: int = 0,
    estimated_input_tokens: int = 0,
    estimated_output_tokens: int = 0,
    estimated_total_tokens: int = 0,
    usage_source: str = "",
    trace_id: str = "",
    thesis_id: str = "",
) -> None:
    """Emit a per-call token-usage trace event into trace-events.jsonl.

    Reuses run_id / job / hypothesis_id correlation fields from the trace runtime.
    Fail-open: any exception during emission must not block the caller.
    """
    try:
        _STATE.next_seq()  # advance seq; _event_span reads _STATE.seq passively (no second increment)
        _record_event(
            source_module="agent_token_usage",
            category="usage",
            action="accumulate",
            summary=f"USAGE {agent} in={input_tokens} out={output_tokens} cost={cost_usd:.6f}",
            payload={
                "agent": agent,
                "input_tokens": int(input_tokens or 0),
                "cached_input_tokens": int(cached_input_tokens or 0),
                "output_tokens": int(output_tokens or 0),
                "reasoning_output_tokens": int(reasoning_output_tokens or 0),
                "total_tokens": int(total_tokens or 0),
                "estimated_input_tokens": int(estimated_input_tokens or 0),
                "estimated_output_tokens": int(estimated_output_tokens or 0),
                "estimated_total_tokens": int(estimated_total_tokens or 0),
                "cost_usd": float(cost_usd or 0.0),
                "dedupe_key": dedupe_key,
                "usage_source": usage_source,
                "trace_id": trace_id,
                "thesis_id": thesis_id,
            },
            model_provider=model_provider or "",
            model_name=model_name or "",
        )
    except Exception as exc:
        # observability never blocks business logic
        _log.debug("record_usage_event failed (suppressed): %s", exc)


def trace_agent_prompt(
    agent_name: str,
    prompt: str,
    system_prompt: str = "",
    *,
    model_provider: str = "",
    model_name: str = "",
) -> str:
    seq = _STATE.next_seq()
    hid = _STATE.current_hypothesis_id or "global"
    trace_id = f"{hid}-{agent_name}-{seq:05d}"
    timestamp = _canonical_ts()
    prompt_file = _STATE.hypothesis_dir / f"{trace_id}-prompt.txt"
    prompt_path = _write_text(
        prompt_file,
        _render_prompt_artifact(
            run_id=_STATE.run_id,
            hypothesis_id=_STATE.current_hypothesis_id,
            hypothesis_name=_STATE.current_hypothesis_name,
            agent_name=agent_name,
            timestamp=timestamp,
            trace_id=trace_id,
            prompt=prompt,
            system_prompt=system_prompt,
        ),
    )
    _log_line(
        f"AGENT->{agent_name}",
        f"PROMPT sent (len={len(prompt)}) artifact={prompt_path}",
        None,
        seq,
    )
    _record_event(
        source_module="trace_sdk",
        category="agent",
        action="prompt",
        summary=f"PROMPT sent to {agent_name}",
        payload={
            "agent_name": agent_name,
            "trace_id": trace_id,
            "prompt_length": len(prompt),
            "system_prompt_length": len(system_prompt),
            "preview_len": min(len(prompt), 200),
        },
        artifact_paths=[prompt_path],
        model_provider=model_provider,
        model_name=model_name,
    )
    return trace_id


def trace_agent_response(
    agent_name: str,
    trace_id: str,
    raw_text: str,
    parsed: dict | None = None,
    *,
    model_provider: str = "",
    model_name: str = "",
) -> None:
    seq = _STATE.next_seq()
    timestamp = _canonical_ts()
    response_file = _STATE.hypothesis_dir / f"{trace_id}-response.txt"
    response_path = _write_text(
        response_file,
        _render_response_artifact(
            run_id=_STATE.run_id,
            hypothesis_id=_STATE.current_hypothesis_id,
            hypothesis_name=_STATE.current_hypothesis_name,
            agent_name=agent_name,
            timestamp=timestamp,
            trace_id=trace_id,
            raw_text=raw_text,
            parsed=parsed,
        ),
    )
    status = "PARSED_OK" if parsed else "PARSE_FAILED"
    _log_line(
        f"AGENT<-{agent_name}",
        f"RESPONSE {status} (len={len(raw_text)})",
        None,
        seq,
    )
    _record_event(
        source_module="trace_sdk",
        category="agent",
        action="response",
        summary=f"RESPONSE {status} from {agent_name}",
        payload={
            "agent_name": agent_name,
            "trace_id": trace_id,
            "status": status,
            "response_length": len(raw_text),
            "preview_len": min(len(raw_text), 200),
            "parsed_keys": sorted(parsed.keys()) if parsed else [],
        },
        artifact_paths=[response_path],
        model_provider=model_provider,
        model_name=model_name,
    )


def trace_agent_tool_call(
    agent_name: str,
    trace_id: str,
    tool_name: str,
    tool_input: str = "",
    *,
    model_provider: str = "",
    model_name: str = "",
) -> None:
    seq = _STATE.next_seq()
    input_preview = tool_input[:300].replace("\n", " ") if tool_input else ""
    _log_line(f"AGENT.TOOL {agent_name}", f"{tool_name} | {input_preview}", None, seq)
    _record_event(
        source_module="trace_sdk",
        category="agent",
        action="tool_call",
        summary=f"{agent_name} called {tool_name}",
        payload={
            "agent_name": agent_name,
            "trace_id": trace_id,
            "tool_name": tool_name,
            "tool_input_preview": input_preview,
            "tool_input_length": len(tool_input or ""),
            "tool_input_hash": _short_hash(tool_input or ""),
        },
        model_provider=model_provider,
        model_name=model_name,
    )


def _short_hash(value: str) -> str:
    if not value:
        return ""
    return hashlib.sha256(value.encode("utf-8", errors="replace")).hexdigest()[:16]


def trace_agent_tool_result(
    agent_name: str,
    trace_id: str,
    tool_name: str,
    tool_output: str = "",
    *,
    status: str = "ok",
    error_type: str = "",
    truncated: bool = False,
    duration_ms: int | None = None,
    model_provider: str = "",
    model_name: str = "",
) -> None:
    seq = _STATE.next_seq()
    output_preview = tool_output[:300].replace("\n", " ") if tool_output else ""
    duration_text = f" duration_ms={duration_ms}" if duration_ms is not None else ""
    _log_line(
        f"AGENT.TOOL<-{agent_name}",
        f"{tool_name} status={status} len={len(tool_output or '')}{duration_text}",
        None,
        seq,
    )
    _record_event(
        source_module="trace_sdk",
        category="agent",
        action="tool_result",
        summary=f"{agent_name} {tool_name} result {status}",
        payload={
            "agent_name": agent_name,
            "trace_id": trace_id,
            "tool_name": tool_name,
            "status": status,
            "error_type": error_type,
            "tool_output_preview": output_preview,
            "tool_output_length": len(tool_output or ""),
            "tool_output_hash": _short_hash(tool_output or ""),
            "truncated": bool(truncated),
            "duration_ms": duration_ms,
        },
        model_provider=model_provider,
        model_name=model_name,
    )


def trace_ssh(command: str, exit_code: int, stdout: str = "", stderr: str = "") -> None:
    seq = _STATE.next_seq()
    timestamp = _canonical_ts()
    stdout_preview = stdout[:500].replace("\n", " | ") if stdout else ""
    stderr_preview = stderr[:300].replace("\n", " | ") if stderr else ""
    ssh_file = _STATE.hypothesis_dir / f"ssh-{seq:05d}.txt"
    ssh_path = _write_text(
        ssh_file,
        _render_ssh_artifact(
            run_id=_STATE.run_id,
            hypothesis_id=_STATE.current_hypothesis_id,
            hypothesis_name=_STATE.current_hypothesis_name,
            timestamp=timestamp,
            command=command,
            exit_code=exit_code,
            stdout=stdout,
            stderr=stderr,
        ),
    )
    _log_line("SSH", f"cmd='{command}' exit={exit_code} | stdout: {stdout_preview}", None, seq)
    if stderr_preview:
        _log_line("SSH.ERR", stderr_preview, None, seq)
    _record_event(
        source_module="trace_sdk",
        category="ssh",
        action="command",
        summary=f"SSH exit={exit_code}",
        payload={
            "command": command,
            "exit_code": exit_code,
            "stdout_preview": stdout_preview,
            "stderr_preview": stderr_preview,
        },
        artifact_paths=[ssh_path],
    )


def trace_benchmark(
    config: str,
    metric: float | None,
    decision: str,
    details: dict | None = None,
) -> None:
    seq = _STATE.next_seq()
    _log_line("BENCHMARK", f"config={config} metric={metric} decision={decision}", None, seq)
    if details:
        _log_line("BENCHMARK.DETAIL", json.dumps(details, default=str), None, seq)
    _record_event(
        source_module="trace_sdk",
        category="benchmark",
        action="result",
        summary=f"benchmark {decision}",
        payload={
            "config": config,
            "metric": metric,
            "decision": decision,
            "details": details or {},
        },
    )


def trace_state_change(old_state: str, new_state: str, reason: str = "") -> None:
    seq = _STATE.next_seq()
    summary = f"{old_state} -> {new_state}"
    _log_line("STATE", summary + (f" | reason: {reason}" if reason else ""), None, seq)
    _record_event(
        source_module="trace_sdk",
        category="state",
        action="transition",
        summary=summary,
        payload={"old_state": old_state, "new_state": new_state, "reason": reason},
    )


def record_event(
    *,
    source_module: str,
    category: str,
    action: str,
    summary: str,
    payload: dict[str, Any] | None = None,
    artifact_paths: list[str] | None = None,
    model_provider: str = "",
    model_name: str = "",
) -> dict[str, Any]:
    seq = _STATE.next_seq()
    event_payload = payload or {}
    _record_event(
        source_module=source_module,
        category=category,
        action=action,
        summary=summary,
        payload=event_payload,
        artifact_paths=artifact_paths,
        model_provider=model_provider,
        model_name=model_name,
    )
    return {
        "source_module": source_module,
        "category": category,
        "action": action,
        "summary": summary,
        **event_payload,
        "artifact_paths": list(artifact_paths or []),
        "model_provider": model_provider,
        "model_name": model_name,
        "run_id": _STATE.run_id,
        "seq": seq,
    }


def set_family(family: str, job: int = 0) -> None:
    _STATE.family = family
    _STATE.job = job


def get_log_file() -> Path:
    return _STATE.log_file


def get_session_id() -> str:
    return _SESSION_ID


def get_event_file() -> Path:
    return _STATE.canonical_event_file
