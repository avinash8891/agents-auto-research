"""Round-local causal harvest artifacts and terminal model promotion."""

from __future__ import annotations

import asyncio
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml

from autoresearch_artifacts import round_number_from_path, serialize_artifact_path
from autoresearch_constants import research_engine_retest_extension_months
from autoresearch_logging import get_logger
from autoresearch_paths import resolve_config_path
from causal_model import CausalModelStore
from feature_table import FeatureTableArtifact
from persistence_utils import read_config_payload, write_json_atomic
from research_types import CausalModel

log = get_logger(__name__)

_HARVEST_LESSON_TIMEOUT_ENV = "AUTORESEARCH_HARVEST_LESSON_TIMEOUT_SECONDS"

if TYPE_CHECKING:
    from autoresearch_controller import AutoresearchController


@dataclass(frozen=True)
class PendingCausalModelArtifact:
    """Round-local model candidate awaiting registered prediction harvest."""

    round_root: Path

    @property
    def path(self) -> Path:
        return self.round_root / "pending_causal_model.json"

    def write(self, model: CausalModel) -> None:
        self.round_root.mkdir(parents=True, exist_ok=True)
        write_json_atomic(self.path, model.model_dump())

    def load(self) -> CausalModel | None:
        if not self.path.exists():
            return None
        return CausalModel.model_validate(json.loads(self.path.read_text(encoding="utf-8")))


@dataclass(frozen=True)
class RetestRequested:
    """Explicit transition emitted when prediction validation needs one retest."""

    next_state: dict[str, Any]


def write_feature_table_artifact(
    controller: "AutoresearchController",
    *,
    config: str,
    details: dict[str, Any],
    artifact_dir: Path,
    feature_artifact: FeatureTableArtifact | None = None,
) -> None:
    trades_file = str(details.get("trades_file") or "")
    if not trades_file:
        return
    runtime_config = load_runtime_config_contents(controller, config)
    if not runtime_config.get("data_universe"):
        return

    import pandas as pd

    from backtest.data_universe import load_universe_data
    from feature_table import build_feature_table

    trades_path = Path(trades_file)
    if not trades_path.is_absolute():
        trades_path = artifact_dir / trades_path
    trades_df = pd.read_csv(trades_path)
    batch = load_universe_data(runtime_config)
    bars_df = _wide_ohlcv_batch_to_long_bars(batch)
    events_file = details.get("strategy_events_file")
    if isinstance(events_file, str) and events_file:
        events_path = Path(events_file)
        if not events_path.is_absolute():
            events_file = str(artifact_dir / events_path)
    events = _load_strategy_events(events_file)
    if feature_artifact is None:
        round_root = artifact_dir.parent if artifact_dir.name == "backtest" else artifact_dir
        feature_artifact = FeatureTableArtifact(round_root)
    table = build_feature_table(
        trades_df,
        bars_df,
        events,
        controller.family.name,
        runtime_config=runtime_config,
        quarantine_path=feature_artifact.path.parent / "feature_table_quarantine.json",
    )
    feature_artifact.write(table)
    details["feature_table_file"] = str(feature_artifact.path)


def evaluate_registered_predictions(
    controller: "AutoresearchController",
    run_output_dir: Path,
    metric: float,
    details: dict[str, Any],
    *,
    baseline_override: dict[str, Any] | None = None,
) -> Any | None:
    registered_path = run_output_dir.parent / "registered_predictions.json"
    if not registered_path.exists():
        return None
    from experiment_evaluator import evaluate_predictions

    primary_metric_name = (
        controller.primary_metric_name()
        if hasattr(controller, "primary_metric_name")
        else "profit_factor"
    )
    candidate_metrics = _flatten_metrics(details, primary_metric=(primary_metric_name, metric))
    return evaluate_predictions(
        registered_path,
        baseline=(
            _baseline_metrics_from_first_result(controller)
            if baseline_override is None
            else baseline_override
        ),
        candidate=candidate_metrics,
        config=_family_base_config(controller),
    )


