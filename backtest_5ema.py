#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os


def main() -> None:
    parser = argparse.ArgumentParser(description="5 EMA Backtest Runner")
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
            "ema",
            "--config",
            args.config,
            "--output-dir",
            args.output_dir,
        ],
    )


if __name__ == "__main__":
    main()
