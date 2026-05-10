from __future__ import annotations

from pathlib import Path
from typing import Any

from backtest.event_diagnostics import validate_event_frame_schema, write_event_diagnostics
from backtest.result_schema import build_result_payload
from persistence_utils import write_json_atomic


def write_all(
    result: dict[str, Any],
    config: dict[str, Any],
    output_dir: str | Path,
    *,
    strategy: str,
    config_path: str,
) -> dict[str, Any]:
    result_payload_input = dict(result)
    trades_df = result_payload_input.pop("_trades_df", None)
    event_logger = result_payload_input.pop("_event_logger", None)

    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    trades_path = ""
    if trades_df is not None and not trades_df.empty:
        trades_path = str(output_path / "trades.csv")
        trades_df.to_csv(trades_path, index=False)

    metrics_path = output_path / "metrics.json"
    write_json_atomic(metrics_path, result_payload_input)

    events_df = None
    events_path = ""
    if event_logger:
        events_df = event_logger.to_dataframe()
        validate_event_frame_schema(events_df)
        if events_df is not None and not events_df.empty:
            events_path = output_path / "strategy_events.parquet"
            events_df.to_parquet(events_path, index=False)

    diagnostics_path = ""
    trade_count = len(trades_df) if trades_df is not None else 0
    if event_logger:
        diagnostics_path = output_path / "diagnostics.json"
        write_event_diagnostics(
            diagnostics_path,
            trade_count=trade_count,
            event_frame=events_df,
            event_counts=event_logger.event_counts_snapshot(),
            rejection_breakdown=event_logger.rejection_breakdown_snapshot(),
        )

    result_payload = build_result_payload(
        strategy,
        config_path,
        config,
        {
            "metrics_file": str(metrics_path),
            "trades_file": trades_path,
            "strategy_events_file": str(events_path) if events_path else "",
            "diagnostics_file": str(diagnostics_path) if diagnostics_path else "",
        },
    )
    result_json_path = output_path / "result.json"
    write_json_atomic(result_json_path, result_payload)

    print(f"RESULT_JSON {result_json_path}")
    print(f"METRICS_FILE {metrics_path}")
    if events_path:
        print(f"STRATEGY_EVENTS_FILE {events_path}")
    if diagnostics_path:
        print(f"DIAGNOSTICS_FILE {diagnostics_path}")
    return result_payload
