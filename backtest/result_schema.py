from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from config_hash import _config_hash, _git_sha

METRIC_KEYS = (
    "median_expectancy",
    "trade_count",
    "profit_factor",
    "max_drawdown",
    "pct_profitable_windows",
    "avg_sharpe_across_windows",
)


def build_result_payload(
    strategy: str,
    config_path: str,
    config: dict[str, Any],
    result: dict[str, Any],
    strategy_diagnostics: dict[str, Any],
    file_paths: dict[str, str],
) -> dict[str, Any]:
    return {
        "family": strategy,
        "config": config_path,
        "config_hash": _config_hash(config),
        "git_sha": _git_sha(),
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "metrics": {key: result[key] for key in METRIC_KEYS},
        "diagnostics": result.get("diagnostics", {}),
        "strategy_diagnostics": strategy_diagnostics,
        "trades_file": file_paths["trades_file"],
        "strategy_events_file": file_paths["strategy_events_file"],
        "diagnostics_file": file_paths["diagnostics_file"],
    }
