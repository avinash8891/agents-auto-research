from __future__ import annotations

import os
import sys
import sysconfig
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

import yaml

from family_research_spec import FamilyResearchSpec
from strategies.contract import (
    BacktestSemanticsContract,
    backtest_semantics_for_family,
    validate_backtest_runtime_config,
)


@dataclass(frozen=True)
class FamilyDirnames:
    proposals: str
    compilations: str
    contracts: str
    run_queue: str
    builder_requests: str
    base_config_filename: str
    runs: str
    variant_prefix: str


class Strategy(Protocol):
    name: str
    benchmark_script: str
    description_for_research: str
    research_spec: FamilyResearchSpec
    discord_webhook: str
    default_variants: tuple[str, ...]
    thesis_family_by_slug: dict[str, str]
    combination_rules: dict[tuple[str, str], str]
    requires_data_universe: bool

    def run(self, config: dict[str, Any]) -> dict[str, Any]: ...
    def get_defaults(self) -> dict[str, Any]: ...
    def validate_runtime_config_scope(
        self, config: dict[str, Any], source_path: Path | None = None
    ) -> dict[str, Any]: ...
    def validate_runtime_config(self, config: dict[str, Any]) -> list[str]: ...
    def compile_contract(self, contract: list[dict[str, Any]]) -> Any: ...
    def render_contract_to_runtime_config(
        self, contract: list[dict[str, Any]]
    ) -> dict[str, Any]: ...
    def resolve_contract_support(self, contract: list[dict[str, Any]]) -> dict[str, Any]: ...
    def map_config_changes_to_contract(
        self, config_changes: dict[str, Any]
    ) -> list[dict[str, Any]]: ...
    @property
    def family_dirnames(self) -> FamilyDirnames: ...
    @property
    def backtest_contract(self) -> BacktestSemanticsContract: ...


class BaseStrategy:
    name = ""
    benchmark_script = ""
    description_for_research = ""
    default_variants: tuple[str, ...] = ()
    thesis_family_by_slug: dict[str, str] = {}
    combination_rules: dict[tuple[str, str], str] = {}
    requires_data_universe = True
    research_spec: FamilyResearchSpec

    @property
    def family_dirnames(self) -> FamilyDirnames:
        return FamilyDirnames(
            proposals=f"{self.name}-proposals",
            compilations=f"{self.name}-compilations",
            contracts=f"{self.name}-contracts",
            run_queue=f"{self.name}-run-queue",
            builder_requests=f"{self.name}-builder-requests",
            base_config_filename=f"{self.name}_base.yaml",
            runs=f"{self.name}_autoresearch-runs",
            variant_prefix=f"{self.name}_",
        )

    @property
    def backtest_contract(self) -> BacktestSemanticsContract:
        return backtest_semantics_for_family(self.name)

    def validate_runtime_config_scope(
        self, config: dict[str, Any], source_path: Path | None = None
    ) -> dict[str, Any]:
        return validate_backtest_runtime_config(self.name, config, source_path)

    @property
    def discord_webhook(self) -> str:
        return os.environ.get(f"AUTORESEARCH_DISCORD_WEBHOOK_{self.name.upper()}", "")

    def get_defaults(self) -> dict[str, Any]:
        return load_strategy_defaults(self.name, self.family_dirnames.base_config_filename)

    def render_contract_to_runtime_config(self, contract: list[dict[str, Any]]) -> dict[str, Any]:
        compilation = self.compile_contract(contract)  # type: ignore[attr-defined]
        return compilation.runtime_config

    def resolve_contract_support(self, contract: list[dict[str, Any]]) -> dict[str, Any]:
        compilation = self.compile_contract(contract)  # type: ignore[attr-defined]
        return {
            "supported": compilation.status == "ready_to_run",
            "missing_primitive_types": compilation.missing_primitives,
        }


STRATEGIES: dict[str, Strategy] = {}


def load_strategy_defaults(name: str, base_config_filename: str | None = None) -> dict[str, Any]:
    filename = base_config_filename or f"{name}_base.yaml"
    candidate_paths = [
        Path(__file__).resolve().parents[1] / "configs" / filename,
        Path(sysconfig.get_paths()["data"]) / "configs" / filename,
        Path(sys.prefix) / "configs" / filename,
    ]
    for path in candidate_paths:
        if path.exists():
            return yaml.safe_load(path.read_text())
    searched = ", ".join(str(path) for path in candidate_paths)
    raise FileNotFoundError(f"Could not load strategy defaults for {name}: searched {searched}")


def register(name: str):
    def deco(cls):
        STRATEGIES[name] = cls()
        return cls

    return deco
