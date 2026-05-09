from __future__ import annotations

import os
import shlex
import sys
from dataclasses import dataclass
from functools import lru_cache

from strategies import STRATEGIES


@dataclass(frozen=True)
class StrategyFamily:
    name: str
    benchmark_script: str
    description_for_research: str = ""
    proposals_dirname: str = "proposals"
    compilations_dirname: str = "compilations"
    contracts_dirname: str = "contracts"
    run_queue_dirname: str = "run-queue"
    builder_requests_dirname: str = "builder-requests"
    base_config_filename: str = "base.yaml"
    discord_webhook: str = ""
    # Family-aware variant config conventions. Variant config files live
    # at `configs/variants/{variant_prefix}{slug}.yaml`. Default variants
    # are the seed list of well-known thesis paths checked at loop start.
    # Pre-rule-PR-5, these were hardcoded as strategy-specific filenames in
    # the planner; now they derive from the registered strategy metadata.
    variant_prefix: str = ""
    default_variants: tuple[str, ...] = ()
    thesis_family_by_slug: dict[str, str] | None = None
    combination_rules: dict[tuple[str, str], str] | None = None

    @property
    def baseline_config_path(self) -> str:
        """`configs/{base_config_filename}` — the baseline thesis path
        that the loop compares against when computing kept/discarded."""
        return f"configs/{self.base_config_filename}"

    def variant_config_path(self, slug: str) -> str:
        """Build a `configs/variants/{prefix}{slug}.yaml` path."""
        return f"configs/variants/{self.variant_prefix}{slug}.yaml"

    def slug_from_config(self, config_path: str) -> str:
        """Strip the family's variant prefix from a config-path stem.
        Returns the slug with the prefix removed if present, or the bare
        stem otherwise."""
        from pathlib import Path as _Path

        return _Path(config_path).stem.removeprefix(self.variant_prefix)

    def benchmark_command(self, config_path: str, output_dir: str | None = None) -> str:
        config_path_str = str(config_path)
        python_bin = os.environ.get("AUTORESEARCH_PYTHON_BIN", sys.executable)
        cmd = (
            f"{shlex.quote(python_bin)} -m backtest.runner --strategy {shlex.quote(self.name)} "
            f"--config {shlex.quote(config_path_str)}"
        )
        if output_dir:
            cmd += f" --output-dir {shlex.quote(str(output_dir))}"
        return cmd

    @property
    def research_spec(self):
        return STRATEGIES[self.name].research_spec


def _discord_webhook_for(family_name: str) -> str:
    """Read the Discord webhook URL for a family from the environment.

    Variable name convention: AUTORESEARCH_DISCORD_WEBHOOK_<FAMILY_UPPER>.
    Returns "" if unset, which makes notify_discord a no-op (rule 2:
    secrets must never live in source).
    """
    return os.environ.get(f"AUTORESEARCH_DISCORD_WEBHOOK_{family_name.upper()}", "")


@lru_cache(maxsize=1)
def _families() -> dict[str, StrategyFamily]:
    families: dict[str, StrategyFamily] = {}
    for name, strategy in STRATEGIES.items():
        families[name] = StrategyFamily(
            name=name,
            benchmark_script=strategy.benchmark_script or f"backtest_{name}.py",
            description_for_research=strategy.description_for_research,
            proposals_dirname=strategy.family_dirnames.proposals,
            compilations_dirname=strategy.family_dirnames.compilations,
            contracts_dirname=strategy.family_dirnames.contracts,
            run_queue_dirname=strategy.family_dirnames.run_queue,
            builder_requests_dirname=strategy.family_dirnames.builder_requests,
            base_config_filename=strategy.family_dirnames.base_config_filename,
            discord_webhook=_discord_webhook_for(name),
            variant_prefix=strategy.family_dirnames.variant_prefix,
            default_variants=strategy.default_variants,
            thesis_family_by_slug=dict(strategy.thesis_family_by_slug),
            combination_rules=dict(strategy.combination_rules),
        )
    return families


def load_family(name: str) -> StrategyFamily:
    try:
        return _families()[name]
    except KeyError as exc:
        raise ValueError(f"Unknown strategy family: {name}") from exc
