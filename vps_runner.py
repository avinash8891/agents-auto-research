#!/usr/bin/env python3
"""VPS runner using Git-based deployment for strategy controller runs.

Usage:
  python3 vps_runner.py --strategy <strategy-name> --git-ref <branch|tag>
  python3 vps_runner.py --strategy <strategy-name> --git-sha <full-commit-sha>
  [--vps-dir <abs-path>]

1. SSH to the VPS
2. Clone/fetch AUTORESEARCH_GIT_REPO at the requested Git ref or exact SHA
3. Resolve that deploy spec to an exact commit SHA and check it out detached
4. Launch the strategy-family autoresearch controller on the VPS
5. Stream controller output
"""

import argparse
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, replace
from pathlib import Path, PurePosixPath
from urllib.parse import urlsplit, urlunsplit

import paramiko

from backtest.data_universe import DATA_ROOT_ENV
from strategies import STRATEGIES
from strategy_family import StrategyFamily, load_family
from trace_sdk import trace, trace_ssh

KNOWN_HOSTS_ENV = "AUTORESEARCH_KNOWN_HOSTS"
RESOLVED_SHA_MARKER = "AUTORESEARCH_RESOLVED_SHA"
LEGACY_REMOTE_ROOT = "/root/orb-research"
REMOTE_ROOT_DENYLIST = {"/", "/root", "/home", "/srv", "/tmp", "/var", "/opt"}
REMOTE_RUNTIME_ENV_FILENAME = ".env.autoresearch"
REMOTE_RUNTIME_ENV_PREFIXES = (
    "AUTORESEARCH_",
    "ANTHROPIC_",
    "CLAUDE_CODE_",
    "OPENAI_",
    "OPENINFERENCE_",
    "OPENTELEMETRY_",
    "OTEL_",
    "TRACELOOP_",
)
REMOTE_RUNTIME_ENV_EXACT_KEYS = {
    "AUTORESEARCH_GIT_REPO",
    "AUTORESEARCH_GIT_REF",
    "AUTORESEARCH_KNOWN_HOSTS",
    "AUTORESEARCH_JOB",
    "AUTORESEARCH_PYTHON_BIN",
    "AUTORESEARCH_RESOLVED_SHA",
    "AUTORESEARCH_VPS",
    "AUTORESEARCH_VPS_HOST",
    "AUTORESEARCH_VPS_KEY",
    "AUTORESEARCH_VPS_DIR",
    "AUTORESEARCH_VPS_USER",
}


def _load_local_env_file() -> None:
    """Load repo-local .env defaults for CLI runs without overriding shell env."""
    env_file = Path(__file__).resolve().parent / ".env"
    if not env_file.exists():
        return

    for raw_line in env_file.read_text().splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].strip()
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
            continue
        if key in os.environ:
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        os.environ[key] = value


@dataclass(frozen=True)
class VPSConfig:
    host: str
    user: str
    key: str
    remote_dir: str
    git_repo: str
    git_ref: str
    git_sha: str = ""
    data_root: str = ""

    @property
    def deploy_spec(self) -> str:
        return self.git_sha or self.git_ref


def _default_remote_dir(vps_user: str, strategy_name: str) -> str:
    base = "/root" if vps_user == "root" else f"/home/{vps_user}"
    stamp = time.strftime("%Y%m%d-%H%M%S", time.gmtime())
    # Add a sub-second suffix so multiple launches in the same second still
    # get distinct remote roots.
    nonce = f"{time.time_ns() % 1_000_000_000:09d}"
    return f"{base}/autoresearch-{strategy_name}-{stamp}-{nonce}"


def config_from_env(
    *, git_ref: str = "", git_sha: str = "", strategy_name: str, remote_dir: str | None = None
) -> VPSConfig:
    required = (
        "AUTORESEARCH_VPS_HOST",
        "AUTORESEARCH_VPS_USER",
        "AUTORESEARCH_VPS_KEY",
        "AUTORESEARCH_GIT_REPO",
    )
    missing = [name for name in required if not os.environ.get(name)]
    if missing:
        raise ValueError("Missing VPS configuration environment variables: " + ", ".join(missing))
    if bool(git_ref) == bool(git_sha):
        raise ValueError("Specify exactly one of git_ref or git_sha for VPS deployment.")
    if git_ref:
        _validate_git_ref(git_ref)
    if git_sha:
        _validate_git_sha(git_sha)
    vps_user = os.environ["AUTORESEARCH_VPS_USER"]
    resolved_remote_dir = remote_dir or _default_remote_dir(vps_user, strategy_name)
    _validate_remote_dir(resolved_remote_dir)
    data_root = os.environ.get(DATA_ROOT_ENV, "")
    if data_root:
        data_root = _expand_remote_user_path(data_root, vps_user)
        _validate_remote_data_root(data_root)
    return VPSConfig(
        host=os.environ["AUTORESEARCH_VPS_HOST"],
        user=vps_user,
        key=os.path.expanduser(os.environ["AUTORESEARCH_VPS_KEY"]),
        remote_dir=resolved_remote_dir,
        git_repo=os.environ["AUTORESEARCH_GIT_REPO"],
        git_ref=git_ref,
        git_sha=git_sha,
        data_root=data_root,
    )