def retest_baseline_metrics(
    controller: "AutoresearchController",
    state: dict[str, Any],
    run_output_dir: Path,
) -> dict[str, Any] | None:
    """Re-run the baseline over the retest's extended validation range.

    A retest candidate runs on [validation_start, extended_end] while the
    checkpoint baseline was measured on the original range — count metrics
    like trade_count would pass their direction checks mechanically on any
    extension. A range-matched baseline keeps the comparison honest.

    Returns None (caller falls back to the checkpoint baseline) when no
    baseline run exists or the rerun fails; the bias risk is logged loudly.
    """
    next_action = state.get("next_action") or {}
    metadata = next_action.get("registered_prediction_retest") or {}
    extended_end = str(metadata.get("validation_end") or "")
    if not extended_end:
        return None
    from walkforward import latest_baseline

    try:
        current_job = int(state.get("job"))
    except (TypeError, ValueError):
        current_job = None
    records = [
        record
        for record in controller.backtest_run_db.all()
        if current_job is None or _record_job(record) == current_job
    ]
    baseline_record = latest_baseline(records)
    if baseline_record is None:
        log.error(
            "retest baseline rerun skipped: no accepted baseline run in DB "
            "| falling back to checkpoint baseline; count-metric directions may be inflated"
        )
        return None
    config = dict(baseline_record.runtime_config)
    config["validation_end"] = extended_end
    output_dir = run_output_dir.parent / "baseline_retest"
    output_dir.mkdir(parents=True, exist_ok=True)
    config_path = output_dir / "config.json"
    write_json_atomic(config_path, config)
    command = controller.family.benchmark_command(str(config_path), output_dir=str(output_dir))
    code, output = controller.run_command(command)
    if code != 0:
        log.error(
            "retest baseline rerun failed exit=%s "
            "| falling back to checkpoint baseline; count-metric directions may be inflated",
            code,
        )
        return None
    try:
        primary_metric_name = (
            controller.primary_metric_name()
            if hasattr(controller, "primary_metric_name")
            else "profit_factor"
        )
        parsed = controller.parse_metric(output, name=primary_metric_name)
        metrics = _flatten_metrics(
            controller.parse_benchmark_details(output),
            primary_metric=(primary_metric_name, parsed),
        )
    except Exception as exc:  # noqa: BLE001 — fail-open: external backtest output
        log.error(
            "retest baseline rerun parse failed: %s "
            "| falling back to checkpoint baseline; count-metric directions may be inflated",
            exc,
        )
        return None
    return metrics


def _record_job(record: Any) -> int | None:
    try:
        return int(record.job)
    except (TypeError, ValueError, AttributeError):
        return None


def attach_harvest_lesson(
    controller: "AutoresearchController",
    run_output_dir: Path,
    verdict: Any,
) -> Any:
    """Attach an LLM-synthesized harvest lesson when prediction gaps exist.

    Falls back to the deterministic evaluator summary if the LLM path is
    unavailable, disabled, or returns an empty response.
    """

    fallback = str(getattr(verdict, "lesson", "") or getattr(verdict, "summary", ""))
    prediction_results = [dict(item) for item in getattr(verdict, "prediction_results", [])]
    if not prediction_results:
        return (
            verdict.model_copy(update={"lesson": fallback})
            if hasattr(verdict, "model_copy")
            else verdict
        )
    selected = _selected_thesis_payload(run_output_dir.parent)
    lesson = fallback
    if _harvest_lesson_llm_enabled():
        try:
            generated = _generate_harvest_lesson(
                controller=controller,
                verdict=verdict,
                selected_thesis=selected,
                prediction_results=prediction_results,
            )
        except (asyncio.TimeoutError, TimeoutError) as exc:
            log.warning("harvest lesson LLM synthesis timed out; using metric fallback: %s", exc)
        except Exception as exc:  # noqa: BLE001
            log.warning("harvest lesson LLM synthesis failed; using metric fallback: %s", exc)
        else:
            if generated.strip():
                lesson = generated.strip()
    if hasattr(verdict, "model_copy"):
        return verdict.model_copy(update={"lesson": lesson})
    return verdict


def _harvest_lesson_llm_enabled() -> bool:
    if os.environ.get("AUTORESEARCH_DISABLE_HARVEST_LLM_LESSON") == "1":
        return False
    if os.environ.get("AUTORESEARCH_ENABLE_HARVEST_LLM_LESSON") == "1":
        return True
    return "PYTEST_CURRENT_TEST" not in os.environ


