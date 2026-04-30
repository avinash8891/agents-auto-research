from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol

import yaml

from family_research import FamilyResearchSpec

if TYPE_CHECKING:
    from strategies.ema.contract import CompilationResult


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
    extra_result_fields: tuple[str, ...]
    description_for_research: str
    research_spec: FamilyResearchSpec
    discord_webhook: str

    def run(self, config: dict[str, Any]) -> dict[str, Any]: ...
    def get_defaults(self) -> dict[str, Any]: ...
    def validate_runtime_config_scope(
        self, config: dict[str, Any], source_path: Path | None = None
    ) -> dict[str, Any]: ...
    def validate_runtime_config(self, config: dict[str, Any]) -> list[str]: ...
    def compile_contract(self, contract: list[dict[str, Any]]) -> CompilationResult: ...
    def map_config_changes_to_contract(
        self, config_changes: dict[str, Any]
    ) -> list[dict[str, Any]]: ...
    @property
    def family_dirnames(self) -> FamilyDirnames: ...


class BaseStrategy:
    name = ""
    extra_result_fields: tuple[str, ...] = ()
    description_for_research = ""
    research_spec: FamilyResearchSpec

    @property
    def family_dirnames(self) -> FamilyDirnames:
        return FamilyDirnames(
            proposals=f"{self.name}-proposals",
            compilations=f"{self.name}-compilations",
            contracts=f"{self.name}-contracts",
            run_queue=f"{self.name}-run-queue",
            research=f"{self.name}-research",
            builder_requests=f"{self.name}-builder-requests",
            base_config_filename=f"{self.name}_base.yaml",
            runs=f"{self.name}_autoresearch-runs",
            variant_prefix=f"{self.name}_",
        )

    @property
    def discord_webhook(self) -> str:
        return os.environ.get(f"AUTORESEARCH_DISCORD_WEBHOOK_{self.name.upper()}", "")

    def get_defaults(self) -> dict[str, Any]:
        path = Path(__file__).parent / self.name / "defaults.yaml"
        return yaml.safe_load(path.read_text())


STRATEGIES: dict[str, Strategy] = {}


def register(name: str):
    def deco(cls):
        STRATEGIES[name] = cls()
        return cls

    return deco
