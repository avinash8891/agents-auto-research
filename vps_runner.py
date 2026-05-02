#!/usr/bin/env python3
"""VPS runner using Git-based deployment for strategy backtests.

Usage: python3 vps_runner.py --strategy <strategy-name> <config-path>

1. SSH to the VPS
2. Clone/fetch AUTORESEARCH_GIT_REPO at AUTORESEARCH_GIT_REF
3. Resolve that ref to an exact commit SHA and check it out detached
4. Run the generic backtest runner with SHA-scoped output
5. Print backtest output for autoresearch_loop to parse
"""

import argparse
import json
import os
import re
import shlex
import shutil
import sys
import tempfile
import time
from dataclasses import dataclass, replace
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import paramiko

from strategies import STRATEGIES
from strategy_family import StrategyFamily, load_family
from trace_sdk import trace, trace_ssh

KNOWN_HOSTS_ENV = "AUTORESEARCH_KNOWN_HOSTS"
RESOLVED_SHA_MARKER = "AUTORESEARCH_RESOLVED_SHA"
LEGACY_REMOTE_ROOT = "/root/orb-research"


@dataclass(frozen=True)
class VPSConfig:
    host: str
    user: str
    key: str
    remote_dir: str
    git_repo: str
    git_ref: str
    job: str = "0"


def config_from_env() -> VPSConfig:
    required = (
        "AUTORESEARCH_VPS_HOST",
        "AUTORESEARCH_VPS_USER",
        "AUTORESEARCH_VPS_KEY",
        "AUTORESEARCH_VPS_DIR",
        "AUTORESEARCH_GIT_REPO",
        "AUTORESEARCH_GIT_REF",
    )
    missing = [name for name in required if not os.environ.get(name)]
    if missing:
        raise ValueError("Missing VPS configuration environment variables: " + ", ".join(missing))
    remote_dir = os.environ["AUTORESEARCH_VPS_DIR"]
    if remote_dir == LEGACY_REMOTE_ROOT:
        raise ValueError(
            "Refusing legacy VPS root /root/orb-research; set AUTORESEARCH_VPS_DIR "
            "to a fresh remote root before launching."
        )
    return VPSConfig(
        host=os.environ["AUTORESEARCH_VPS_HOST"],
        user=os.environ["AUTORESEARCH_VPS_USER"],
        key=os.path.expanduser(os.environ["AUTORESEARCH_VPS_KEY"]),
        remote_dir=remote_dir,
        git_repo=os.environ["AUTORESEARCH_GIT_REPO"],
        git_ref=os.environ["AUTORESEARCH_GIT_REF"],
        job=os.environ.get("AUTORESEARCH_JOB", "0"),
    )


def build_git_prepare_command(config: VPSConfig) -> str:
    remote_dir = shlex.quote(config.remote_dir)
    remote_parent = shlex.quote(str(Path(config.remote_dir).parent))
    git_repo = shlex.quote(config.git_repo)
    git_ref = shlex.quote(config.git_ref)
    return (
        "set -e && "
        f"mkdir -p {remote_parent} && "
        f"if [ ! -d {remote_dir}/.git ]; then "
        f"git clone --no-checkout {git_repo} {remote_dir}; "
        "fi && "
        f"cd {remote_dir} && "
        f"git remote set-url origin {git_repo} && "
        f"git fetch --prune origin {git_ref} && "
        "resolved=$(git rev-parse --verify FETCH_HEAD^{commit}) && "
        'git checkout --detach "$resolved" && '
        "git clean -ffdx "
        "-e '*_autoresearch-runs' -e '*_autoresearch-runs/**' "
        "-e 'venv' -e 'venv/**' -e '.venv' -e '.venv/**' "
        "-e '*_autoresearch.next.json' -e '*_autoresearch.current.md' "
        "-e '*_autoresearch.ideas.md' -e '*_baseline_checkpoints.json' "
        "-e '*_experiments.db' -e 'logs' -e 'logs/**' && "
        f"printf '{RESOLVED_SHA_MARKER} %s\\n' \"$resolved\""
    )


def redact_git_repo_url(git_repo: str) -> str:
    parsed = urlsplit(git_repo)
    if not parsed.scheme or "@" not in parsed.netloc:
        return git_repo
    host = parsed.hostname or ""
    if parsed.port:
        host = f"{host}:{parsed.port}"
    return urlunsplit((parsed.scheme, f"***@{host}", parsed.path, parsed.query, parsed.fragment))


def redact_secrets(text: str, config: VPSConfig) -> str:
    redacted = text.replace(config.git_repo, redact_git_repo_url(config.git_repo))
    return re.sub(r"(https?://)[^/\s@]+@", r"\1***@", redacted)


def parse_resolved_sha(output: str) -> str:
    match = re.search(rf"^{RESOLVED_SHA_MARKER} ([0-9a-f]{{40}})$", output, flags=re.MULTILINE)
    if not match:
        raise RuntimeError("VPS Git prepare did not report a resolved commit SHA")
    return match.group(1)