def _harvest_lesson_timeout_seconds() -> float:
    raw = os.environ.get(_HARVEST_LESSON_TIMEOUT_ENV, "60")
    try:
        timeout = float(raw)
    except ValueError as exc:
        raise ValueError(f"{_HARVEST_LESSON_TIMEOUT_ENV} must be a positive number") from exc
    if timeout <= 0:
        raise ValueError(f"{_HARVEST_LESSON_TIMEOUT_ENV} must be greater than zero")
    return timeout


def _generate_harvest_lesson(
    *,
    controller: "AutoresearchController",
    verdict: Any,
    selected_thesis: dict[str, Any],
    prediction_results: list[dict[str, Any]],
) -> str:
    from agents import Agent as OAIAgent
    from agents import RunConfig as OAIRunConfig
    from agents import Runner as OAIRunner
    from agents.models.openai_chatcompletions import OpenAIChatCompletionsModel

    from agent_infra import _OAUTH_PROXY_URL, _get_openai_client, _run_coroutine_sync
    from autoresearch_constants import DEFAULT_AGENT_MODEL

    async def _run() -> str:
        client = _get_openai_client(_OAUTH_PROXY_URL)
        model = OpenAIChatCompletionsModel(model=DEFAULT_AGENT_MODEL, openai_client=client)
        agent = OAIAgent(
            name="harvest-lesson-synthesizer",
            instructions=(
                "You convert registered prediction verdicts into one durable lesson "
                "for the next causal mechanism proposal. Be concrete, mention the "
                "prediction gaps, and return only one sentence."
            ),
            tools=[],
            model=model,
        )
        prompt = json.dumps(
            {
                "family": getattr(getattr(controller, "family", None), "name", ""),
                "thesis_id": getattr(verdict, "thesis_id", ""),
                "status": getattr(verdict, "status", ""),
                "summary": getattr(verdict, "summary", ""),
                "story": selected_thesis.get("story") or selected_thesis.get("hypothesis"),
                "rule": selected_thesis.get("rule"),
                "change": selected_thesis.get("proposed_change")
                or selected_thesis.get("config_changes"),
                "prediction_results": prediction_results,
            },
            default=str,
        )
        result = OAIRunner.run_streamed(
            agent,
            prompt,
            run_config=OAIRunConfig(tracing_disabled=True),
        )
        await _drain_stream_with_timeout(result, timeout_seconds=_harvest_lesson_timeout_seconds())
        return str(getattr(result, "final_output", "") or "")

    return _run_coroutine_sync(_run())


async def _drain_stream_with_timeout(result: Any, *, timeout_seconds: float) -> None:
    async def _drain() -> None:
        async for _ in result.stream_events():
            pass

    await asyncio.wait_for(_drain(), timeout=timeout_seconds)


def write_harvest_verdict_artifact(
    controller: "AutoresearchController",
    run_output_dir: Path,
    verdict: Any,
) -> Path:
    round_root = run_output_dir.parent
    selected = _selected_thesis_payload(round_root)
    prediction_rows = []
    for result in getattr(verdict, "prediction_results", []):
        row = dict(result)
        if "gap" not in row and "magnitude_gap" in row:
            row["gap"] = row["magnitude_gap"]
        prediction_rows.append(row)
    payload = {
        "round": round_number_from_path(round_root) or 0,
        "thesis_id": str(getattr(verdict, "thesis_id", "") or selected.get("thesis_id") or ""),
        "status": str(getattr(verdict, "status", "")),
        "change": selected.get("proposed_change") or selected.get("config_changes") or {},
        "rule": str(selected.get("rule") or ""),
        "registered_predictions": prediction_rows,
        "summary": str(getattr(verdict, "summary", "")),
        "lesson": str(getattr(verdict, "lesson", "") or getattr(verdict, "summary", "")),
    }
    path = round_root / "harvest_verdict.json"
    write_json_atomic(path, payload)
    return path