def _validate_remote_dir(remote_dir: str) -> None:
    if remote_dir == LEGACY_REMOTE_ROOT:
        raise ValueError(
            "Refusing legacy VPS root /root/orb-research; set --vps-dir "
            "to a fresh remote root before launching."
        )
    path = PurePosixPath(remote_dir)
    if not path.is_absolute() or str(path) != remote_dir:
        raise ValueError("--vps-dir must be an absolute normalized POSIX path.")
    if remote_dir in REMOTE_ROOT_DENYLIST:
        raise ValueError(
            "--vps-dir must point at a dedicated autoresearch checkout, " f"not {remote_dir}."
        )
    if ".." in path.parts:
        raise ValueError("--vps-dir must not contain parent-directory segments.")
    if not re.fullmatch(r"/[A-Za-z0-9._/-]+", remote_dir):
        raise ValueError(
            "--vps-dir must use only path-safe letters, digits, underscore, "
            "dot, dash, and slash."
        )
    if "autoresearch" not in path.name and "auto-research" not in path.name:
        raise ValueError("--vps-dir must end in a dedicated autoresearch checkout directory.")


def _validate_git_ref(git_ref: str) -> None:
    if re.fullmatch(r"[0-9a-fA-F]{40}", git_ref):
        raise ValueError("AUTORESEARCH_GIT_REF must be a branch or tag, not a commit SHA.")
    if re.fullmatch(r"[0-9a-fA-F]{7,39}", git_ref):
        raise ValueError(
            "AUTORESEARCH_GIT_REF must be a branch or tag, not a commit SHA abbreviation."
        )
    if git_ref.startswith(("+", "-")) or ":" in git_ref:
        raise ValueError("AUTORESEARCH_GIT_REF must be a branch or tag, not a refspec.")
    if any(token in git_ref for token in ("..", "@{", "\\")):
        raise ValueError("AUTORESEARCH_GIT_REF contains unsafe Git ref syntax.")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/@-]*", git_ref):
        raise ValueError("AUTORESEARCH_GIT_REF must be a path-safe branch or tag.")


def _validate_git_sha(git_sha: str) -> None:
    if not re.fullmatch(r"[0-9a-fA-F]{40}", git_sha):
        raise ValueError("AUTORESEARCH_GIT_SHA must be a full 40-char commit SHA.")


def _validate_remote_data_root(data_root: str) -> None:
    path = PurePosixPath(data_root)
    if not path.is_absolute() or str(path) != data_root:
        raise ValueError(f"{DATA_ROOT_ENV} must be an absolute normalized POSIX path.")
    if ".." in path.parts:
        raise ValueError(f"{DATA_ROOT_ENV} must not contain parent-directory segments.")
    if not re.fullmatch(r"/[A-Za-z0-9._/-]+", data_root):
        raise ValueError(
            f"{DATA_ROOT_ENV} must use only path-safe letters, digits, underscore, "
            "dot, dash, and slash."
        )


def _expand_remote_user_path(path: str, user: str) -> str:
    if path == "~":
        return "/root" if user == "root" else f"/home/{user}"
    if path.startswith("~/"):
        home = "/root" if user == "root" else f"/home/{user}"
        suffix = path[2:]
        return f"{home}/{suffix}" if suffix else home
    return path


def _runtime_env_items() -> list[tuple[str, str]]:
    items: list[tuple[str, str]] = []
    for key, value in os.environ.items():
        if key in REMOTE_RUNTIME_ENV_EXACT_KEYS:
            continue
        if not any(key.startswith(prefix) for prefix in REMOTE_RUNTIME_ENV_PREFIXES):
            continue
        if not value:
            continue
        items.append((key, value))
    return sorted(items)


def render_runtime_env_file() -> str:
    items = _runtime_env_items()
    if not items:
        return ""
    lines = [
        "# Autogenerated by vps_runner.py; sourced on the remote VPS controller.",
    ]
    for key, value in items:
        lines.append(f"export {key}={shlex.quote(value)}")
    return "\n".join(lines) + "\n"


