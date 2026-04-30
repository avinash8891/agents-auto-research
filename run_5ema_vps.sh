#!/usr/bin/env bash
# Run 5 EMA autoresearch entirely on VPS.
# Usage: ./run_5ema_vps.sh
set -euo pipefail

cd "$(dirname "$0")"

echo "=== 5 EMA Autoresearch — VPS Launch ==="
echo "Timestamp: $(date -u '+%Y-%m-%d %H:%M:%S UTC')"

# Sync latest source files to VPS
echo "Syncing files to VPS..."
python3 << 'PYEOF'
import os
from pathlib import Path

import paramiko

client = paramiko.SSHClient()
client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
client.connect("31.97.60.116", username="root", key_filename=os.path.expanduser("~/.ssh/vps_key"))
sftp = client.open_sftp()

LOCAL = Path(".").resolve()
REMOTE = "/root/orb-research"
SYNC_DIRS = ["backtest", "strategies"]
FILES = [
    "autoresearch_loop.py",
    "agent_orchestrator.py",
    "compiler_pipeline.py",
    "strategy_family.py",
    "family_research.py",
    "artifact_store.py",
    "experiment_schema.py",
    "trace_logger.py",
    "backtest_5ema.py",
    "config_hash.py",
    "data_loader.py",
    "metrics.py",
    "numba_kernels.py",
    "strategy_event_logger.py",
    "experiment_db.py",
    "experiment_evaluator.py",
    "research_conductor.py",
    "research_types.py",
    "thesis_validator.py",
    "ema_autoresearch.jsonl",
    "ema_autoresearch.next.json",
    "configs/ema_base.yaml",
]


def ensure_remote_dir(remote_path: str) -> None:
    parts = Path(remote_path).parts
    current = ""
    for part in parts[:-1]:
        current = f"{current}/{part}" if current else part
        try:
            sftp.mkdir(current)
        except IOError:
            pass


def iter_sync_paths() -> list[Path]:
    paths: list[Path] = []
    for dirname in SYNC_DIRS:
        base = LOCAL / dirname
        if not base.exists():
            continue
        for path in sorted(base.rglob("*")):
            if path.is_file() and path.suffix in {".py", ".yaml"}:
                paths.append(path)
    for rel in FILES:
        path = LOCAL / rel
        if path.exists():
            paths.append(path)
    unique: list[Path] = []
    seen: set[Path] = set()
    for path in paths:
        rel = path.relative_to(LOCAL)
        if rel in seen:
            continue
        seen.add(rel)
        unique.append(path)
    return unique


for d in [
    "configs",
    "ema-contracts",
    "ema-proposals",
    "ema-compilations",
    "ema-run-queue",
    "ema-research",
    "ema_autoresearch-runs",
    "ema-builder-requests",
    "logs",
]:
    try:
        sftp.mkdir(f"{REMOTE}/{d}")
    except IOError:
        pass

for path in iter_sync_paths():
    rel = path.relative_to(LOCAL).as_posix()
    remote = f"{REMOTE}/{rel}"
    ensure_remote_dir(remote)
    sftp.put(str(path), remote)

for d in ["ema-contracts", "ema-run-queue"]:
    source_dir = LOCAL / d
    if source_dir.exists():
        for p in source_dir.glob("*.json"):
            sftp.put(str(p), f"{REMOTE}/{d}/{p.name}")

sftp.close()
client.close()
print("Sync done")
PYEOF

echo ""
echo "Launching autoresearch on VPS..."
echo "Logs: /root/orb-research/logs/ on VPS"
echo ""

# Launch on VPS via paramiko (ssh binary doesn't work from Factory)
python3 << 'PYEOF'
import os

import paramiko

client = paramiko.SSHClient()
client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
client.connect("31.97.60.116", username="root", key_filename=os.path.expanduser("~/.ssh/vps_key"))

cmd = (
    "su - researcher -c '"
    "cd /root/orb-research && "
    "export AUTORESEARCH_VPS=1 && "
    "export CLAUDE_CODE_OAUTH_TOKEN=$(cat /home/researcher/.claude_oauth_token) && "
    "nohup ./venv/bin/python3 autoresearch_loop.py --family ema "
    "> autoresearch_stdout.log 2>&1 & "
    "echo PID=$!'"
)
stdin, stdout, stderr = client.exec_command(cmd, timeout=10)
out = stdout.read().decode().strip()
err = stderr.read().decode().strip()
print(out)
if err:
    print(f"ERR: {err}")

client.close()
PYEOF

echo ""
echo "Autoresearch running on VPS. To monitor:"
echo "  python3 -c "import paramiko,os; c=paramiko.SSHClient(); c.set_missing_host_key_policy(paramiko.AutoAddPolicy()); c.connect('31.97.60.116',username='root',key_filename=os.path.expanduser('~/.ssh/vps_key')); i,o,e=c.exec_command('tail -20 /root/orb-research/autoresearch_stdout.log'); print(o.read().decode()); c.close()""
