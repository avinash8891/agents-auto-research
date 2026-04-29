#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

CONFIG_PATH="${1:?config path required}"

if [ ! -f "$CONFIG_PATH" ]; then
  echo "Config not found: $CONFIG_PATH"
  exit 1
fi

# Pre-check: syntax validation
python3 -c "import ast; ast.parse(open('ema_signals.py').read()); ast.parse(open('backtest_5ema.py').read()); ast.parse(open('ema_contract.py').read())" 2>&1 || {
  echo 'SYNTAX ERROR'
  exit 1
}

scp -q \
  backtest_5ema.py \
  ema_signals.py \
  ema_exits.py \
  ema_contract.py \
  agent_orchestrator.py \
  data_loader.py \
  metrics.py \
  numba_kernels.py \
  "$CONFIG_PATH" \
  root@31.97.60.116:/root/orb-research/ >/dev/null 2>&1 || true

ssh -o StrictHostKeyChecking=no root@31.97.60.116 \
  "cd /root/orb-research && mkdir -p \"$(dirname "$CONFIG_PATH")\" && cp \"$(basename "$CONFIG_PATH")\" \"$CONFIG_PATH\" 2>/dev/null || true && find . -name __pycache__ -type d -exec rm -rf {} + 2>/dev/null || true && python3 backtest_5ema.py --config \"$CONFIG_PATH\"" 2>&1