def build_git_prepare_command(config: VPSConfig) -> str:
    remote_dir = shlex.quote(config.remote_dir)
    remote_parent = shlex.quote(str(PurePosixPath(config.remote_dir).parent))
    git_repo = shlex.quote(config.git_repo)
    deploy_spec = shlex.quote(config.deploy_spec)
    fetch_and_resolve = (
        f"git fetch --prune origin {deploy_spec} && "
        "resolved=$(git rev-parse --verify FETCH_HEAD^{commit}) && "
    )
    return (
        "set -e && "
        f"mkdir -p {remote_parent} && "
        f"if [ ! -d {remote_dir}/.git ]; then "
        f"git clone --no-checkout {git_repo} {remote_dir}; "
        "fi && "
        f"cd {remote_dir} && "
        f"git remote set-url origin {git_repo} && "
        f"{fetch_and_resolve}"
        'git checkout --detach "$resolved" && '
        "git clean -ffdx "
        "-e '*_autoresearch-runs' -e '*_autoresearch-runs/**' "
        "-e 'data' -e 'data/**' "
        "-e 'experiments' -e 'experiments/**' "
        "-e 'proposals' -e 'proposals/**' "
        "-e '*-proposals' -e '*-proposals/**' "
        "-e 'compilations' -e 'compilations/**' "
        "-e '*-compilations' -e '*-compilations/**' "
        "-e 'contracts' -e 'contracts/**' "
        "-e '*-contracts' -e '*-contracts/**' "
        "-e 'run-queue' -e 'run-queue/**' "
        "-e '*-run-queue' -e '*-run-queue/**' "
        "-e 'research' -e 'research/**' "
        "-e '*-research' -e '*-research/**' "
        "-e 'builder-requests' -e 'builder-requests/**' "
        "-e '*-builder-requests' -e '*-builder-requests/**' "
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
    resolved_sha: str,
) -> str:
    segments = [
        "set -e",
        f"cd {shlex.quote(config.remote_dir)}",
        f'if [ -f "{REMOTE_RUNTIME_ENV_FILENAME}" ]; then set -a; . ./{REMOTE_RUNTIME_ENV_FILENAME}; set +a; fi',
        f"export AUTORESEARCH_RESOLVED_SHA={shlex.quote(resolved_sha)}",
    ]
    if config.data_root:
        segments.append(f"export {DATA_ROOT_ENV}={shlex.quote(config.data_root)}")
    segments.extend(
        [
            'if [ ! -x ".venv/bin/python" ]; then python3 -m venv .venv; fi',
            "python_bin=.venv/bin/python",
            'export AUTORESEARCH_PYTHON_BIN="$python_bin"',
            '"$python_bin" -m pip install -e .',
            (f'"$python_bin" autoresearch_controller.py ' f"--family {shlex.quote(family.name)}"),
        ]
    )
    return " && ".join(segments)


def materialize_remote_runtime_env(
    client: paramiko.SSHClient,
    config: VPSConfig,
) -> str | None:
    content = render_runtime_env_file()
    if not content.strip():
        return None

    remote_path = f"{config.remote_dir.rstrip('/')}/{REMOTE_RUNTIME_ENV_FILENAME}"
    local_tmp = tempfile.NamedTemporaryFile(
        "w", delete=False, encoding="utf-8", prefix="autoresearch-env-"
    )
    try:
        local_tmp.write(content)
        local_tmp.flush()
        local_tmp.close()

        sftp = client.open_sftp()
        try:
            remote_parent = str(PurePosixPath(remote_path).parent)
            _sftp_mkdir_p(sftp, remote_parent)
            sftp.put(local_tmp.name, remote_path)
            sftp.chmod(remote_path, 0o600)
        finally:
            sftp.close()
    finally:
        try:
            os.unlink(local_tmp.name)
        except OSError:
            pass

    trace("VPS_RUNNER", f"Materialized runtime env file: {REMOTE_RUNTIME_ENV_FILENAME}")
    return remote_path


def _repo_root() -> Path:
    return Path(__file__).resolve().parent


def _relative_repo_path(path: str) -> Path:
    repo_root = _repo_root()
    local_path = Path(path)
    if not local_path.is_absolute():
        local_path = repo_root / local_path
    try:
        return local_path.resolve().relative_to(repo_root)
    except ValueError as exc:
        raise ValueError(f"Config path must be inside repository: {path}") from exc


