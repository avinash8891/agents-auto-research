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
import json
import os
import re
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

import paramiko

from strategies import STRATEGIES
from strategy_family import StrategyFamily, load_family
from trace_sdk import trace, trace_ssh

LOCAL_ROOT = Path(__file__).resolve().parent
SYNC_DIRS = ("backtest", "configs", "strategies", "trace_adapters")
SYNC_TOP_LEVEL_SUFFIXES = (".py", ".yaml", ".yml", ".json")
SYNC_EXCLUDED_DIRS = {
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    "__pycache__",
    "experiments",
    "logs",
}
KNOWN_HOSTS_ENV = "AUTORESEARCH_KNOWN_HOSTS"


@dataclass(frozen=True)
class VPSConfig:
    host: str
    user: str
    key: str
    remote_dir: str


def config_from_env() -> VPSConfig:
    required = (
        "AUTORESEARCH_VPS_HOST",
        "AUTORESEARCH_VPS_USER",
        "AUTORESEARCH_VPS_KEY",
        "AUTORESEARCH_VPS_DIR",
    )
    missing = [name for name in required if not os.environ.get(name)]
    if missing:
        raise ValueError("Missing VPS configuration environment variables: " + ", ".join(missing))
    return VPSConfig(
        host=os.environ["AUTORESEARCH_VPS_HOST"],
        user=os.environ["AUTORESEARCH_VPS_USER"],
        key=os.path.expanduser(os.environ["AUTORESEARCH_VPS_KEY"]),
        remote_dir=os.environ["AUTORESEARCH_VPS_DIR"],
    )


def build_remote_command(config: VPSConfig, family: StrategyFamily, config_path: str) -> str:
    config_basename = os.path.basename(config_path)
    config_dirname = os.path.dirname(config_path)
    return (
        f"cd {config.remote_dir} && "
        f'mkdir -p "{config_dirname}" && '
        f'cp "{config_basename}" "{config_path}" 2>/dev/null || true && '
        f"find . -name __pycache__ -type d -exec rm -rf {{}} + 2>/dev/null || true && "
        f'python3 backtest/runner.py --strategy "{family.name}" --config "{config_path}"'
    )


def syntax_check(paths: list[Path]) -> bool:
    for path in paths:
        if path.suffix != ".py":
            continue
        try:
            ast.parse(path.read_text())
        except SyntaxError as e:
            try:
                rel = path.relative_to(LOCAL_ROOT)
            except ValueError:
                rel = path
            print(f"SYNTAX ERROR in {rel}: {e}", file=sys.stderr)
            return False
    return True


def _is_runtime_top_level_file(rel: Path) -> bool:
    name = rel.as_posix()
    return (
        name.endswith("_autoresearch.current.md")
        or name.endswith("_autoresearch.next.json")
        or name.endswith("_baseline_checkpoints.json")
        or name.endswith("_experiments.db")
        or name == "research_findings.jsonl"
    )


def _is_syncable_path(path: Path, root: Path) -> bool:
    rel = path.relative_to(root)
    if any(part.endswith("_autoresearch-runs") for part in rel.parts):
        return False
    if any(part in SYNC_EXCLUDED_DIRS for part in rel.parts):
        return False
    if len(rel.parts) == 1:
        return path.suffix in SYNC_TOP_LEVEL_SUFFIXES and not _is_runtime_top_level_file(rel)
    return rel.parts[0] in SYNC_DIRS and path.suffix in SYNC_TOP_LEVEL_SUFFIXES


def sync_relative_paths(root: Path = LOCAL_ROOT) -> list[str]:
    return [
        path.relative_to(root).as_posix()
        for path in sorted(root.rglob("*"))
        if path.is_file() and _is_syncable_path(path, root)
    ]


def _iter_sync_paths(root: Path = LOCAL_ROOT) -> list[Path]:
    return [root / rel for rel in sync_relative_paths(root)]


def create_verified_ssh_client() -> paramiko.SSHClient:
    """Create an SSH client that rejects untrusted VPS host keys."""
    client = paramiko.SSHClient()
    client.load_system_host_keys()
    known_hosts_path = os.environ.get(KNOWN_HOSTS_ENV)
    if known_hosts_path:
        known_hosts_file = Path(os.path.expanduser(known_hosts_path))
        if not known_hosts_file.exists():
            raise FileNotFoundError(f"{KNOWN_HOSTS_ENV} points to missing file: {known_hosts_file}")
        client.load_host_keys(str(known_hosts_file))
    client.set_missing_host_key_policy(paramiko.RejectPolicy())
    return client


