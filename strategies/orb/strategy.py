from __future__ import annotations

from pathlib import Path
from typing import Any

from strategies.base import BaseStrategy, FamilyDirnames
from strategies.orb.contract import (
    compile_contract,
    render_contract_to_runtime_config,
    resolve_contract_support,
)
from strategies.orb.defaults import _get_orb_defaults
from strategies.orb.prompt import DESCRIPTION_FOR_RESEARCH
from strategies.orb.research import ORB_RESEARCH_SPEC
from strategies.orb.runner import run_backtest
from strategies.orb.validate import validate_orb_runtime_config


class ORBStrategy(BaseStrategy):
    name = "orb"
    benchmark_script = "backtest_orb_v2.py"
    vps_benchmark_script = "backtest_orb_v2.py"
    extra_result_fields = ("regime_expectancy",)
    description_for_research = DESCRIPTION_FOR_RESEARCH
    research_spec = ORB_RESEARCH_SPEC
    default_variants = (
        "configs/variants/orb_spy_only.yaml",
        "configs/variants/orb_stocks_in_play.yaml",
        "configs/variants/orb_trailing_stop.yaml",
        "configs/variants/orb_trend_filter.yaml",
    )

    @property
    def family_dirnames(self) -> FamilyDirnames:
        return FamilyDirnames(
            proposals="orb-proposals",
            compilations="orb-compilations",
            contracts="orb-contracts",
            run_queue="orb-run-queue",
            research="orb-research-artifacts",
            builder_requests="orb-builder-requests",
            base_config_filename="orb_base.yaml",
            runs="orb_autoresearch-runs",
            variant_prefix="orb_",
        )

    def run(self, config: dict[str, Any]) -> dict[str, Any]:
        return run_backtest(config)

    def get_defaults(self) -> dict[str, Any]:
        return _get_orb_defaults()

    def validate_runtime_config_scope(
        self, config: dict[str, Any], source_path: Path | None = None
    ) -> dict[str, Any]:
        missing = [key for key in ("validation_start", "validation_end") if not config.get(key)]
        if missing and not config.get("allow_unbounded_research_backtest"):
            source = f" for {source_path}" if source_path is not None else ""
            raise ValueError(
                "Refusing unbounded ORB backtest"
                f"{source}: missing {', '.join(missing)}. "
                "Set validation_start and validation_end, or explicitly set "
                "allow_unbounded_research_backtest=true."
            )
        return config

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