def _is_git_tracked(rel_path: Path) -> bool:
    result = subprocess.run(
        ["git", "ls-files", "--error-unmatch", "--", rel_path.as_posix()],
        cwd=_repo_root(),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    return result.returncode == 0


def _sftp_mkdir_p(sftp: paramiko.SFTPClient, remote_dir: str) -> None:
    current = ""
    for part in PurePosixPath(remote_dir).parts:
        if part == "/":
            current = "/"
            continue
        current = f"{current.rstrip('/')}/{part}" if current else part
        try:
            sftp.stat(current)
        except OSError:
            sftp.mkdir(current)


def materialize_remote_config_if_needed(
    client: paramiko.SSHClient,
    config: VPSConfig,
    config_path: str,
) -> str:
    """Upload ignored generated experiment configs as run inputs, not code deployment."""
    rel_path = _relative_repo_path(config_path)
    if _is_git_tracked(rel_path):
        return rel_path.as_posix()

    if rel_path.parts[:1] != ("experiments",):
        raise RuntimeError(
            "VPS Git deployment only accepts tracked config files or generated "
            f"experiments/ configs; refused untracked config: {rel_path.as_posix()}"
        )

    local_path = _repo_root() / rel_path
    if not local_path.exists():
        raise FileNotFoundError(f"Generated config is not present locally: {rel_path.as_posix()}")

    remote_path = f"{config.remote_dir.rstrip('/')}/{rel_path.as_posix()}"
    remote_parent = str(PurePosixPath(remote_path).parent)
    sftp = client.open_sftp()
    try:
        _sftp_mkdir_p(sftp, remote_parent)
        sftp.put(str(local_path), remote_path)
    finally:
        sftp.close()
    trace("VPS_RUNNER", f"Materialized generated config input: {rel_path.as_posix()}")
    return rel_path.as_posix()


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


def _stream_remote_command(
    stdout: paramiko.channel.ChannelFile,
    stderr: paramiko.channel.ChannelStderrFile,
) -> tuple[int, str, str]:
    """Stream SSH command output while collecting stdout/stderr."""
    channel = stdout.channel
    out_chunks: list[str] = []
    err_chunks: list[str] = []

    while True:
        progressed = False

        while channel.recv_ready():
            chunk = channel.recv(4096)
            if not chunk:
                break
            text = chunk.decode(errors="replace")
            out_chunks.append(text)
            print(text, end="", flush=True)
            progressed = True

        while channel.recv_stderr_ready():
            chunk = channel.recv_stderr(4096)
            if not chunk:
                break
            text = chunk.decode(errors="replace")
            err_chunks.append(text)
            print(text, end="", file=sys.stderr, flush=True)
            progressed = True

        if (
            channel.exit_status_ready()
            and not channel.recv_ready()
            and not channel.recv_stderr_ready()
        ):
            break
        if not progressed:
            time.sleep(0.1)

    exit_code = channel.recv_exit_status()
    return exit_code, "".join(out_chunks), "".join(err_chunks)


def main():
    _load_local_env_file()

    parser = argparse.ArgumentParser(description="Run autoresearch controller on the VPS")
    parser.add_argument("--strategy", required=True, choices=sorted(STRATEGIES))
    deploy_group = parser.add_mutually_exclusive_group(required=True)
    deploy_group.add_argument(
        "--git-ref",
        help="Git branch or tag to deploy on VPS",
    )
    deploy_group.add_argument(
        "--git-sha",
        help="Exact 40-char commit SHA to deploy on VPS",
    )
    parser.add_argument(
        "--vps-dir",
        help=(
            "Absolute remote checkout directory on VPS. "
            "If omitted, runner auto-generates a fresh per-run directory."
        ),
    )
    args = parser.parse_args()

    strategy_name = args.strategy
    family = load_family(strategy_name)
    vps_config = config_from_env(
        git_ref=args.git_ref or "",
        git_sha=args.git_sha or "",
        strategy_name=strategy_name,
        remote_dir=args.vps_dir,
    )
    deploy_label = "sha" if args.git_sha else "ref"
    deploy_value = args.git_sha or args.git_ref or ""
    trace(
        "VPS_RUNNER",
        f"START strategy={strategy_name} deploy={deploy_label}:{deploy_value} remote_dir={vps_config.remote_dir}",
    )

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

    try:
        materialize_remote_runtime_env(client, vps_config)
    except (OSError, RuntimeError) as exc:
        client.close()
        trace("VPS_RUNNER", f"Runtime env upload failed: {exc}")
        print(str(exc), file=sys.stderr)
        sys.exit(1)

    cmd = build_remote_command(vps_config, family, resolved_sha)
    trace("VPS_RUNNER", f"SSH EXEC: {cmd}")
    t1 = time.time()
    # Controller runs are long-lived; do not enforce a 10-minute SSH timeout.
    _stdin, stdout, stderr = client.exec_command(cmd)
    exit_code, out, err = _stream_remote_command(stdout, stderr)
    elapsed = time.time() - t1

    client.close()

    trace_ssh(cmd, exit_code, out, err)
    trace(
        "VPS_RUNNER",
        f"DONE exit={exit_code} elapsed={elapsed:.1f}s stdout_len={len(out)} stderr_len={len(err)}",
    )

    sys.exit(exit_code)


if __name__ == "__main__":
    main()
