from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd

from metrics import empty_metrics
from strategies._demo.prompt import DESCRIPTION_FOR_RESEARCH
from strategies._demo.research import DEMO_RESEARCH_SPEC
from strategies.base import BaseStrategy
from strategy_event_logger import StrategyEventLogger


class DemoCompilationResult:
    def __init__(self, runtime_config: dict[str, Any]) -> None:
        self.status = "ready_to_run"
        self.runtime_config = runtime_config
        self.missing_primitives: list[str] = []
        self.normalized_contract: list[dict[str, Any]] = []


class DemoStrategy(BaseStrategy):
    name = "_demo"
    description_for_research = DESCRIPTION_FOR_RESEARCH
    research_spec = DEMO_RESEARCH_SPEC
    requires_data_universe = False

    def run(self, config: dict[str, Any]) -> dict[str, Any]:
        result = empty_metrics()
        result["_trades_df"] = pd.DataFrame(
            columns=[
                "entry_date",
                "exit_date",
                "direction",
                "entry_price",
                "exit_price",
                "stop",
                "target",
                "pnl_pct",
                "exit_reason",
                "symbol",
            ]
        )
        result["_event_logger"] = StrategyEventLogger()
        return result

    def validate_runtime_config_scope(
        self, config: dict[str, Any], source_path: Path | None = None
    ) -> dict[str, Any]:
        missing = [key for key in ("validation_start", "validation_end") if not config.get(key)]
        if missing and not config.get("allow_unbounded_research_backtest"):
            source = f" for {source_path}" if source_path is not None else ""
            raise ValueError(
                "Refusing unbounded _demo backtest"
                f"{source}: missing {', '.join(missing)}. "
                "Set validation_start and validation_end, or explicitly set "
                "allow_unbounded_research_backtest=true."
            )
        return config

    def validate_runtime_config(self, config: dict[str, Any]) -> list[str]:
        return []

    def compile_contract(self, contract: list[dict[str, Any]]) -> DemoCompilationResult:
        return DemoCompilationResult(self.get_defaults())

    def map_config_changes_to_contract(
        self, config_changes: dict[str, Any]
    ) -> list[dict[str, Any]]:
        return []
