from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache

from family_research import get_family_research_spec
from strategies import STRATEGIES

# When running on VPS, set AUTORESEARCH_VPS=1 to use direct backtest instead of vps_runner
IS_VPS = os.environ.get("AUTORESEARCH_VPS", "") == "1"


@dataclass(frozen=True)
class StrategyFamily:
    name: str
    benchmark_script: str
    vps_benchmark_script: str = ""  # direct script when running on VPS
    proposals_dirname: str = "proposals"
    compilations_dirname: str = "compilations"
    contracts_dirname: str = "contracts"
    run_queue_dirname: str = "run-queue"
    research_dirname: str = "research"
    builder_requests_dirname: str = "builder-requests"
    base_config_filename: str = "orb_base.yaml"
    runs_dirname: str = "autoresearch-runs"
    discord_webhook: str = ""
    # Family-aware variant config conventions. Variant config files live
    # at `configs/variants/{variant_prefix}{slug}.yaml`. Default variants
    # are the seed list of well-known thesis paths checked at loop start.
    # Pre-rule-PR-5, these were hardcoded as `orb_*.yaml` in the planner;
    # now they derive from the family. See planning.list_known_variant_configs.
    variant_prefix: str = "orb_"
    default_variants: tuple[str, ...] = ()

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
        script = (
            self.vps_benchmark_script
            if IS_VPS and self.vps_benchmark_script
            else self.benchmark_script
        )
        # On VPS, use venv python to ensure all deps are available
        python = "./venv/bin/python3" if IS_VPS else "python3"
        if script.endswith(".py"):
            if script == "vps_runner.py":
                return f"{python} {script} {config_path}"
            if script.startswith("backtest"):
                cmd = f"{python} {script} --config {config_path}"
                if output_dir:
                    cmd += f" --output-dir {output_dir}"
                return cmd
            return f"{python} {script} {config_path}"
        return f"./{script} {config_path}"

    @property
    def research_spec(self):
        return get_family_research_spec(self.name)


def _discord_webhook_for(family_name: str) -> str:
    """Read the Discord webhook URL for a family from the environment.

    Variable name convention: AUTORESEARCH_DISCORD_WEBHOOK_<FAMILY_UPPER>.
    Returns "" if unset, which makes notify_discord a no-op (rule 2:
    secrets must never live in source).
    """
    return os.environ.get(f"AUTORESEARCH_DISCORD_WEBHOOK_{family_name.upper()}", "")


@lru_cache(maxsize=1)
def _families() -> dict[str, StrategyFamily]:
    families = {
        "orb": StrategyFamily(
            name="orb",
            benchmark_script="backtest_orb_v2.py",
            vps_benchmark_script="backtest_orb_v2.py",
            proposals_dirname="orb-proposals",
            compilations_dirname="orb-compilations",
            contracts_dirname="orb-contracts",
            run_queue_dirname="orb-run-queue",
            research_dirname="orb-research-artifacts",
            builder_requests_dirname="orb-builder-requests",
            base_config_filename="orb_base.yaml",
            runs_dirname="orb_autoresearch-runs",
            discord_webhook=_discord_webhook_for("orb"),
            variant_prefix="orb_",
            default_variants=(
                "configs/variants/orb_spy_only.yaml",
                "configs/variants/orb_stocks_in_play.yaml",
                "configs/variants/orb_trailing_stop.yaml",
                "configs/variants/orb_trend_filter.yaml",
            ),
        )
    }
    for name, strategy in STRATEGIES.items():
        if name == "orb":
            continue
        families[name] = StrategyFamily(
            name=name,
            benchmark_script=f"backtest_{'5ema' if name == 'ema' else name}.py",
            vps_benchmark_script="vps_runner.py" if name == "ema" else f"backtest_{name}.py",
            proposals_dirname=strategy.family_dirnames.proposals,
            compilations_dirname=strategy.family_dirnames.compilations,
            contracts_dirname=strategy.family_dirnames.contracts,
            run_queue_dirname=strategy.family_dirnames.run_queue,
            research_dirname=strategy.family_dirnames.research,
            builder_requests_dirname=strategy.family_dirnames.builder_requests,
            base_config_filename=strategy.family_dirnames.base_config_filename,
            runs_dirname=strategy.family_dirnames.runs,
            discord_webhook=_discord_webhook_for(name),
            variant_prefix=strategy.family_dirnames.variant_prefix,
            default_variants=(),
        )
    return families


def load_family(name: str = "orb") -> StrategyFamily:
    try:
        return _families()[name]
    except KeyError as exc:
        raise ValueError(f"Unknown strategy family: {name}") from exc
