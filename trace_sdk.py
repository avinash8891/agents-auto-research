from __future__ import annotations

import json
import os
import threading
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from itertools import count
from pathlib import Path
from typing import IO, Any, Iterator

from openinference.instrumentation import using_attributes
from openinference.instrumentation.openai import OpenAIInstrumentor
from opentelemetry import trace as otel_trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import ReadableSpan, TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor, SpanExporter, SpanExportResult
from traceloop.sdk import Traceloop
from traceloop.sdk.instruments import Instruments

from persistence_utils import write_text_atomic

# Traceloop SDK is the OpenLLMetry layer used for model/workflow instrumentation.

_LOG_DIR = Path(__file__).resolve().parent / "logs"
_LOG_DIR.mkdir(exist_ok=True)

_SESSION_ID = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")


def _canonical_ts() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


@dataclass(slots=True)
class TraceEvent:
    event_id: str
    schema_version: int
    timestamp: str
    source_module: str
    run_id: str
    session_id: str
    family: str
    job: int
    model_provider: str
    model_name: str
    hypothesis_id: str | None
    hypothesis_name: str | None
    seq: int
    category: str
    action: str
    summary: str
    payload: dict[str, Any]
    artifact_paths: list[str]

    def to_json(self) -> str:
        return json.dumps(asdict(self), default=str)


class JsonLineTraceExporter(SpanExporter):
    def __init__(self, event_file: Path) -> None:
        self._event_file = event_file
        self._event_file.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    @property
    def event_file(self) -> Path:
        return self._event_file

    def export(self, spans: list[ReadableSpan]) -> SpanExportResult:
        if not spans:
            return SpanExportResult.SUCCESS
        with self._lock, self._event_file.open("a", encoding="utf-8") as handle:
            for span in spans:
                if not span.attributes.get("autoresearch.event_id"):
                    continue
                artifact_paths = _coerce_sequence(
                    span.attributes.get("autoresearch.artifact_paths")
                )
                payload = _decode_json_attribute(span.attributes.get("autoresearch.payload_json"))
                event = TraceEvent(
                    event_id=_string_attr(span, "autoresearch.event_id"),
                    schema_version=int(span.attributes.get("autoresearch.schema_version", 1)),
                    timestamp=_string_attr(span, "autoresearch.timestamp", span.start_time),
                    source_module=_string_attr(span, "autoresearch.source_module"),
                    run_id=_string_attr(span, "autoresearch.run_id"),
                    session_id=_string_attr(span, "autoresearch.session_id"),
                    family=_string_attr(span, "autoresearch.family"),
                    job=int(span.attributes.get("autoresearch.job", 0)),
                    model_provider=_string_attr(span, "autoresearch.model_provider"),
                    model_name=_string_attr(span, "autoresearch.model_name"),
                    hypothesis_id=_optional_string(
                        span.attributes.get("autoresearch.hypothesis_id")
                    ),
                    hypothesis_name=_optional_string(
                        span.attributes.get("autoresearch.hypothesis_name")
                    ),
                    seq=int(span.attributes.get("autoresearch.seq", 0)),
                    category=_string_attr(span, "autoresearch.category"),
                    action=_string_attr(span, "autoresearch.action"),
                    summary=_string_attr(span, "autoresearch.summary", span.name),
                    payload=payload,
                    artifact_paths=artifact_paths,
                )
                handle.write(event.to_json() + "\n")
        return SpanExportResult.SUCCESS

    def shutdown(self) -> None:
        return None


