#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

CONFIG_PATH="${1:?config path required}"

if [ ! -f "$CONFIG_PATH" ]; then
  echo "Config not found: $CONFIG_PATH"
  exit 1
fi

python3 - <<'PYEOF'
import ast
from pathlib import Path

CHECKS = [
    Path("backtest_5ema.py"),
    Path("backtest/runner.py"),
    Path("backtest/runtime_config.py"),
    Path("strategies/base.py"),
    Path("strategies/ema/strategy.py"),
    Path("strategies/ema/signals.py"),
    Path("strategies/ema/contract.py"),
]
for path in CHECKS:
    ast.parse(path.read_text())
PYEOF

python3 - <<'PYEOF'
import os
from pathlib import Path

LOCAL = Path('.').resolve()
SYNC_DIRS = ['backtest', 'strategies']
SYNC_FILES = [
    'backtest_5ema.py',
    'config_hash.py',
    'agent_orchestrator.py',
    'data_loader.py',
    'metrics.py',
    'numba_kernels.py',
    'strategy_event_logger.py',
    'trace_logger.py',
    'configs/ema_base.yaml',
]
paths = []
for dirname in SYNC_DIRS:
    base = LOCAL / dirname
    if base.exists():
        for path in sorted(base.rglob('*')):
            if path.is_file() and path.suffix in {'.py', '.yaml'}:
                paths.append(path)
for rel in SYNC_FILES:
    path = LOCAL / rel
    if path.exists():
        paths.append(path)
seen = set()
for path in paths:
    rel = path.relative_to(LOCAL).as_posix()
    if rel in seen:
        continue
    seen.add(rel)
    print(rel)
PYEOF | while read -r rel; do
  scp -q "$rel" root@31.97.60.116:/root/orb-research/"$rel" >/dev/null 2>&1 || true
done

scp -q "$CONFIG_PATH" root@31.97.60.116:/root/orb-research/ >/dev/null 2>&1 || true

ssh -o StrictHostKeyChecking=no root@31.97.60.116   "cd /root/orb-research && mkdir -p "$(dirname "$CONFIG_PATH")" && cp "$(basename "$CONFIG_PATH")" "$CONFIG_PATH" 2>/dev/null || true && find . -name __pycache__ -type d -exec rm -rf {} + 2>/dev/null || true && python3 backtest_5ema.py --config "$CONFIG_PATH"" 2>&1
