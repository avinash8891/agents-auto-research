from __future__ import annotations

import hashlib
import json
import os
import sys
import threading
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from itertools import count
from pathlib import Path
from typing import IO, Any, Callable, Iterator

from openinference.instrumentation import using_attributes
from opentelemetry import trace as otel_trace
from opentelemetry.context.context import Context
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import ReadableSpan, TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor, SpanExporter, SpanExportResult
from traceloop.sdk import Traceloop
from traceloop.sdk.instruments import Instruments

from autoresearch_constants import ENV_TRACE_MODE, TRACE_MODE_TRANSACTION
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
    otel_trace_id: str
    span_id: str
    parent_span_id: str
    span_name: str
    span_kind: str
    span_start_time: str
    span_end_time: str
    span_status_code: str
    span_status_message: str
    resource_attributes: dict[str, Any]
    scope: dict[str, str]
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
        try:
            with self._lock, self._event_file.open("a", encoding="utf-8") as handle:
                for span in spans:
                    artifact_paths = _coerce_sequence(
                        span.attributes.get("autoresearch.artifact_paths")
                    )
                    payload = _decode_json_attribute(
                        span.attributes.get("autoresearch.payload_json")
                    )
                    event_id = _string_attr(span, "autoresearch.event_id")
                    if not event_id:
                        event_id = f"span-{_span_trace_id(span)}-{_span_id(span)}"
                    source_module = _string_attr(span, "autoresearch.source_module")
                    if not source_module:
                        source_module = _span_scope(span)["name"]
                    category = _string_attr(span, "autoresearch.category")
                    if not category:
                        category = "instrumentation"
                    action = _string_attr(span, "autoresearch.action")
                    if not action:
                        action = str(span.name or "span")
                    event = TraceEvent(
                        event_id=event_id,
                        schema_version=int(span.attributes.get("autoresearch.schema_version", 1)),
                        timestamp=_string_attr(span, "autoresearch.timestamp", span.start_time),
                        otel_trace_id=_span_trace_id(span),
                        span_id=_span_id(span),
                        parent_span_id=_parent_span_id(span),
                        span_name=str(span.name or ""),
                        span_kind=_span_kind(span),
                        span_start_time=_ns_to_iso_z(getattr(span, "start_time", None)),
                        span_end_time=_ns_to_iso_z(
                            getattr(span, "end_time", None) or getattr(span, "start_time", None)
                        ),
                        span_status_code=_span_status_code(span),
                        span_status_message=_span_status_message(span),
                        resource_attributes=dict(
                            getattr(getattr(span, "resource", None), "attributes", {}) or {}
                        ),
                        scope=_span_scope(span),
                        source_module=source_module,
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
                        category=category,
                        action=action,
                        summary=_string_attr(span, "autoresearch.summary", span.name),
                        payload=payload,
                        artifact_paths=artifact_paths,
                    )
                    handle.write(event.to_json() + "\n")
        except OSError as exc:
            _log.debug("trace jsonl export failed (suppressed): %s", exc)
        return SpanExportResult.SUCCESS

    def shutdown(self) -> None:
        return None


class _SeqCounter:
    """Mutable ordinal holder owned by a RoundContext.

    Round/hypothesis IDENTITY is immutable (a fresh context object is installed
    per round/hypothesis). The seq ordinal is the one explicitly-mutable bit; it
    lives here so the surrounding RoundContext can stay a frozen dataclass.
    """

    __slots__ = ("value",)

    def __init__(self, value: int = 0) -> None:
        self.value = value

    def advance(self) -> int:
        self.value += 1
        return self.value


@dataclass(frozen=True, slots=True)
class HypothesisContext:
    """Immutable per-hypothesis identity.

    Identity fields (``hypothesis_id``/``hypothesis_name``) are frozen — the
    engine swaps a fresh instance on ``begin_hypothesis``. The OTel parent
    context and the per-agent context cache are mutable holders carried inside
    so the frozen identity can still accumulate span linkage during the
    hypothesis lifetime.
    """

    hypothesis_id: str | None = None
    hypothesis_name: str | None = None
    span_context: list[Context | None] = field(default_factory=lambda: [None], compare=False)
    agent_contexts: dict[str, Context] = field(default_factory=dict, compare=False)


