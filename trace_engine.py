from __future__ import annotations

import json
import logging as _logging
import os
import threading
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from itertools import count
from pathlib import Path
from typing import IO, Any, Callable

from opentelemetry import trace as otel_trace
from opentelemetry.context.context import Context
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import ReadableSpan, TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor, SpanExporter, SpanExportResult
from traceloop.sdk import Traceloop
from traceloop.sdk.instruments import Instruments

from autoresearch_constants import ENV_TRACE_MODE, TRACE_MODE_TRANSACTION

# Traceloop SDK is the OpenLLMetry layer used for model/workflow instrumentation.

_log = _logging.getLogger("trace_sdk")

DEFAULT_EXPORTER_NAME = "jsonl"
_TRACELOOP_INSTRUMENTS = {
    Instruments.OPENAI_AGENTS,
    Instruments.REQUESTS,
    Instruments.URLLIB3,
}


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


# --- artifact rendering (stateless string builders) -------------------------
# The agent prompt/response renderers live in trace_sdk next to their callers;
# the shared header + SSH renderer stay here with the rest of the marshalling.


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


class TraceEngine:
    """Owns trace initialization, the active exporter, the provider, the OTel
    context-switching, and the round/hypothesis state lifecycle.

    A single module-singleton instance (``_ENGINE``) backs every module-level
    ``trace_*`` / lifecycle function — those keep their exact names + signatures
    and delegate here. The engine swaps immutable ``RoundContext`` /
    ``HypothesisContext`` objects wholesale on ``begin_round`` / begin-hypothesis
    rather than mutating identity fields in place.

    ``provider_builder`` / ``initializer`` are injected seams so the facade
    module can keep ``_build_provider`` / ``_initialize_tracing`` as
    monkeypatchable module-level functions: the engine resolves them through the
    injected callables at call time rather than holding a direct reference.
    """

    def __init__(
        self,
        *,
        log_dir: Path,
        session_id: str,
        registry: _ExporterRegistry,
        instruments: set[Instruments],
        provider_builder: Callable[[], TracerProvider] | None = None,
        initializer: Callable[[], None] | None = None,
    ) -> None:
        self.log_dir = log_dir
        self.session_id = session_id
        self.registry = registry
        self.instruments = instruments
        self.provider: TracerProvider | None = None
        self.initialized = False
        self._provider_builder = provider_builder or self.build_provider
        self._initializer = initializer or self.initialize
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
        self.provider = self._provider_builder()

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
        self.provider = self._provider_builder()
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
        self.provider = self._provider_builder()

    def reset(self, *, exporter: SpanExporter | None = None) -> None:
        if exporter is not None:
            self.exporter = exporter
        self.initialized = False
        self.provider = None
        self._initializer()

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