def is_registered_prediction_retest_action(state: dict[str, Any]) -> bool:
    next_action = state.get("next_action")
    return isinstance(next_action, dict) and next_action.get("source") == (
        "registered_prediction_retest"
    )


def force_registered_inconclusive_after_retest(verdict: Any) -> Any:
    return verdict.model_copy(
        update={
            "status": "refuted",
            "summary": f"forced_after_retest: {verdict.summary}",
            "lesson": f"forced_after_retest: {verdict.summary}",
        }
    )


def request_registered_prediction_retest(
    controller: "AutoresearchController",
    state: dict[str, Any],
    *,
    config: str,
) -> RetestRequested:
    config_path = resolve_config_path(
        config,
        code_root=controller.root,
        runtime_root=_runtime_root(controller),
        execution_root=_execution_root(controller),
    )
    retest_config_path, metadata = _write_extended_retest_config(
        controller,
        config_path,
    )
    next_action = dict(state.get("next_action") or {})
    next_action.update(
        {
            "type": "run_round",
            "config": serialize_artifact_path(retest_config_path, controller.root),
            "source": "registered_prediction_retest",
            "registered_prediction_retest": metadata,
        }
    )
    persisted = controller.read_state()
    persisted.update(
        {
            "state": "running",
            "job": state.get("job"),
            "research_round": state.get("research_round"),
            "selected_thesis_id": state.get("selected_thesis_id"),
            "next_action": next_action,
            "blockers": [],
        }
    )
    return RetestRequested(next_state=persisted)


def apply_registered_verdict_to_causal_factor(
    controller: "AutoresearchController",
    config: str,
    verdict: Any,
) -> None:
    """Promote a pending causal model only after a terminal registered verdict."""

    status = getattr(verdict, "status", "")
    if status not in {"supported", "refuted"}:
        return

    round_root = _round_root_for_config(controller, config)
    pending = PendingCausalModelArtifact(round_root).load()
    if pending is None:
        return

    rule = str(_selected_thesis_payload(round_root).get("rule") or "")
    if not rule:
        return

    target_status = "harvested" if status == "supported" else "refuted"
    lesson = str(getattr(verdict, "lesson", "") or getattr(verdict, "summary", ""))
    updated_pending_factor = None
    for factor in pending.factors:
        if factor.rule == rule:
            updated_pending_factor = factor.model_copy(
                update={"status": target_status, "lesson": lesson}
            )
            break
    if updated_pending_factor is None:
        return

    store = CausalModelStore(runtime_root=_runtime_root(controller), code_root=controller.root)
    live = store.load(pending.family)
    live_factors = []
    matched_live = False
    for factor in live.factors:
        if factor.rule == rule:
            live_factors.append(updated_pending_factor)
            matched_live = True
        else:
            live_factors.append(factor)
    if not matched_live:
        live_factors.append(updated_pending_factor)
    merged_history = list(live.accuracy_history)
    merged_history.extend(
        point for point in pending.accuracy_history if point not in merged_history
    )
    store.save(
        live.model_copy(
            update={
                "version": live.version + 1,
                "factors": live_factors,
                "accuracy_history": merged_history,
            }
        )
    )


@dataclass
class VerdictOutcome:
    """Resolved registered-prediction verdict plus its effect on the round."""

    verdict: Any | None
    decision: str
    retest_request: RetestRequested | None