@dataclass(frozen=True, slots=True)
class RoundContext:
    """Immutable per-round identity + layout.

    Identity/layout fields (``run_id``, ``log_file``, ``agent_log_dir``,
    ``family``, ``job``) are frozen — the engine installs a fresh instance on
    ``begin_round``. The seq counter, event-id counter, OTel round-context
    holder, log handle and active hypothesis are the explicitly-mutable bits and
    live in mutable holders so the surrounding identity stays immutable.
    """

    log_dir: Path
    session_id: str
    run_id: str
    log_file: Path
    agent_log_dir: Path
    family: str = ""
    job: int = 0
    exporter: SpanExporter | None = None
    seq_counter: _SeqCounter = field(default_factory=_SeqCounter, compare=False)
    hypothesis_counter: _SeqCounter = field(default_factory=_SeqCounter, compare=False)
    event_counter: count = field(default_factory=lambda: count(1), compare=False)
    span_context: list[Context | None] = field(default_factory=lambda: [None], compare=False)
    log_handle: list[IO[str] | None] = field(default_factory=lambda: [None], compare=False)
    hypothesis: list[HypothesisContext] = field(
        default_factory=lambda: [HypothesisContext()], compare=False
    )

    @property
    def canonical_event_file(self) -> Path:
        return self.agent_log_dir / "trace-events.jsonl"

    @property
    def active_hypothesis(self) -> HypothesisContext:
        return self.hypothesis[0]

    def hypothesis_dir(self) -> Path:
        hid = self.active_hypothesis.hypothesis_id
        if hid:
            path = self.agent_log_dir / hid
            path.mkdir(parents=True, exist_ok=True)
            return path
        self.agent_log_dir.mkdir(parents=True, exist_ok=True)
        return self.agent_log_dir


def _session_timestamp() -> str:
    return _canonical_ts().replace("-", "").replace(":", "").replace("T", "-")[0:15]


def _build_round_context(
    *,
    log_dir: Path,
    session_id: str,
    family: str,
    job: int,
    run_id: str,
    exporter: SpanExporter | None = None,
) -> RoundContext:
    """Construct a fresh immutable RoundContext, laying out its filesystem dirs.

    ``exporter`` defaults to a ``JsonLineTraceExporter`` bound to the round's
    canonical event file, preserving the per-round JSONL rotation behaviour.
    """
    log_file = log_dir / f"trace-{run_id}.log"
    agent_log_dir = log_dir / f"agents-{run_id}"
    log_dir.mkdir(parents=True, exist_ok=True)
    agent_log_dir.mkdir(parents=True, exist_ok=True)
    if exporter is None:
        exporter = JsonLineTraceExporter(agent_log_dir / "trace-events.jsonl")
    return RoundContext(
        log_dir=log_dir,
        session_id=session_id,
        run_id=run_id,
        log_file=log_file,
        agent_log_dir=agent_log_dir,
        family=family,
        job=job,
        exporter=exporter,
    )


class _ExporterRegistry:
    """Name -> exporter factory/instance registry.

    Alternate exporters (the default ``JsonLineTraceExporter``, a test-capture
    exporter, a future Halo backend) register here and are selected by name
    WITHOUT editing ``_initialize_tracing``. A registered value may be a
    ``SpanExporter`` instance (returned as-is) or a zero-arg factory that the
    registry calls on selection.
    """

    def __init__(self) -> None:
        self._entries: dict[str, SpanExporter | Callable[[], SpanExporter]] = {}

    def register(
        self, name: str, exporter_or_factory: SpanExporter | Callable[[], SpanExporter]
    ) -> None:
        self._entries[name] = exporter_or_factory

    def names(self) -> list[str]:
        return sorted(self._entries)

    def has(self, name: str) -> bool:
        return name in self._entries

    def create(self, name: str) -> SpanExporter:
        try:
            entry = self._entries[name]
        except KeyError as exc:
            raise KeyError(
                f"No exporter registered under {name!r}; " f"registered names: {self.names()}"
            ) from exc
        if isinstance(entry, SpanExporter):
            return entry
        return entry()