def connect_verified_ssh_client(vps_config: VPSConfig) -> paramiko.SSHClient:
    client = create_verified_ssh_client()
    try:
        client.connect(vps_config.host, username=vps_config.user, key_filename=vps_config.key)
    except paramiko.SSHException as exc:
        client.close()
        raise RuntimeError(
            "VPS SSH connection failed with host-key verification enforced. "
            f"Add {vps_config.host} to ~/.ssh/known_hosts or set {KNOWN_HOSTS_ENV} "
            "to a known_hosts file containing the VPS host key."
        ) from exc
    return client


def _ensure_remote_dir(sftp: paramiko.SFTPClient, remote_path: str) -> None:
    parts = Path(remote_path).parts
    current = ""
    for part in parts[:-1]:
        current = f"{current}/{part}" if current else part
        try:
            sftp.mkdir(current)
        except IOError:
            pass


def _localize_remote_result_output(output: str, sftp: paramiko.SFTPClient) -> str:
    """Fetch remote result artifacts locally and rewrite RESULT_JSON to a local path."""
    match = re.search(r"^RESULT_JSON (.+)$", output, flags=re.MULTILINE)
    if not match:
        return output

    remote_result_path = Path(match.group(1).strip())
    local_dir = Path(tempfile.mkdtemp(prefix="autoresearch-vps-"))
    local_result_path = local_dir / "result.json"

    try:
        sftp.get(str(remote_result_path), str(local_result_path))
    except OSError:
        return output

    try:
        payload = json.loads(local_result_path.read_text())
    except Exception:
        return output

    for key in ("trades_file", "strategy_events_file", "diagnostics_file"):
        remote_file = payload.get(key)
        if not remote_file:
            continue
        remote_file_path = Path(str(remote_file))
        local_file_path = local_dir / remote_file_path.name
        try:
            sftp.get(str(remote_file_path), str(local_file_path))
        except OSError:
            continue
        payload[key] = str(local_file_path)

    local_result_path.write_text(json.dumps(payload, indent=2) + "\n")
    return re.sub(
        r"^RESULT_JSON .+$",
        lambda _: f"RESULT_JSON {local_result_path}",
        output,
        count=1,
        flags=re.MULTILINE,
    )


def main():
    parser = argparse.ArgumentParser(description="Run a strategy backtest on the VPS")
    parser.add_argument("--strategy", required=True, choices=sorted(STRATEGIES))
    parser.add_argument("config_path")
    args = parser.parse_args()

    config_path = args.config_path
    strategy_name = args.strategy
    family = load_family(strategy_name)
    vps_config = config_from_env()
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

    trace("VPS_RUNNER", f"Connecting to {vps_config.host} as {vps_config.user}")
    try:
        client = connect_verified_ssh_client(vps_config)
    except (FileNotFoundError, RuntimeError) as exc:
        trace("VPS_RUNNER", f"SSH setup failed: {exc}")
        print(str(exc), file=sys.stderr)
        sys.exit(1)
    sftp = client.open_sftp()
    trace("VPS_RUNNER", "Connected")

    t0 = time.time()
    for path in sync_paths:
        rel = path.relative_to(LOCAL_ROOT).as_posix()
        remote = f"{vps_config.remote_dir}/{rel}"
        _ensure_remote_dir(sftp, remote)
        sftp.put(str(path), remote)
        trace("VPS_RUNNER", f"SCP {rel} -> {remote}")

    config_basename = os.path.basename(config_path)
    sftp.put(str(LOCAL_ROOT / config_path), f"{vps_config.remote_dir}/{config_basename}")
    trace(
        "VPS_RUNNER",
        f"SCP config {config_path} -> {vps_config.remote_dir}/{config_basename}",
    )

    trace("VPS_RUNNER", f"SCP complete in {time.time() - t0:.1f}s")

    cmd = build_remote_command(vps_config, family, config_path)
    trace("VPS_RUNNER", f"SSH EXEC: {cmd}")
    t1 = time.time()
    stdin, stdout, stderr = client.exec_command(cmd, timeout=600)
    stdout.channel.settimeout(600)
    stderr.channel.settimeout(600)
    out = stdout.read().decode()
    err = stderr.read().decode()
    exit_code = stdout.channel.recv_exit_status()
    elapsed = time.time() - t1

    out = _localize_remote_result_output(out, sftp)
    client.close()
    sftp.close()

    trace_ssh(cmd, exit_code, out, err)
    trace(
        "VPS_RUNNER",
        f"DONE exit={exit_code} elapsed={elapsed:.1f}s stdout_len={len(out)} stderr_len={len(err)}",
    )

    if out:
        print(out, end="")
    if err:
        print(err, end="", file=sys.stderr)

    sys.exit(exit_code)


if __name__ == "__main__":
    main()
