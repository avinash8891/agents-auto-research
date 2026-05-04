from __future__ import annotations

import os
import sys
import sysconfig
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

import yaml

from family_research_spec import FamilyResearchSpec


@dataclass(frozen=True)
class FamilyDirnames:
    proposals: str
    compilations: str
    contracts: str
    run_queue: str
    research: str
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


class BaseStrategy:
    name = ""
    benchmark_script = ""
    description_for_research = ""
    default_variants: tuple[str, ...] = ()
    thesis_family_by_slug: dict[str, str] = {}
    combination_rules: dict[tuple[str, str], str] = {}
    requires_data_universe = True
    research_spec: FamilyResearchSpec
    # Override to change the research directory suffix (e.g. "research-artifacts").
    _research_dirname_suffix: str = "research"

    @property
    def family_dirnames(self) -> FamilyDirnames:
        return FamilyDirnames(
            proposals=f"{self.name}-proposals",
            compilations=f"{self.name}-compilations",
            contracts=f"{self.name}-contracts",
            run_queue=f"{self.name}-run-queue",
            research=f"{self.name}-{self._research_dirname_suffix}",
            builder_requests=f"{self.name}-builder-requests",
            base_config_filename=f"{self.name}_base.yaml",
            runs=f"{self.name}_autoresearch-runs",
            variant_prefix=f"{self.name}_",
        )

    def validate_runtime_config_scope(
        self, config: dict[str, Any], source_path: Path | None = None
    ) -> dict[str, Any]:
        if config.get("allow_unbounded_research_backtest"):
            return config
        missing = [key for key in ("validation_start", "validation_end") if not config.get(key)]
        if missing:
            source = f" for {source_path}" if source_path is not None else ""
            raise ValueError(
                f"Refusing unbounded {self.name.upper()} backtest"
                f"{source}: missing {', '.join(missing)}. "
                "Set validation_start and validation_end, or explicitly set "
                "allow_unbounded_research_backtest=true."
            )
        return config

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