DEFAULT_EXPORTER_NAME = "jsonl"
_EXPORTER_REGISTRY = _ExporterRegistry()
_EXPORTER_REGISTRY.register(
    DEFAULT_EXPORTER_NAME,
    lambda: JsonLineTraceExporter(_STATE.canonical_event_file),
)
_TRACELOOP_INSTRUMENTS = {
    Instruments.OPENAI_AGENTS,
    Instruments.REQUESTS,
    Instruments.URLLIB3,
}

import logging as _logging

_log = _logging.getLogger(__name__)


class TraceEngine:
    """Owns trace initialization, the active exporter, the provider, the OTel
    context-switching, and the round/hypothesis state lifecycle.

    A single module-singleton instance (``_ENGINE``) backs every module-level
    ``trace_*`` / lifecycle function — those keep their exact names + signatures
    and delegate here. The engine swaps immutable ``RoundContext`` /
    ``HypothesisContext`` objects wholesale on ``begin_round`` / begin-hypothesis
    rather than mutating identity fields in place.
    """

    def __init__(
        self,
        *,
        log_dir: Path,
        session_id: str,
        registry: _ExporterRegistry,
        instruments: set[Instruments],
    ) -> None:
        self.log_dir = log_dir
        self.session_id = session_id
        self.registry = registry
        self.instruments = instruments
        self.provider: TracerProvider | None = None
        self.initialized = False
        run_id = f"R-{session_id}"
        self.round: RoundContext = _build_round_context(
            log_dir=log_dir,
            session_id=session_id,
            family="",
            job=0,
            run_id=run_id,
        )
        self.exporter: SpanExporter | None = self.round.exporter

    # --- exporter registry --------------------------------------------------

    def register_exporter(
        self, name: str, exporter_or_factory: SpanExporter | Callable[[], SpanExporter]
    ) -> None:
        self.registry.register(name, exporter_or_factory)

    def select_exporter(self, name: str) -> None:
        """Select a registered exporter by name and rebuild the provider."""
        self.configure_exporter(self.registry.create(name))

    # --- provider / resource construction -----------------------------------

    def build_resource(self) -> Resource:
        return Resource.create(
            {
                "service.name": "agents-auto-research",
                "service.namespace": "autoresearch",
                "service.instance.id": self.session_id,
            }
        )

    def build_provider(self) -> TracerProvider:
        provider = TracerProvider(resource=self.build_resource())
        provider.add_span_processor(SimpleSpanProcessor(self.exporter))
        return provider

    def reset_provider_for_current_state(self) -> None:
        self.provider = _build_provider()

    def tracer(self):
        return otel_trace.get_tracer(
            "agents-auto-research.trace_sdk", tracer_provider=self.provider
        )

    # --- lifecycle ----------------------------------------------------------

    def initialize(self) -> None:
        if self.initialized:
            return
        if os.getenv(ENV_TRACE_MODE) == TRACE_MODE_TRANSACTION:
            self.initialized = True
            return
        self.provider = _build_provider()
        if os.getenv("AUTORESEARCH_TRACING_DISABLED"):
            self.initialized = True
            return
        try:
            Traceloop.init(
                app_name="agents-auto-research",
                disable_batch=True,
                exporter=self.exporter,
                telemetry_enabled=False,
                api_key=os.getenv("TRACELOOP_API_KEY", "local-dev"),
                endpoint_is_traceloop=False,
                instruments=self.instruments,
                resource_attributes={"autoresearch.session_id": self.session_id},
            )
        except Exception as exc:
            _log.warning("Traceloop.init failed (suppressed): %s", exc)
        self.initialized = True

    def configure_exporter(self, exporter: SpanExporter) -> None:
        self.exporter = exporter
        self.provider = _build_provider()

    def reset(self, *, exporter: SpanExporter | None = None) -> None:
        if exporter is not None:
            self.exporter = exporter
        self.initialized = False
        self.provider = None
        _initialize_tracing()

    # --- round / hypothesis context swaps -----------------------------------

    def begin_round(self, round_number: int) -> None:
        """Install a fresh immutable RoundContext (new identity, seq reset)."""
        handle = self.round.log_handle[0]
        if handle is not None:
            handle.close()
        prefix = f"{self.round.family}-" if self.round.family else ""
        job_tag = f"job-{self.round.job}-" if self.round.job else ""
        run_id = f"R-{prefix}{job_tag}round-{round_number}-{_session_timestamp()}"
        self.round = _build_round_context(
            log_dir=self.log_dir,
            session_id=self.session_id,
            family=self.round.family,
            job=self.round.job,
            run_id=run_id,
        )
        self.exporter = self.round.exporter
        self.reset_provider_for_current_state()

    def begin_hypothesis(self, name: str) -> str:
        # Hypothesis ordinal is round-scoped: the counter lives in the round
        # context, so a fresh round automatically restarts numbering at H001.
        next_index = self.round.hypothesis_counter.advance()
        hypothesis_id = f"H{next_index:03d}"
        # Swap a fresh immutable HypothesisContext wholesale; the previous
        # hypothesis identity (and its agent-context cache) is discarded.
        self.round.hypothesis[0] = HypothesisContext(
            hypothesis_id=hypothesis_id, hypothesis_name=name
        )
        self.round.hypothesis_dir()
        return hypothesis_id

    def end_hypothesis(self) -> None:
        self.round.hypothesis[0] = HypothesisContext()


