from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backtest.output import write_all
from backtest.runtime_config import load_runtime_config
from strategies import STRATEGIES


def run_strategy(strategy_name: str, config: dict[str, Any]) -> dict[str, Any]:
    try:
        strategy = STRATEGIES[strategy_name]
    except KeyError as exc:
        available = ", ".join(sorted(STRATEGIES))
        raise ValueError(
            f"Unknown strategy '{strategy_name}'. Available strategies: {available}"
        ) from exc
    return strategy.run(config)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Generic backtest runner")
    parser.add_argument("--strategy", required=True, choices=sorted(STRATEGIES))
    parser.add_argument("--config", required=True)
    parser.add_argument(
        "--output-dir", default=".", help="Directory to write result.json and trades CSV"
    )
    return parser


def main() -> None:
    args = build_parser().parse_args()
    config = load_runtime_config(args.config, args.strategy)
    result = run_strategy(args.strategy, config)
    write_all(
        result,
        config,
        args.output_dir,
        strategy=args.strategy,
        config_path=args.config,
    )


if __name__ == "__main__":
    main()
