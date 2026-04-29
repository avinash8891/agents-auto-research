from __future__ import annotations

import os
from dataclasses import dataclass

from family_research import get_family_research_spec

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

    def benchmark_command(self, config_path: str, output_dir: str | None = None) -> str:
        script = self.vps_benchmark_script if IS_VPS and self.vps_benchmark_script else self.benchmark_script
        # On VPS, use venv python to ensure all deps are available
        python = "./venv/bin/python3" if IS_VPS else "python3"
        if script.endswith(".py"):
            # vps_runner.py uses positional arg; backtest_5ema.py uses --config
            if "backtest" in script:
                cmd = f"{python} {script} --config {config_path}"
                if output_dir:
                    cmd += f" --output-dir {output_dir}"
                return cmd
            return f"{python} {script} {config_path}"
        return f"./{script} {config_path}"

    @property
    def research_spec(self):
        return get_family_research_spec(self.name)


FAMILIES: dict[str, StrategyFamily] = {
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
        discord_webhook="https://discord.com/api/webhooks/1498190524606316725/8te44ljBeskS2jdlEEEAy5mvORW5IaiOhsH6-3fjfhh6xf-HvncCa86MfBLehXKwWX3R",
    ),
    "ema": StrategyFamily(
        name="ema",
        benchmark_script="backtest_5ema.py",
        vps_benchmark_script="backtest_5ema.py",
        proposals_dirname="ema-proposals",
        compilations_dirname="ema-compilations",
        contracts_dirname="ema-contracts",
        run_queue_dirname="ema-run-queue",
        research_dirname="ema-research",
        builder_requests_dirname="ema-builder-requests",
        base_config_filename="ema_base.yaml",
        runs_dirname="ema_autoresearch-runs",
        discord_webhook="https://discord.com/api/webhooks/1498191248144465960/FkBLmVdPQtBuXXJE63xuZqJ4JemtWflgWE3ioGT_Dtb1yb_8hlBe1XY54PRRL5GE9LD7",
    ),
}


def load_family(name: str = "orb") -> StrategyFamily:
    try:
        return FAMILIES[name]
    except KeyError as exc:
        raise ValueError(f"Unknown strategy family: {name}") from exc