def resolve_registered_verdict(
    controller: "AutoresearchController",
    *,
    run_output_dir: Path,
    config: str,
    state: dict[str, Any],
    metric: Any,
    details: dict[str, Any],
    decision: str,
) -> VerdictOutcome:
    """Run the registered-prediction verdict lifecycle for one backtest result.

    Owns the full ordering the caller used to perform by hand: evaluate (with the
    retest baseline override) -> classify inconclusive (force-after-retest vs
    request a retest) -> attach the paid lesson only to terminal verdicts ->
    persist the artifact -> apply the verdict to the causal factor. Returns the
    final verdict, the possibly-updated decision, and any retest request.
    """
    registered_verdict = evaluate_registered_predictions(
        controller,
        run_output_dir,
        metric,
        details,
        baseline_override=(
            retest_baseline_metrics(controller, state, run_output_dir)
            if is_registered_prediction_retest_action(state)
            else None
        ),
    )
    if registered_verdict is None:
        return VerdictOutcome(verdict=None, decision=decision, retest_request=None)

    retest_request: RetestRequested | None = None
    forced_after_retest = False
    if registered_verdict.status == "inconclusive":
        if is_registered_prediction_retest_action(state):
            registered_verdict = force_registered_inconclusive_after_retest(registered_verdict)
            forced_after_retest = True
            decision = "discard"
        else:
            retest_request = request_registered_prediction_retest(controller, state, config=config)
            decision = "retest"
    if retest_request is None and not forced_after_retest:
        # Only terminal verdicts get the paid LLM lesson; a verdict heading to
        # retest would have its lesson overwritten, and a forced-after-retest
        # verdict must keep its audit marker.
        registered_verdict = attach_harvest_lesson(controller, run_output_dir, registered_verdict)
    write_harvest_verdict_artifact(controller, run_output_dir, registered_verdict)
    if registered_verdict.status in {"degenerate", "refuted"}:
        decision = "discard"
    apply_registered_verdict_to_causal_factor(controller, config, registered_verdict)
    return VerdictOutcome(
        verdict=registered_verdict,
        decision=decision,
        retest_request=retest_request,
    )


def _round_root_for_config(controller: "AutoresearchController", config: str) -> Path:
    config_path = resolve_config_path(
        config,
        code_root=controller.root,
        runtime_root=_runtime_root(controller),
        execution_root=_execution_root(controller),
    )
    return config_path.parent


def _selected_thesis_payload(round_root: Path) -> dict[str, Any]:
    path = round_root / "selected_thesis.json"
    if not path.exists():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    return payload if isinstance(payload, dict) else {}


def _runtime_root(controller: "AutoresearchController") -> Path:
    return getattr(controller, "runtime_root", controller.root)


def _execution_root(controller: "AutoresearchController") -> Path:
    ctx = getattr(controller, "ctx", None)
    return getattr(ctx, "execution_root", None) or controller.root


def load_runtime_config_contents(
    controller: "AutoresearchController",
    config: str,
) -> dict[str, Any]:
    config_path = resolve_config_path(
        config,
        code_root=controller.root,
        runtime_root=_runtime_root(controller),
        execution_root=_execution_root(controller),
    )
    if not config_path.exists():
        return {}
    try:
        if config_path.suffix in (".yaml", ".yml"):
            raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        else:
            raw = json.loads(config_path.read_text(encoding="utf-8"))
    except OSError as exc:
        log.error(
            f"CONFIG_READ error config={config}: {exc} "
            f"| hint=the config file exists but cannot be read"
        )
        raise
    if isinstance(raw, dict) and "runtime_config" in raw:
        runtime = raw["runtime_config"]
        return runtime if isinstance(runtime, dict) else {}
    if isinstance(raw, dict):
        return raw
    from strategies import STRATEGIES

    family_name = controller.family.name
    return STRATEGIES[family_name].compile_contract(raw).runtime_config


def _wide_ohlcv_batch_to_long_bars(batch: dict[str, Any]) -> Any:
    import pandas as pd

    required = ("open", "high", "low", "close")
    missing = [name for name in required if name not in batch]
    if missing:
        raise ValueError(f"data universe batch missing OHLC fields: {missing}")
    symbols = set.intersection(*(set(batch[n].columns) for n in required))
    volume = batch.get("volume")
    if volume is not None:
        symbols &= set(volume.columns)

    raw_ts = pd.to_datetime(batch["close"].index)
    timestamps = (
        raw_ts.tz_localize("America/New_York").tz_convert("UTC")
        if raw_ts.tz is None
        else raw_ts.tz_convert("UTC")
    )

    frames: list[pd.DataFrame] = []
    for symbol in sorted(symbols):
        frame = pd.DataFrame(
            {
                "timestamp": timestamps,
                "symbol": symbol,
                "open": batch["open"][symbol].to_numpy(),
                "high": batch["high"][symbol].to_numpy(),
                "low": batch["low"][symbol].to_numpy(),
                "close": batch["close"][symbol].to_numpy(),
            }
        )
        if volume is not None:
            frame["volume"] = volume[symbol].to_numpy()
        frames.append(frame)
    if not frames:
        return pd.DataFrame(
            columns=["timestamp", "symbol", "open", "high", "low", "close", "volume"]
        )
    return pd.concat(frames, ignore_index=True)


