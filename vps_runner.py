#!/usr/bin/env python3
"""VPS runner using paramiko for strategy backtests.

Usage: python3 vps_runner.py --strategy <strategy-name> <config-path>

1. Syntax-check local files
2. SCP files to VPS
3. SSH to run the generic backtest runner
4. Print backtest output for autoresearch_loop to parse
"""

import argparse
import ast
import os
import sys
import time
from pathlib import Path

import paramiko

from strategies import STRATEGIES
from trace_logger import trace, trace_ssh

VPS_HOST = "31.97.60.116"
VPS_USER = "root"
VPS_KEY = os.path.expanduser("~/.ssh/vps_key")
VPS_DIR = "/root/orb-research"

LOCAL_ROOT = Path(__file__).resolve().parent
SYNC_DIRS = ["backtest", "strategies"]
SYNC_FILES = [
    "config_hash.py",
    "agent_orchestrator.py",
    "data_loader.py",
    "metrics.py",
    "numba_kernels.py",
    "strategy_event_logger.py",
    "trace_logger.py",
]


def syntax_check(paths: list[Path]) -> bool:
    for path in paths:
        if path.suffix != ".py":
            continue
        try:
            ast.parse(path.read_text())
        except SyntaxError as e:
            rel = path.relative_to(LOCAL_ROOT)
            print(f"SYNTAX ERROR in {rel}: {e}", file=sys.stderr)
            return False
    return True


def _iter_sync_paths() -> list[Path]:
    paths: list[Path] = []
    for dirname in SYNC_DIRS:
        base = LOCAL_ROOT / dirname
        if not base.exists():
            continue
        for path in sorted(base.rglob("*")):
            if path.is_file() and path.suffix in {".py", ".yaml"}:
                paths.append(path)
    for rel in SYNC_FILES:
        path = LOCAL_ROOT / rel
        if path.exists():
            paths.append(path)
    seen: set[Path] = set()
    unique: list[Path] = []
    for path in paths:
        rel = path.relative_to(LOCAL_ROOT)
        if rel in seen:
            continue
        seen.add(rel)
        unique.append(path)
    return unique


def _ensure_remote_dir(sftp: paramiko.SFTPClient, remote_path: str) -> None:
    parts = Path(remote_path).parts
    current = ""
    for part in parts[:-1]:
        current = f"{current}/{part}" if current else part
        try:
            sftp.mkdir(current)
        except IOError:
            pass


def main():
    parser = argparse.ArgumentParser(description="Run a strategy backtest on the VPS")
    parser.add_argument("--strategy", required=True, choices=sorted(STRATEGIES))
    parser.add_argument("config_path")
    args = parser.parse_args()

    config_path = args.config_path
    strategy_name = args.strategy
    trace("VPS_RUNNER", f"START strategy={strategy_name} config={config_path}")
    if not (LOCAL_ROOT / config_path).exists():
        trace("VPS_RUNNER", f"Config not found: {config_path}")
        print(f"Config not found: {config_path}", file=sys.stderr)
        sys.exit(1)

    sync_paths = _iter_sync_paths()
    trace("VPS_RUNNER", f"Syntax checking {len(sync_paths)} synced files")
    if not syntax_check(sync_paths):
        trace("VPS_RUNNER", "SYNTAX CHECK FAILED")
        print("SYNTAX ERROR")
        sys.exit(1)
    trace("VPS_RUNNER", "Syntax check passed")

    trace("VPS_RUNNER", f"Connecting to {VPS_HOST} as {VPS_USER}")
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(VPS_HOST, username=VPS_USER, key_filename=VPS_KEY)
    sftp = client.open_sftp()
    trace("VPS_RUNNER", "Connected")

    t0 = time.time()
    for path in sync_paths:
        rel = path.relative_to(LOCAL_ROOT).as_posix()
        remote = f"{VPS_DIR}/{rel}"
        _ensure_remote_dir(sftp, remote)
        sftp.put(str(path), remote)
        trace("VPS_RUNNER", f"SCP {rel} -> {remote}")

    config_basename = os.path.basename(config_path)
    config_dirname = os.path.dirname(config_path)
    sftp.put(str(LOCAL_ROOT / config_path), f"{VPS_DIR}/{config_basename}")
    trace("VPS_RUNNER", f"SCP config {config_path} -> {VPS_DIR}/{config_basename}")

    sftp.close()
    trace("VPS_RUNNER", f"SCP complete in {time.time() - t0:.1f}s")

    cmd = (
        f"cd {VPS_DIR} && "
        f'mkdir -p "{config_dirname}" && '
        f'cp "{config_basename}" "{config_path}" 2>/dev/null || true && '
        f"find . -name __pycache__ -type d -exec rm -rf {{}} + 2>/dev/null || true && "
        f'python3 backtest/runner.py --strategy "{strategy_name}" --config "{config_path}"'
    )
    trace("VPS_RUNNER", f"SSH EXEC: {cmd}")
    t1 = time.time()
    stdin, stdout, stderr = client.exec_command(cmd, timeout=600)
    stdout.channel.settimeout(600)
    stderr.channel.settimeout(600)
    out = stdout.read().decode()
    err = stderr.read().decode()
    exit_code = stdout.channel.recv_exit_status()
    elapsed = time.time() - t1

    trace_ssh(cmd, exit_code, out, err)
    trace(
        "VPS_RUNNER",
        f"DONE exit={exit_code} elapsed={elapsed:.1f}s stdout_len={len(out)} stderr_len={len(err)}",
    )

    if out:
        print(out, end="")
    if err:
        print(err, end="", file=sys.stderr)

    client.close()
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