class TraceRuntimeState:
    """Compatibility facade over :class:`TraceEngine`.

    The module singleton ``_STATE`` is an instance of this facade so existing
    callers (and tests) can keep reading/writing ``_STATE.run_id``,
    ``_STATE.seq``, ``_STATE.provider``, ``_STATE.exporter`` etc. and calling
    ``_STATE.next_seq()`` / ``_STATE.get_log_handle()``. Lifecycle attributes
    (``provider``/``initialized``/``exporter``/``session_id``) live on the
    engine; round/hypothesis fields proxy to the engine's active immutable
    ``RoundContext`` / ``HypothesisContext``.
    """

    __slots__ = ("_engine",)

    def __init__(self, engine: TraceEngine) -> None:
        object.__setattr__(self, "_engine", engine)

    # --- lifecycle attributes (live on the engine) --------------------------

    @property
    def provider(self) -> TracerProvider | None:
        return self._engine.provider

    @provider.setter
    def provider(self, value: TracerProvider | None) -> None:
        self._engine.provider = value

    @property
    def initialized(self) -> bool:
        return self._engine.initialized

    @initialized.setter
    def initialized(self, value: bool) -> None:
        self._engine.initialized = value

    @property
    def exporter(self) -> SpanExporter | None:
        return self._engine.exporter

    @exporter.setter
    def exporter(self, value: SpanExporter | None) -> None:
        self._engine.exporter = value

    @property
    def session_id(self) -> str:
        return self._engine.session_id

    @property
    def log_dir(self) -> Path:
        return self._engine.log_dir

    # --- round identity / layout (proxy to the active RoundContext) ---------

    @property
    def family(self) -> str:
        return self._engine.round.family

    @family.setter
    def family(self, value: str) -> None:
        from dataclasses import replace

        self._engine.round = replace(self._engine.round, family=value)

    @property
    def job(self) -> int:
        return self._engine.round.job

    @job.setter
    def job(self, value: int) -> None:
        from dataclasses import replace

        self._engine.round = replace(self._engine.round, job=value)

    @property
    def run_id(self) -> str:
        return self._engine.round.run_id

    @property
    def log_file(self) -> Path:
        return self._engine.round.log_file

    @property
    def agent_log_dir(self) -> Path:
        return self._engine.round.agent_log_dir

    @property
    def event_counter(self) -> count:
        return self._engine.round.event_counter

    @property
    def canonical_event_file(self) -> Path:
        return self._engine.round.canonical_event_file

    @property
    def hypothesis_dir(self) -> Path:
        return self._engine.round.hypothesis_dir()

    # --- seq ordinal (mutable holder inside the RoundContext) ---------------

    @property
    def seq(self) -> int:
        return self._engine.round.seq_counter.value

    @seq.setter
    def seq(self, value: int) -> None:
        self._engine.round.seq_counter.value = value

    def next_seq(self) -> int:
        return self._engine.round.seq_counter.advance()

    def next_event_id(self) -> str:
        return f"evt-{next(self._engine.round.event_counter):08d}"

    # --- OTel parent-context cache (mutable holders) ------------------------

    @property
    def round_context(self) -> Context | None:
        return self._engine.round.span_context[0]

    @round_context.setter
    def round_context(self, value: Context | None) -> None:
        self._engine.round.span_context[0] = value

    @property
    def current_hypothesis_id(self) -> str | None:
        return self._engine.round.active_hypothesis.hypothesis_id

    @property
    def current_hypothesis_name(self) -> str | None:
        return self._engine.round.active_hypothesis.hypothesis_name

    @property
    def current_hypothesis_context(self) -> Context | None:
        return self._engine.round.active_hypothesis.span_context[0]

    @current_hypothesis_context.setter
    def current_hypothesis_context(self, value: Context | None) -> None:
        self._engine.round.active_hypothesis.span_context[0] = value

    @property
    def agent_contexts(self) -> dict[str, Context]:
        return self._engine.round.active_hypothesis.agent_contexts

    # --- log handle (mutable holder inside the RoundContext) ----------------

    def get_log_handle(self) -> IO[str]:
        holder = self._engine.round.log_handle
        if holder[0] is None:
            log_file = self._engine.round.log_file
            try:
                holder[0] = log_file.open("a", encoding="utf-8")
            except OSError as exc:
                raise RuntimeError(f"Cannot open trace log {log_file}: {exc}") from exc
        return holder[0]

    # --- lifecycle delegators ----------------------------------------------

    def begin_hypothesis(self, name: str) -> str:
        return self._engine.begin_hypothesis(name)

    def reset_for_round(self, round_number: int) -> None:
        self._engine.begin_round(round_number)

    @staticmethod
    def session_timestamp() -> str:
        return _session_timestamp()