@dataclass(slots=True)
class TraceRuntimeState:
    log_dir: Path
    session_id: str
    family: str = ""
    job: int = 0
    run_id: str = ""
    log_file: Path | None = None
    agent_log_dir: Path | None = None
    seq: int = 0
    hypothesis_counter: int = 0
    current_hypothesis_id: str | None = None
    current_hypothesis_name: str | None = None
    event_counter: count = field(default_factory=lambda: count(1))
    exporter: JsonLineTraceExporter | None = None
    log_handle: IO[str] | None = field(default=None, compare=False)

    def __post_init__(self) -> None:
        if not self.run_id:
            self.run_id = f"R-{self.session_id}"
        if self.log_file is None:
            self.log_file = self.log_dir / f"trace-{self.run_id}.log"
        if self.agent_log_dir is None:
            self.agent_log_dir = self.log_dir / f"agents-{self.run_id}"
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.agent_log_dir.mkdir(parents=True, exist_ok=True)
        if self.exporter is None:
            self.exporter = JsonLineTraceExporter(self.canonical_event_file)

    def next_seq(self) -> int:
        self.seq += 1
        return self.seq

    def next_event_id(self) -> str:
        return f"evt-{next(self.event_counter):08d}"

    def get_log_handle(self) -> IO[str]:
        if self.log_handle is None:
            if self.log_file is None:
                raise RuntimeError("TraceRuntimeState.log_file is not set")
            try:
                self.log_handle = self.log_file.open("a", encoding="utf-8")
            except OSError as exc:
                raise RuntimeError(f"Cannot open trace log {self.log_file}: {exc}") from exc
        return self.log_handle

    def reset_for_round(self, round_number: int) -> None:
        if self.log_handle is not None:
            self.log_handle.close()
            self.log_handle = None
        self.seq = 0
        self.hypothesis_counter = 0
        prefix = f"{self.family}-" if self.family else ""
        job_tag = f"job-{self.job}-" if self.job else ""
        ts = self.session_timestamp()
        self.run_id = f"R-{prefix}{job_tag}round-{round_number}-{ts}"
        self.log_file = self.log_dir / f"trace-{self.run_id}.log"
        self.agent_log_dir = self.log_dir / f"agents-{self.run_id}"
        self.agent_log_dir.mkdir(parents=True, exist_ok=True)
        self.current_hypothesis_id = None
        self.current_hypothesis_name = None
        self.event_counter = count(1)
        self.exporter = JsonLineTraceExporter(self.canonical_event_file)

    def begin_hypothesis(self, name: str) -> str:
        self.hypothesis_counter += 1
        self.current_hypothesis_id = f"H{self.hypothesis_counter:03d}"
        self.current_hypothesis_name = name
        self.hypothesis_dir.mkdir(parents=True, exist_ok=True)
        return self.current_hypothesis_id

    @property
    def hypothesis_dir(self) -> Path:
        if self.current_hypothesis_id:
            path = self.agent_log_dir / self.current_hypothesis_id
            path.mkdir(parents=True, exist_ok=True)
            return path
        self.agent_log_dir.mkdir(parents=True, exist_ok=True)
        return self.agent_log_dir

    @property
    def canonical_event_file(self) -> Path:
        return self.agent_log_dir / "trace-events.jsonl"

    @staticmethod
    def session_timestamp() -> str:
        return _canonical_ts().replace("-", "").replace(":", "").replace("T", "-")[0:15]


_STATE = TraceRuntimeState(log_dir=_LOG_DIR, session_id=_SESSION_ID)
_PROVIDER: TracerProvider | None = None
_INITIALIZED = False
_OPENAI_INSTRUMENTED = False
_OPENAI_INSTRUMENTOR = OpenAIInstrumentor()

import logging as _logging

_log = _logging.getLogger(__name__)


def _write_text(path: Path, content: str) -> str:
    write_text_atomic(path, content)
    return str(path)


def _render_artifact_header(
    *,
    run_id: str,
    hypothesis_id: str | None,
    hypothesis_name: str | None,
    timestamp: str,
    agent_name: str | None = None,
    trace_id: str | None = None,
) -> list[str]:
    lines = [
        f"=== RUN_ID: {run_id} ===",
        f"=== HYPOTHESIS_ID: {hypothesis_id or ''} ===",
        f"=== HYPOTHESIS_NAME: {hypothesis_name or ''} ===",
    ]
    if agent_name is not None:
        lines.append(f"=== AGENT: {agent_name} ===")
    lines.append(f"=== TIMESTAMP: {timestamp} ===")
    if trace_id is not None:
        lines.append(f"=== TRACE_ID: {trace_id} ===")
    return lines


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


def _render_ssh_artifact(
    *,
    run_id: str,
    hypothesis_id: str | None,
    hypothesis_name: str | None,
    timestamp: str,
    command: str,
    exit_code: int,
    stdout: str,
    stderr: str,
) -> str:
    lines = _render_artifact_header(
        run_id=run_id,
        hypothesis_id=hypothesis_id,
        hypothesis_name=hypothesis_name,
        timestamp=timestamp,
    ) + [
        "",
        "--- COMMAND ---",
        command,
        "",
        f"--- EXIT CODE ---\n{exit_code}",
        "",
        "--- STDOUT ---",
        stdout,
        "",
        "--- STDERR ---",
        stderr,
        "",
    ]
    return "\n".join(lines)


