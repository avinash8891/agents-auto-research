from __future__ import annotations

import argparse
import os
from pathlib import Path

from strategies import STRATEGIES


def strategy_for_script(script_name: str) -> str:
    matches = [
        name for name, strategy in STRATEGIES.items() if strategy.benchmark_script == script_name
    ]
    if len(matches) != 1:
        available = ", ".join(sorted(STRATEGIES))
        raise ValueError(
            f"Cannot resolve strategy for script {script_name!r}; "
            f"matching benchmark_script entries={matches}; available={available}"
        )
    return matches[0]


def main_for_script(script_path: str) -> None:
    parser = argparse.ArgumentParser(description="Strategy backtest compatibility runner")
    parser.add_argument("--config", required=True)
    parser.add_argument(
        "--output-dir", default="/tmp", help="Directory to write result.json and trades CSV"
    )
    args = parser.parse_args()
    os.execvp(
        "python3",
        [
            "python3",
            "-m",
            "backtest.runner",
            "--strategy",
            strategy_for_script(Path(script_path).name),
            "--config",
            args.config,
            "--output-dir",
            args.output_dir,
        ],
    )
