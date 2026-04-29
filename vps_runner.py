#!/usr/bin/env python3
"""VPS runner using paramiko — replaces ssh/scp in ema_autoresearch.sh.

Usage: python3 vps_runner.py <config-path>

Does exactly what ema_autoresearch.sh does:
1. Syntax-check local files
2. SCP files to VPS
3. SSH to run backtest
4. Print backtest output (for autoresearch_loop to parse)
"""
import ast
import os
import sys
import time
from pathlib import Path

import paramiko

from trace_logger import trace, trace_ssh

VPS_HOST = "31.97.60.116"
VPS_USER = "root"
VPS_KEY = os.path.expanduser("~/.ssh/vps_key")
VPS_DIR = "/root/orb-research"

LOCAL_ROOT = Path(__file__).resolve().parent

# Files to sync (mirrors ema_autoresearch.sh)
SYNC_FILES = [
    "backtest_5ema.py",
    "ema_signals.py",
    "ema_exits.py",
    "ema_contract.py",
    "agent_orchestrator.py",
    "data_loader.py",
    "metrics.py",
    "numba_kernels.py",
    "trace_logger.py",
]

# Files to syntax-check
SYNTAX_CHECK = [
    "ema_signals.py",
    "backtest_5ema.py",
    "ema_contract.py",
]


def syntax_check():
    for fname in SYNTAX_CHECK:
        path = LOCAL_ROOT / fname
        try:
            ast.parse(path.read_text())
        except SyntaxError as e:
            print(f"SYNTAX ERROR in {fname}: {e}", file=sys.stderr)
            return False
    return True


def main():
    if len(sys.argv) < 2:
        print("Usage: python3 vps_runner.py <config-path>", file=sys.stderr)
        sys.exit(1)

    config_path = sys.argv[1]
    trace("VPS_RUNNER", f"START config={config_path}")
    if not (LOCAL_ROOT / config_path).exists():
        trace("VPS_RUNNER", f"Config not found: {config_path}")
        print(f"Config not found: {config_path}", file=sys.stderr)
        sys.exit(1)

    # 1. Syntax check
    trace("VPS_RUNNER", f"Syntax checking: {SYNTAX_CHECK}")
    if not syntax_check():
        trace("VPS_RUNNER", "SYNTAX CHECK FAILED")
        print("SYNTAX ERROR")
        sys.exit(1)
    trace("VPS_RUNNER", "Syntax check passed")

    # 2. Connect
    trace("VPS_RUNNER", f"Connecting to {VPS_HOST} as {VPS_USER}")
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(VPS_HOST, username=VPS_USER, key_filename=VPS_KEY)
    sftp = client.open_sftp()
    trace("VPS_RUNNER", "Connected")

    # 3. SCP files
    t0 = time.time()
    for fname in SYNC_FILES:
        local = str(LOCAL_ROOT / fname)
        remote = f"{VPS_DIR}/{fname}"
        if os.path.exists(local):
            sftp.put(local, remote)
            trace("VPS_RUNNER", f"SCP {fname} -> {remote}")

    # SCP config file
    config_basename = os.path.basename(config_path)
    config_dirname = os.path.dirname(config_path)
    sftp.put(str(LOCAL_ROOT / config_path), f"{VPS_DIR}/{config_basename}")
    trace("VPS_RUNNER", f"SCP config {config_path} -> {VPS_DIR}/{config_basename}")

    sftp.close()
    trace("VPS_RUNNER", f"SCP complete in {time.time() - t0:.1f}s")

    # 4. SSH to run backtest
    cmd = (
        f"cd {VPS_DIR} && "
        f"mkdir -p \"{config_dirname}\" && "
        f"cp \"{config_basename}\" \"{config_path}\" 2>/dev/null || true && "
        f"find . -name __pycache__ -type d -exec rm -rf {{}} + 2>/dev/null || true && "
        f"python3 backtest_5ema.py --config \"{config_path}\""
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
    trace("VPS_RUNNER", f"DONE exit={exit_code} elapsed={elapsed:.1f}s stdout_len={len(out)} stderr_len={len(err)}")

    # Print output for autoresearch_loop to parse
    if out:
        print(out, end="")
    if err:
        print(err, end="", file=sys.stderr)

    client.close()
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
