from __future__ import annotations

from typing import Any

from strategies.base import BaseStrategy
from strategies.orb.contract import (
    compile_contract,
    render_contract_to_runtime_config,
    resolve_contract_support,
)
from strategies.orb.defaults import get_orb_defaults
from strategies.orb.prompt import DESCRIPTION_FOR_RESEARCH
from strategies.orb.research import ORB_RESEARCH_SPEC
from strategies.orb.runner import run_backtest
from strategies.orb.validate import validate_orb_runtime_config


class ORBStrategy(BaseStrategy):
    name = "orb"
    benchmark_script = "backtest_orb_v2.py"
    description_for_research = DESCRIPTION_FOR_RESEARCH
    research_spec = ORB_RESEARCH_SPEC
    default_variants = (
        "configs/variants/orb_spy_only.yaml",
        "configs/variants/orb_stocks_in_play.yaml",
        "configs/variants/orb_trailing_stop.yaml",
        "configs/variants/orb_trend_filter.yaml",
    )
    thesis_family_by_slug = {
        "spy_only": "universe",
        "stocks_in_play": "universe",
        "stocks_in_play_universe": "universe",
        "relative_volume_stocks_in_play": "universe",
        "top_10_dollar_volume_stocks_in_play": "universe",
        "high_relative_volume_filter": "entry",
        "trend_alignment_filter": "entry",
        "follow_through": "entry",
        "trailing_stop": "exit",
        "time_stop": "exit",
        "failed_breakout_exit": "exit",
        "volatility_trail": "exit",
        "trend_filter": "regime",
        "skip_chop": "regime",
        "skip_low_vol": "regime",
        "trend_day_only": "regime",
    }
    combination_rules = {
        ("universe", "exit"): "allowed",
        ("universe", "entry"): "allowed",
        ("universe", "regime"): "allowed",
        ("entry", "exit"): "allowed",
        ("entry", "regime"): "allowed",
        ("exit", "regime"): "allowed",
        ("universe", "universe"): "disallowed",
        ("entry", "entry"): "disallowed",
        ("exit", "exit"): "review_required",
        ("regime", "regime"): "review_required",
    }

    def run(self, config: dict[str, Any]) -> dict[str, Any]:
        return run_backtest(config)

    def get_defaults(self) -> dict[str, Any]:
        return get_orb_defaults()

    def validate_runtime_config(self, config: dict[str, Any]) -> list[str]:
        return validate_orb_runtime_config(config)

    def compile_contract(self, contract: list[dict[str, Any]]):
        return compile_contract(contract)

    def render_contract_to_runtime_config(self, contract: list[dict[str, Any]]) -> dict[str, Any]:
        return render_contract_to_runtime_config(contract)

    def resolve_contract_support(self, contract: list[dict[str, Any]]) -> dict[str, Any]:
        return resolve_contract_support(contract)

    def map_config_changes_to_contract(
        self, config_changes: dict[str, Any]
    ) -> list[dict[str, Any]]:
        return [{"type": "config_changes_passthrough", "config_changes": config_changes}]