def _load_strategy_events(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, str) or not value:
        return []
    path = Path(value)
    if not path.exists():
        return []
    if path.suffix == ".parquet":
        import pandas as pd

        return pd.read_parquet(path).to_dict("records")
    if path.suffix == ".json":
        payload = json.loads(path.read_text(encoding="utf-8"))
        return payload if isinstance(payload, list) else []
    return []


def _flatten_metrics(
    details: dict[str, Any],
    *,
    primary_metric: tuple[str, Any] | None = None,
) -> dict[str, Any]:
    # Merge order is precedence order: validation_metrics must win over train
    # values, ambiguous top-level copies, AND the parsed primary metric,
    # because registered predictions are judged against the validation period.
    metrics: dict[str, Any] = {}
    train_metrics = details.get("train_metrics")
    if isinstance(train_metrics, dict):
        metrics.update(train_metrics)
    nested = details.get("metrics")
    if isinstance(nested, dict):
        metrics.update(nested)
    for key, value in details.items():
        if key not in {"train_metrics", "validation_metrics", "metrics"}:
            metrics[key] = value
    if primary_metric is not None and primary_metric[1] is not None:
        metrics[primary_metric[0]] = primary_metric[1]
    validation_metrics = details.get("validation_metrics")
    if isinstance(validation_metrics, dict):
        metrics.update(validation_metrics)
    return metrics


def _baseline_metrics_from_first_result(controller: "AutoresearchController") -> dict[str, Any]:
    tracker = getattr(controller, "baseline_tracker", None)
    latest_checkpoint = tracker.latest() if tracker is not None else None
    if latest_checkpoint is not None and isinstance(latest_checkpoint.metrics, dict):
        return dict(latest_checkpoint.metrics)

    results = controller.read_results()
    baseline_result = results[0] if results else None
    if not baseline_result:
        return {}
    bta = baseline_result.asi.get("trade_analysis", {})
    out: dict[str, Any] = {}
    for key in (
        "trade_count",
        "profit_factor",
        "max_drawdown",
        "median_expectancy",
        "win_rate",
    ):
        if key in bta:
            out[key] = bta[key]
    return out


def _write_extended_retest_config(
    controller: "AutoresearchController",
    config_path: Path,
) -> tuple[Path, dict[str, Any]]:
    raw = read_config_payload(config_path)
    if not isinstance(raw, dict):
        raise ValueError(f"registered prediction retest config must be a mapping: {config_path}")
    target = raw.get("runtime_config") if isinstance(raw.get("runtime_config"), dict) else raw
    if not isinstance(target, dict):
        raise ValueError(
            f"registered prediction retest runtime_config must be a mapping: {config_path}"
        )
    family_config = _family_base_config(controller)
    validation_end = str(target.get("validation_end") or family_config.get("validation_end") or "")
    if not validation_end:
        raise ValueError(
            "registered prediction retest requires validation_end in selected config "
            f"or configs/{controller.family.name}_base.yaml"
        )
    config_for_tunables = dict(family_config)
    config_for_tunables.update(target)
    months = research_engine_retest_extension_months(config_for_tunables)

    import pandas as pd

    end = pd.Timestamp(validation_end)
    if end.tzinfo is None:
        end = end.tz_localize("UTC")
    else:
        end = end.tz_convert("UTC")
    extended = (end + pd.DateOffset(months=months)).date().isoformat()
    target["validation_end"] = extended
    retest_path = config_path.with_name("selected_config_retest.json")
    write_json_atomic(retest_path, raw)
    return retest_path, {
        "original_config": serialize_artifact_path(config_path, controller.root),
        "original_validation_end": validation_end,
        "validation_end": extended,
        "retest_extension_months": months,
    }


def _family_base_config(controller: "AutoresearchController") -> dict[str, Any]:
    path = controller.root / "configs" / controller.family.base_config_filename
    if not path.exists():
        return {}
    payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return payload if isinstance(payload, dict) else {}