def build_remote_command(
    config: VPSConfig,
    family: StrategyFamily,
    config_path: str,
    resolved_sha: str,
) -> str:
    python_hash = (
        "import sys; "
        "from backtest.runtime_config import load_runtime_config; "
        "from config_hash import _config_hash; "
        "print(_config_hash(load_runtime_config(sys.argv[1], sys.argv[2])))"
    )
    output_root = f"{config.remote_dir}/{family.runs_dirname}/job-{config.job}/{resolved_sha}"
    return (
        "set -e && "
        f"cd {shlex.quote(config.remote_dir)} && "
        "export AUTORESEARCH_VPS=1 && "
        f"export AUTORESEARCH_RESOLVED_SHA={shlex.quote(resolved_sha)} && "
        f"config_hash=$(python3 -c {shlex.quote(python_hash)} "
        f"{shlex.quote(config_path)} {shlex.quote(family.name)}) && "
        f"output_dir={shlex.quote(output_root)}/$config_hash && "
        'mkdir -p "$output_dir" && '
        f"python3 -m backtest.runner --strategy {shlex.quote(family.name)} "
        f'--config {shlex.quote(config_path)} --output-dir "$output_dir"'
    )


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
    except OSError as exc:
        shutil.rmtree(local_dir, ignore_errors=True)
        raise RuntimeError(
            f"Failed to fetch remote RESULT_JSON artifact: {remote_result_path}"
        ) from exc

    try:
        payload = json.loads(local_result_path.read_text())
    except Exception as exc:
        shutil.rmtree(local_dir, ignore_errors=True)
        raise RuntimeError(
            f"Failed to parse fetched remote RESULT_JSON artifact: {remote_result_path}"
        ) from exc

    for key in ("trades_file", "strategy_events_file", "diagnostics_file"):
        remote_file = payload.get(key)
        if not remote_file:
            continue
        remote_file_path = Path(str(remote_file))
        local_file_path = local_dir / remote_file_path.name
        try:
            sftp.get(str(remote_file_path), str(local_file_path))
        except OSError as exc:
            shutil.rmtree(local_dir, ignore_errors=True)
            raise RuntimeError(
                f"Failed to fetch remote result artifact referenced by {key}: {remote_file_path}"
            ) from exc
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

    trace("VPS_RUNNER", f"Connecting to {vps_config.host} as {vps_config.user}")
    try:
        client = connect_verified_ssh_client(vps_config)
    except (FileNotFoundError, RuntimeError) as exc:
        trace("VPS_RUNNER", f"SSH setup failed: {exc}")
        print(str(exc), file=sys.stderr)
        sys.exit(1)
    trace("VPS_RUNNER", "Connected")

    prepare_cmd = build_git_prepare_command(vps_config)
    safe_prepare_cmd = build_git_prepare_command(
        replace(vps_config, git_repo=redact_git_repo_url(vps_config.git_repo))
    )
    trace("VPS_RUNNER", f"SSH PREPARE: {safe_prepare_cmd}")
    t0 = time.time()
    _, prepare_stdout, prepare_stderr = client.exec_command(prepare_cmd, timeout=600)
    prepare_out = prepare_stdout.read().decode()
    prepare_err = prepare_stderr.read().decode()
    safe_prepare_out = redact_secrets(prepare_out, vps_config)
    safe_prepare_err = redact_secrets(prepare_err, vps_config)
    prepare_exit = prepare_stdout.channel.recv_exit_status()
    trace_ssh(safe_prepare_cmd, prepare_exit, safe_prepare_out, safe_prepare_err)
    if prepare_exit != 0:
        client.close()
        if safe_prepare_out:
            print(safe_prepare_out, end="")
        if safe_prepare_err:
            print(safe_prepare_err, end="", file=sys.stderr)
        sys.exit(prepare_exit)
    try:
        resolved_sha = parse_resolved_sha(prepare_out)
    except RuntimeError as exc:
        client.close()
        print(str(exc), file=sys.stderr)
        sys.exit(1)
    trace("VPS_RUNNER", f"Git prepare complete sha={resolved_sha} elapsed={time.time() - t0:.1f}s")

    cmd = build_remote_command(vps_config, family, config_path, resolved_sha)
    trace("VPS_RUNNER", f"SSH EXEC: {cmd}")
    t1 = time.time()
    stdin, stdout, stderr = client.exec_command(cmd, timeout=600)
    stdout.channel.settimeout(600)
    stderr.channel.settimeout(600)
    out = stdout.read().decode()
    err = stderr.read().decode()
    exit_code = stdout.channel.recv_exit_status()
    elapsed = time.time() - t1

    sftp = client.open_sftp()
    try:
        out = _localize_remote_result_output(out, sftp)
    except RuntimeError as exc:
        trace("VPS_RUNNER", f"Artifact localization failed: {exc}")
        sftp.close()
        client.close()
        print(str(exc), file=sys.stderr)
        sys.exit(1)
    sftp.close()
    client.close()

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