_ENGINE = TraceEngine(
    log_dir=_LOG_DIR,
    session_id=_SESSION_ID,
    registry=_EXPORTER_REGISTRY,
    instruments=_TRACELOOP_INSTRUMENTS,
)
_STATE = TraceRuntimeState(_ENGINE)


def _write_text(path: Path, content: str) -> str:
    try:
        write_text_atomic(path, content)
    except OSError as exc:
        _log.debug("trace artifact write failed (suppressed): %s", exc)
        return ""
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


def _span_trace_id(span: ReadableSpan) -> str:
    get_context = getattr(span, "get_span_context", None)
    if get_context is None:
        return "0" * 32
    context = get_context()
    return f"{context.trace_id:032x}" if context and context.trace_id else "0" * 32


def _span_id(span: ReadableSpan) -> str:
    get_context = getattr(span, "get_span_context", None)
    if get_context is None:
        return "0" * 16
    context = get_context()
    return f"{context.span_id:016x}" if context and context.span_id else "0" * 16


def _parent_span_id(span: ReadableSpan) -> str:
    parent = getattr(span, "parent", None)
    span_id = getattr(parent, "span_id", 0) if parent is not None else 0
    return f"{span_id:016x}" if span_id else ""


def _span_kind(span: ReadableSpan) -> str:
    name = getattr(getattr(span, "kind", None), "name", "") or "INTERNAL"
    return f"SPAN_KIND_{name.upper()}"


def _span_status_code(span: ReadableSpan) -> str:
    status = getattr(span, "status", None)
    code_name = getattr(getattr(status, "status_code", None), "name", "") or "UNSET"
    return f"STATUS_CODE_{code_name.upper()}"


def _span_status_message(span: ReadableSpan) -> str:
    status = getattr(span, "status", None)
    return str(getattr(status, "description", "") or "")


def _span_scope(span: ReadableSpan) -> dict[str, str]:
    scope = getattr(span, "instrumentation_scope", None)
    return {
        "name": str(getattr(scope, "name", "") or "agents-auto-research.trace_sdk"),
        "version": str(getattr(scope, "version", "") or ""),
    }


def _ns_to_iso_z(ns: int | None) -> str:
    if not ns:
        return _canonical_ts()
    seconds, nanos = divmod(int(ns), 1_000_000_000)
    base = datetime.fromtimestamp(seconds, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
    return f"{base}.{nanos:09d}Z"


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
