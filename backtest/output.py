from __future__ import annotations

from pathlib import Path
from typing import Any

from backtest.result_schema import build_result_payload
from persistence_utils import write_json_atomic_strict


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

    events_path = ""
    if event_logger:
        events_path = str(output_path / "strategy_events.parquet")
        event_logger.write_parquet(events_path)

    diagnostics_path = ""
    trade_count = len(trades_df) if trades_df is not None else 0
    strategy_diagnostics = {}
    if event_logger:
        diagnostics_path = str(output_path / "diagnostics.json")
        strategy_diagnostics = event_logger.write_diagnostics(diagnostics_path, trade_count)

    result_payload = build_result_payload(
        strategy,
        config_path,
        config,
        result_payload_input,
        strategy_diagnostics,
        {
            "trades_file": trades_path,
            "strategy_events_file": events_path,
            "diagnostics_file": diagnostics_path,
        },
    )
    result_json_path = output_path / "result.json"
    write_json_atomic_strict(result_json_path, result_payload)

    print(f"RESULT_JSON {result_json_path}")
    if events_path:
        print(f"STRATEGY_EVENTS_FILE {events_path}")
    if diagnostics_path:
        print(f"DIAGNOSTICS_FILE {diagnostics_path}")
    return result_payload