def _string_attr(span: ReadableSpan, key: str, default: Any = "") -> str:
    value = span.attributes.get(key, default)
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


def _optional_string(value: Any) -> str | None:
    if value in (None, "", "None"):
        return None
    return str(value)


def _coerce_sequence(value: Any) -> list[str]:
    if value in (None, ""):
        return []
    if isinstance(value, (list, tuple)):
        return [str(item) for item in value]
    return [str(value)]


def _decode_json_attribute(value: Any) -> dict[str, Any]:
    if not value:
        return {}
    try:
        decoded = json.loads(str(value))
    except json.JSONDecodeError:
        return {"unparsed_payload": str(value)}
    return decoded if isinstance(decoded, dict) else {"value": decoded}


def _log_line(component: str, message: str, data: dict[str, Any] | None, seq: int) -> None:
    htag = _hyp_tag()
    timestamp = _canonical_ts()
    line = f"[{timestamp}] [{_STATE.run_id}]{htag} [{seq:05d}] [{component}] {message}"
    if data:
        line += f" | {json.dumps(data, default=str)}"
    line += "\n"
    handle = _STATE.get_log_handle()
    handle.write(line)
    handle.flush()
    print(f"TRACE {_STATE.run_id}{htag} [{component}] {message}")


def _build_resource() -> Resource:
    return Resource.create(
        {
            "service.name": "agents-auto-research",
            "service.namespace": "autoresearch",
            "service.instance.id": _STATE.session_id,
        }
    )


def _build_provider() -> TracerProvider:
    provider = TracerProvider(resource=_build_resource())
    provider.add_span_processor(SimpleSpanProcessor(_STATE.exporter))
    return provider


def _bind_instrumentation() -> None:
    global _OPENAI_INSTRUMENTED
    if _PROVIDER is None:
        return
    if _OPENAI_INSTRUMENTED:
        _OPENAI_INSTRUMENTOR.uninstrument()
    _OPENAI_INSTRUMENTOR.instrument(tracer_provider=_PROVIDER)
    _OPENAI_INSTRUMENTED = True


def _initialize_tracing() -> None:
    global _PROVIDER, _INITIALIZED
    if _INITIALIZED:
        return
    _PROVIDER = _build_provider()
    if os.getenv("PYTEST_CURRENT_TEST"):
        _bind_instrumentation()
        _INITIALIZED = True
        return
    try:
        Traceloop.init(
            app_name="agents-auto-research",
            disable_batch=True,
            exporter=_STATE.exporter,
            telemetry_enabled=False,
            api_key=os.getenv("TRACELOOP_API_KEY", "local-dev"),
            endpoint_is_traceloop=False,
            instruments={
                Instruments.OPENAI,
                Instruments.OPENAI_AGENTS,
                Instruments.REQUESTS,
                Instruments.URLLIB3,
            },
            resource_attributes={"autoresearch.session_id": _STATE.session_id},
        )
    except Exception as exc:
        _log.warning("Traceloop.init failed (suppressed): %s", exc)
    _bind_instrumentation()
    _INITIALIZED = True


_initialize_tracing()


def _reset_provider_for_current_state() -> None:
    global _PROVIDER
    _PROVIDER = _build_provider()
    _bind_instrumentation()


def _tracer():
    return otel_trace.get_tracer("agents-auto-research.trace_sdk", tracer_provider=_PROVIDER)


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
        with _tracer().start_as_current_span(span_name) as span:
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
            yield


def begin_hypothesis(name: str) -> str:
    hypothesis_id = _STATE.begin_hypothesis(name)
    trace("HYPOTHESIS", f"BEGIN {hypothesis_id} name={name}")
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
    _STATE.reset_for_round(round_number)
    _reset_provider_for_current_state()


def end_hypothesis(decision: str = "", metric: float | None = None) -> None:
    trace(
        "HYPOTHESIS",
        f"END {_STATE.current_hypothesis_id} name={_STATE.current_hypothesis_name} decision={decision} metric={metric}",
    )


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
                "output_tokens": int(output_tokens or 0),
                "total_tokens": int(total_tokens or 0),
                "cost_usd": float(cost_usd or 0.0),
                "dedupe_key": dedupe_key,
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
        f"PROMPT sent (len={len(prompt)})",
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
