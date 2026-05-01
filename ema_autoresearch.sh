#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

FAMILY="${AUTORESEARCH_FAMILY:-${2:-ema}}"
CONFIG_PATH="${1:?config path required}"
REMOTE_HOST="${AUTORESEARCH_VPS_HOST:?AUTORESEARCH_VPS_HOST required}"
REMOTE_USER="${AUTORESEARCH_VPS_USER:?AUTORESEARCH_VPS_USER required}"
REMOTE_DIR="${AUTORESEARCH_VPS_DIR:?AUTORESEARCH_VPS_DIR required}"

if [ ! -f "$CONFIG_PATH" ]; then
  echo "Config not found: $CONFIG_PATH"
  exit 1
fi

python3 - <<'PYEOF'
import ast
from pathlib import Path

for path in [Path("backtest/runner.py"), Path("backtest/runtime_config.py"), *Path("strategies").rglob("*.py")]:
    ast.parse(path.read_text())
PYEOF

python3 - <<'PYEOF' | while read -r rel; do
from pathlib import Path

local = Path(".").resolve()
paths = []
for dirname in ["backtest", "strategies", "configs"]:
    base = local / dirname
    if base.exists():
        for path in sorted(base.rglob("*")):
            if path.is_file() and path.suffix in {".py", ".yaml", ".yml"}:
                paths.append(path)
for rel in [
    "data_loader.py",
    "metrics.py",
    "numba_kernels.py",
    "strategy_event_logger.py",
    "trace_sdk.py",
]:
    path = local / rel
    if path.exists():
        paths.append(path)
seen = set()
for path in paths:
    rel = path.relative_to(local).as_posix()
    if rel not in seen:
        seen.add(rel)
        print(rel)
PYEOF
  ssh -o StrictHostKeyChecking=no "$REMOTE_USER@$REMOTE_HOST" "mkdir -p '$REMOTE_DIR/$(dirname "$rel")'"
  scp -q "$rel" "$REMOTE_USER@$REMOTE_HOST:$REMOTE_DIR/$rel"
done

scp -q "$CONFIG_PATH" "$REMOTE_USER@$REMOTE_HOST:$REMOTE_DIR/$(basename "$CONFIG_PATH")"

config_dir="$(dirname "$CONFIG_PATH")"
config_base="$(basename "$CONFIG_PATH")"
ssh -o StrictHostKeyChecking=no "$REMOTE_USER@$REMOTE_HOST" \
  "cd '$REMOTE_DIR' && mkdir -p '$config_dir' && cp '$config_base' '$CONFIG_PATH' 2>/dev/null || true && python3 backtest/runner.py --strategy '$FAMILY' --config '$CONFIG_PATH'"
