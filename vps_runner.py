#!/usr/bin/env python3
"""VPS runner using Git-based deployment for strategy controller runs.

Usage: python3 vps_runner.py --strategy <strategy-name> --git-ref <branch|tag|sha>

1. SSH to the VPS
2. Clone/fetch AUTORESEARCH_GIT_REPO at the requested --git-ref
3. Resolve that ref to an exact commit SHA and check it out detached
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

from autoresearch_constants import ENV_TRACE_MODE, PREPARE_RESULT_MARKER, TRACE_MODE_TRANSACTION
from autoresearch_logging import get_logger
from backtest.data_universe import DATA_ROOT_ENV
from strategies import STRATEGIES
from strategy_family import StrategyFamily, load_family
from trace_sdk import trace, trace_ssh

log = get_logger(__name__)

KNOWN_HOSTS_ENV = "AUTORESEARCH_KNOWN_HOSTS"
CURRENT_SHA_MARKER = "AUTORESEARCH_CURRENT_SHA"
RESOLVED_SHA_MARKER = "AUTORESEARCH_RESOLVED_SHA"
ACTIVE_RUN_MARKER = "AUTORESEARCH_ACTIVE_RUN"
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
    "AUTORESEARCH_KNOWN_HOSTS",
    "AUTORESEARCH_VPS_HOST",
    "AUTORESEARCH_VPS_KEY",
    "AUTORESEARCH_VPS_USER",
}
REMOTE_RUNTIME_ENV_PERSISTED_KEYS = {
    "AUTORESEARCH_GIT_REF",
    "AUTORESEARCH_GIT_SHA",
    "AUTORESEARCH_IMPROVEMENT_HALO",
    "AUTORESEARCH_IMPROVEMENT_HALO_APPLY",
    "AUTORESEARCH_IMPROVEMENT_RATCHET",
    "AUTORESEARCH_IMPROVEMENT_REFLEXION",
    "AUTORESEARCH_IMPROVEMENT_RECURSIVE_IMPROVE",
    "AUTORESEARCH_JOB",
    "AUTORESEARCH_PYTHON_BIN",
    "AUTORESEARCH_RECURSIVE_IMPROVE_COMMAND",
    "AUTORESEARCH_RECURSIVE_IMPROVE_INTERVAL",
    "AUTORESEARCH_RECURSIVE_IMPROVE_MODEL",
    "AUTORESEARCH_RECURSIVE_IMPROVE_TIMEOUT_SECONDS",
    "AUTORESEARCH_RESOLVED_SHA",
    "AUTORESEARCH_VPS",
    "AUTORESEARCH_VPS_DIR",
    "AUTORESEARCH_DATA_ROOT",
}
REMOTE_RUNTIME_ENV_DEFAULTS = {
    "AUTORESEARCH_IMPROVEMENT_REFLEXION": "1",
}
PREPARE_SUCCESS_GRACE_SECONDS = 5.0
PREPARE_COMMAND_TIMEOUT_SECONDS = 300.0
REMOTE_RELEASES_DIRNAME = "releases"
REMOTE_REPO_CACHE_DIRNAME = "repo-cache"
REMOTE_CURRENT_RELEASE_LINK = "current"


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
    data_root: str = ""

    @property
    def deploy_spec(self) -> str:
        return self.git_ref


def config_from_env(*, git_ref: str) -> VPSConfig:
    required = (
        "AUTORESEARCH_VPS_HOST",
        "AUTORESEARCH_VPS_USER",
        "AUTORESEARCH_VPS_KEY",
        "AUTORESEARCH_VPS_DIR",
        "AUTORESEARCH_GIT_REPO",
    )
    missing = [name for name in required if not os.environ.get(name)]
    if missing:
        raise ValueError("Missing VPS configuration environment variables: " + ", ".join(missing))
    remote_dir = os.environ["AUTORESEARCH_VPS_DIR"]
    _validate_remote_dir(remote_dir)
    _validate_git_ref(git_ref)
    vps_user = os.environ["AUTORESEARCH_VPS_USER"]
    data_root = os.environ.get(DATA_ROOT_ENV, "")
    if data_root:
        data_root = _expand_remote_user_path(data_root, vps_user)
        _validate_remote_data_root(data_root)
    return VPSConfig(
        host=os.environ["AUTORESEARCH_VPS_HOST"],
        user=vps_user,
        key=os.path.expanduser(os.environ["AUTORESEARCH_VPS_KEY"]),
        remote_dir=remote_dir,
        git_repo=os.environ["AUTORESEARCH_GIT_REPO"],
        git_ref=git_ref,
        data_root=data_root,
    )


def _validate_remote_dir(remote_dir: str) -> None:
    if remote_dir == LEGACY_REMOTE_ROOT:
        raise ValueError(
            "Refusing legacy VPS root /root/orb-research; set AUTORESEARCH_VPS_DIR "
            "to a fresh remote root before launching."
        )
    path = PurePosixPath(remote_dir)
    if not path.is_absolute() or str(path) != remote_dir:
        raise ValueError("AUTORESEARCH_VPS_DIR must be an absolute normalized POSIX path.")
    if remote_dir in REMOTE_ROOT_DENYLIST:
        raise ValueError(
            "AUTORESEARCH_VPS_DIR must point at a dedicated autoresearch checkout, "
            f"not {remote_dir}."
        )
    if ".." in path.parts:
        raise ValueError("AUTORESEARCH_VPS_DIR must not contain parent-directory segments.")
    if not re.fullmatch(r"/[A-Za-z0-9._/-]+", remote_dir):
        raise ValueError(
            "AUTORESEARCH_VPS_DIR must use only path-safe letters, digits, underscore, "
            "dot, dash, and slash."
        )
    if "autoresearch" not in path.name and "auto-research" not in path.name:
        raise ValueError(
            "AUTORESEARCH_VPS_DIR must end in a dedicated autoresearch checkout directory."
        )


def _validate_git_ref(git_ref: str) -> None:
    if re.fullmatch(r"[0-9a-fA-F]{7,40}", git_ref):
        return
    if git_ref.startswith(("+", "-")) or ":" in git_ref:
        raise ValueError(
            "AUTORESEARCH_GIT_REF must be a branch, tag, or full commit SHA, not a refspec."
        )
    if any(token in git_ref for token in ("..", "@{", "\\")):
        raise ValueError("AUTORESEARCH_GIT_REF contains unsafe Git ref syntax.")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/@-]*", git_ref):
        raise ValueError(
            "AUTORESEARCH_GIT_REF must be a path-safe branch, tag, or full commit SHA."
        )


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


def _remote_codex_home(user: str) -> str:
    return _expand_remote_user_path("~/.codex", user)


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


def _persisted_runtime_env_items() -> list[tuple[str, str]]:
    items = {
        key: value
        for key, value in _runtime_env_items()
        if key in REMOTE_RUNTIME_ENV_PERSISTED_KEYS
    }
    for key, value in REMOTE_RUNTIME_ENV_DEFAULTS.items():
        items.setdefault(key, value)
    return sorted(items.items())


def render_runtime_env_file() -> str:
    items = _persisted_runtime_env_items()
    if not items:
        return ""
    lines = [
        "# Autogenerated by vps_runner.py; sourced on the remote VPS controller.",
    ]
    for key, value in items:
        lines.append(f"export {key}={shlex.quote(value)}")
    return "\n".join(lines) + "\n"


def _remote_repo_cache_dir(config: VPSConfig) -> str:
    return str(PurePosixPath(config.remote_dir) / REMOTE_REPO_CACHE_DIRNAME)


def _remote_releases_dir(config: VPSConfig) -> str:
    return str(PurePosixPath(config.remote_dir) / REMOTE_RELEASES_DIRNAME)


def _remote_current_release_link(config: VPSConfig) -> str:
    return str(PurePosixPath(config.remote_dir) / REMOTE_CURRENT_RELEASE_LINK)


def _remote_release_dir(config: VPSConfig, resolved_sha: str) -> str:
    return str(PurePosixPath(_remote_releases_dir(config)) / resolved_sha)


def _render_release_symlink_bootstrap(
    config: VPSConfig, family: StrategyFamily, resolved_sha: str
) -> str:
    release_dir = _remote_release_dir(config, resolved_sha)
    runtime_root = config.remote_dir
    shared_dirs = [
        ".venv",
        "runtime",
        "logs",
    ]
    payload = {
        "release_dir": release_dir,
        "runtime_root": runtime_root,
        "shared_dirs": shared_dirs,
    }
    return (
        "{ python3 - <<'PY'\n"
        "import json\n"
        "import os\n"
        "import pathlib\n"
        "import shutil\n"
        f"payload = json.loads({json.dumps(json.dumps(payload))})\n"
        "release_dir = pathlib.Path(payload['release_dir'])\n"
        "runtime_root = pathlib.Path(payload['runtime_root'])\n"
        "for rel in payload['shared_dirs']:\n"
        "    target = runtime_root / rel\n"
        "    target.mkdir(parents=True, exist_ok=True)\n"
        "    link = release_dir / rel\n"
        "    if link.is_symlink() or link.exists():\n"
        "        if link.is_dir() and not link.is_symlink():\n"
        "            shutil.rmtree(link)\n"
        "        else:\n"
        "            link.unlink()\n"
        "    link.parent.mkdir(parents=True, exist_ok=True)\n"
        "    os.symlink(target, link)\n"
        "PY\n"
        "}"
    )


def build_git_prepare_command(config: VPSConfig) -> str:
    remote_dir = shlex.quote(config.remote_dir)
    remote_parent = shlex.quote(str(PurePosixPath(config.remote_dir).parent))
    repo_cache_dir = shlex.quote(_remote_repo_cache_dir(config))
    releases_dir = shlex.quote(_remote_releases_dir(config))
    current_link = shlex.quote(_remote_current_release_link(config))
    git_repo = shlex.quote(config.git_repo)
    # Fetch strategy differs: full 40-char SHAs cannot be used as refspecs,
    # so fetch all refs and verify the SHA is present locally.
    # Branch/tag refs use a refspec so --prune removes deleted remote refs.
    if re.fullmatch(r"[0-9a-fA-F]{7,40}", config.git_ref):
        git_sha_q = shlex.quote(config.git_ref)
        fetch_and_resolve = (
            "git fetch --prune origin && "
            f"resolved=$(git rev-parse --verify {git_sha_q}^{{commit}}) && "
        )
    else:
        deploy_spec = shlex.quote(config.deploy_spec)
        fetch_and_resolve = (
            f"git fetch --prune origin {deploy_spec} && "
            "resolved=$(git rev-parse --verify FETCH_HEAD^{commit}) && "
        )
    return (
        "set -e && "
        f"mkdir -p {remote_parent} {remote_dir} {releases_dir} && "
        f"if [ ! -d {repo_cache_dir}/.git ]; then "
        f"git clone --no-checkout {git_repo} {repo_cache_dir}; "
        "fi && "
        f"cd {repo_cache_dir} && "
        f"git remote set-url origin {git_repo} && "
        f"{fetch_and_resolve}"
        f"release_dir={releases_dir}"
        '"/$resolved" && '
        'if [ ! -d "$release_dir" ]; then '
        'tmp_release="${release_dir}.tmp.$$" && '
        'rm -rf "$tmp_release" && mkdir -p "$tmp_release" && '
        'git archive "$resolved" | tar -x -C "$tmp_release" && '
        'mv "$tmp_release" "$release_dir"; '
        "fi && "
        f'ln -sfn "$release_dir" {current_link} && '
        f"printf '{RESOLVED_SHA_MARKER} %s\\n' \"$resolved\""
    )


def build_git_status_command(config: VPSConfig) -> str:
    """Report the existing VPS checkout SHA and requested Git ref without changing files."""
    remote_dir = shlex.quote(config.remote_dir)
    repo_cache_dir = shlex.quote(_remote_repo_cache_dir(config))
    current_link = shlex.quote(_remote_current_release_link(config))
    git_repo = shlex.quote(config.git_repo)
    if re.fullmatch(r"[0-9a-fA-F]{7,40}", config.git_ref):
        git_ref_q = shlex.quote(config.git_ref)
        fetch_and_resolve = (
            f"git -c remote.origin.url={git_repo} fetch --prune origin && "
            f"resolved=$(git rev-parse --verify {git_ref_q}^{{commit}}) && "
        )
    else:
        deploy_spec = shlex.quote(config.deploy_spec)
        fetch_and_resolve = (
            f"git -c remote.origin.url={git_repo} fetch --prune origin {deploy_spec} && "
            "resolved=$(git rev-parse --verify FETCH_HEAD^{commit}) && "
        )
    return (
        "set -e && "
        'repo_dir="" && current="missing" && '
        f"if [ -d {repo_cache_dir}/.git ]; then "
        f"repo_dir={repo_cache_dir} && "
        f'if [ -L {current_link} ]; then current=$(basename "$(readlink {current_link})"); fi; '
        f"elif [ -d {remote_dir}/.git ]; then "
        f"repo_dir={remote_dir} && "
        'current=$(git -C "$repo_dir" rev-parse --verify HEAD^{commit}); '
        "else "
        f"printf '{CURRENT_SHA_MARKER} missing\\n'; "
        "exit 0; "
        "fi && "
        'cd "$repo_dir" && '
        f"{fetch_and_resolve}"
        f"printf '{CURRENT_SHA_MARKER} %s\\n' \"$current\" && "
        f"printf '{RESOLVED_SHA_MARKER} %s\\n' \"$resolved\""
    )


def build_activity_probe_command(config: VPSConfig, family: StrategyFamily) -> str:
    remote_dir = shlex.quote(config.remote_dir)
    repo_cache_dir = shlex.quote(_remote_repo_cache_dir(config))
    return (
        "set -e && "
        f"if [ ! -d {repo_cache_dir}/.git ] && [ ! -d {remote_dir}/.git ]; then "
        f"printf '{ACTIVE_RUN_MARKER} %s\\n' "
        + shlex.quote(json.dumps({"active": False, "reason": "missing_checkout"}))
        + "; exit 0; fi && "
        f"cd {remote_dir} && "
        "python3 - <<'PY'\n"
        "import json\n"
        "import pathlib\n"
        "import subprocess\n"
        f"state_path = pathlib.Path({family.name!r} + '_autoresearch.next.json')\n"
        "payload = {\n"
        "    'active': False,\n"
        "    'process_running': False,\n"
        "    'state_active': False,\n"
        "    'reason': '',\n"
        "    'state': None,\n"
        "    'job': None,\n"
        "    'research_round': None,\n"
        "    'research_round_in_progress': None,\n"
        "    'next_action_type': None,\n"
        "    'builder_thesis_id': None,\n"
        "    'state_error': '',\n"
        "}\n"
        "state = {}\n"
        "next_action = {}\n"
        f"controller_pattern = 'autoresearch_controller.py --family {family.name}'\n"
        "proc = subprocess.run(['ps', '-eo', 'command='], capture_output=True, text=True, check=False)\n"
        "payload['process_running'] = any(\n"
        "    controller_pattern in line and 'pgrep -af' not in line\n"
        "    for line in proc.stdout.splitlines()\n"
        ")\n"
        "if state_path.exists():\n"
        "    try:\n"
        "        raw = state_path.read_text()\n"
        "        state = json.loads(raw) if raw.strip() else {}\n"
        "    except (OSError, json.JSONDecodeError) as exc:\n"
        "        payload['state_error'] = str(exc)\n"
        "        state = {}\n"
        "    payload['state'] = state.get('state')\n"
        "    payload['job'] = state.get('job')\n"
        "    payload['research_round'] = state.get('research_round')\n"
        "    payload['research_round_in_progress'] = state.get('research_round_in_progress')\n"
        "    next_action = state.get('next_action') if isinstance(state.get('next_action'), dict) else {}\n"
        "    payload['next_action_type'] = next_action.get('type')\n"
        "    payload['builder_thesis_id'] = next_action.get('builder_thesis_id') or state.get('halted_thesis_id')\n"
        "    state_active = bool(state.get('research_round_in_progress')) or (\n"
        "        state.get('state') == 'building' and next_action.get('type') == 'builder_running'\n"
        "    ) or (\n"
        "        state.get('state') == 'running' and next_action.get('type') == 'run_experiment'\n"
        "    )\n"
        "    payload['state_active'] = bool(state_active)\n"
        "if payload['process_running'] and payload['state_active']:\n"
        "    payload['active'] = True\n"
        "    if state.get('research_round_in_progress'):\n"
        "        payload['reason'] = f\"research_round_in_progress={state.get('research_round_in_progress')}\"\n"
        "    elif state.get('state') == 'running' and next_action.get('type') == 'run_experiment':\n"
        "        payload['reason'] = f\"run_experiment config={next_action.get('config') or '?'}\"\n"
        "    else:\n"
        "        payload['reason'] = f\"builder_running thesis={payload['builder_thesis_id'] or '?'}\"\n"
        "elif payload['process_running']:\n"
        "    payload['active'] = True\n"
        "    payload['reason'] = 'controller_process_running_without_active_state'\n"
        f"print({ACTIVE_RUN_MARKER!r} + ' ' + json.dumps(payload, sort_keys=True))\n"
        "PY"
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


def parse_current_sha(output: str) -> str | None:
    match = re.search(
        rf"^{CURRENT_SHA_MARKER} ([0-9a-f]{{40}}|missing)$",
        output,
        flags=re.MULTILINE,
    )
    if not match:
        raise RuntimeError("VPS Git status did not report the current commit SHA")
    current_sha = match.group(1)
    return None if current_sha == "missing" else current_sha


def parse_activity_probe(output: str) -> dict[str, object]:
    match = re.search(rf"^{ACTIVE_RUN_MARKER} (.+)$", output, flags=re.MULTILINE)
    if not match:
        raise RuntimeError("VPS activity probe did not report active-run status")
    payload = json.loads(match.group(1))
    if not isinstance(payload, dict):
        raise RuntimeError("VPS activity probe payload was not a JSON object")
    return payload


def parse_prepare_result(output: str) -> dict[str, object]:
    match = re.search(rf"^{PREPARE_RESULT_MARKER} (.+)$", output, flags=re.MULTILINE)
    if not match:
        raise RuntimeError("VPS prepare command did not report a prepare result")
    payload = json.loads(match.group(1))
    if not isinstance(payload, dict):
        raise RuntimeError("VPS prepare result payload was not a JSON object")
    if payload.get("ok") is not True:
        raise RuntimeError(f"VPS prepare command reported a non-success payload: {payload}")
    return payload


def _build_remote_bootstrap_segments(
    config: VPSConfig,
    family: StrategyFamily,
    resolved_sha: str,
    *,
    skip_dependency_install: bool = False,
    include_release_bootstrap: bool = True,
) -> list[str]:
    release_dir = _remote_release_dir(config, resolved_sha)
    segments = [
        "set -e",
        f"if [ -f {shlex.quote(str(PurePosixPath(config.remote_dir) / REMOTE_RUNTIME_ENV_FILENAME))} ]; then "
        f"set -a; . {shlex.quote(str(PurePosixPath(config.remote_dir) / REMOTE_RUNTIME_ENV_FILENAME))}; set +a; fi",
        f"export AUTORESEARCH_RESOLVED_SHA={shlex.quote(resolved_sha)}",
        f"export AUTORESEARCH_RUNTIME_ROOT={shlex.quote(config.remote_dir)}",
        f"export CODEX_HOME={shlex.quote(_remote_codex_home(config.user))}",
    ]
    if include_release_bootstrap:
        segments.append(_render_release_symlink_bootstrap(config, family, resolved_sha))
    segments.append(f"cd {shlex.quote(release_dir)}")
    if config.data_root:
        segments.append(f"export {DATA_ROOT_ENV}={shlex.quote(config.data_root)}")
    dependency_install_segments = [
        "deps_fingerprint=$(python3 -c 'import hashlib, pathlib; "
        'p = pathlib.Path("pyproject.toml"); '
        'print(hashlib.sha256(p.read_bytes()).hexdigest() if p.exists() else "missing")'
        "')",
        'if [ -f ".venv/.autoresearch-deps.sha256" ] && '
        '[ "$(cat .venv/.autoresearch-deps.sha256)" != "$deps_fingerprint" ]; '
        'then rm -rf "$AUTORESEARCH_RUNTIME_ROOT/.venv"; fi',
        'if [ ! -x ".venv/bin/python" ]; then python3 -m venv "$AUTORESEARCH_RUNTIME_ROOT/.venv"; fi',
        "python_bin=.venv/bin/python",
        'export AUTORESEARCH_PYTHON_BIN="$python_bin"',
        'if [ ! -f ".venv/.autoresearch-deps.sha256" ] || '
        '[ "$(cat .venv/.autoresearch-deps.sha256)" != "$deps_fingerprint" ]; then '
        '"$python_bin" -m pip install -e . && '
        'printf "%s\\n" "$deps_fingerprint" > .venv/.autoresearch-deps.sha256; fi',
    ]
    if skip_dependency_install:
        fallback_install = " && ".join(dependency_install_segments)
        segments.extend(
            [
                "deps_fingerprint=$(python3 -c 'import hashlib, pathlib; "
                'p = pathlib.Path("pyproject.toml"); '
                'print(hashlib.sha256(p.read_bytes()).hexdigest() if p.exists() else "missing")'
                "')",
                'if [ ! -x ".venv/bin/python" ] || '
                '[ ! -f ".venv/.autoresearch-deps.sha256" ] || '
                '[ "$(cat .venv/.autoresearch-deps.sha256)" != "$deps_fingerprint" ]; then '
                "echo 'Existing checkout missing venv or deps; installing.' "
                f"&& {fallback_install}; fi",
                "python_bin=.venv/bin/python",
                'export AUTORESEARCH_PYTHON_BIN="$python_bin"',
            ]
        )
    else:
        segments.extend(dependency_install_segments)
    return segments


def build_remote_prepare_command(
    config: VPSConfig,
    family: StrategyFamily,
    resolved_sha: str,
    *,
    resume_current_job: bool = False,
    skip_dependency_install: bool = False,
) -> str:
    segments = _build_remote_bootstrap_segments(
        config,
        family,
        resolved_sha,
        skip_dependency_install=skip_dependency_install,
        include_release_bootstrap=True,
    )
    controller_mode = " --resume-current-job" if resume_current_job else " --fresh-job"
    controller_base = (
        f'"$python_bin" autoresearch_controller.py --family {shlex.quote(family.name)}'
    )
    segments.append(f"export {ENV_TRACE_MODE}={shlex.quote(TRACE_MODE_TRANSACTION)}")
    segments.append(controller_base + controller_mode + " --prepare-launch-state-only")
    return " && ".join(segments)


def build_remote_run_command(
    config: VPSConfig,
    family: StrategyFamily,
    resolved_sha: str,
    *,
    skip_dependency_install: bool = False,
) -> str:
    segments = _build_remote_bootstrap_segments(
        config,
        family,
        resolved_sha,
        skip_dependency_install=skip_dependency_install,
        include_release_bootstrap=False,
    )
    segments.append(f"unset {ENV_TRACE_MODE} || true")
    segments.append(
        f'"$python_bin" autoresearch_controller.py --family {shlex.quote(family.name)} --run-current-state'
    )
    return " && ".join(segments)


def build_remote_command(
    config: VPSConfig,
    family: StrategyFamily,
    resolved_sha: str,
    *,
    resume_current_job: bool = False,
    skip_dependency_install: bool = False,
) -> str:
    prepare_cmd = build_remote_prepare_command(
        config,
        family,
        resolved_sha,
        resume_current_job=resume_current_job,
        skip_dependency_install=skip_dependency_install,
    )
    run_cmd = build_remote_run_command(
        config,
        family,
        resolved_sha,
        skip_dependency_install=skip_dependency_install,
    )
    return f"{prepare_cmd} && {run_cmd}"


def _should_skip_git_prepare(*, current_sha: str | None, resolved_sha: str) -> bool:
    return bool(current_sha and current_sha == resolved_sha)


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


def materialize_remote_codex_auth(
    client: paramiko.SSHClient,
    config: VPSConfig,
) -> str | None:
    """Upload the local Codex auth bundle so remote codex exec can authenticate."""
    local_auth = Path.home() / ".codex" / "auth.json"
    if not local_auth.exists():
        return None

    try:
        payload = json.loads(local_auth.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        log.warning("Skipping Codex auth sync: unreadable auth.json: %s", exc)
        return None

    tokens = payload.get("tokens") if isinstance(payload, dict) else None
    if not isinstance(tokens, dict) or not tokens.get("access_token"):
        return None

    remote_home = _expand_remote_user_path("~", config.user)
    remote_codex_dir = f"{remote_home}/.codex"
    remote_path = f"{remote_codex_dir}/auth.json"
    sftp = client.open_sftp()
    try:
        _sftp_mkdir_p(sftp, remote_codex_dir)
        sftp.chmod(remote_codex_dir, 0o700)
        sftp.put(str(local_auth), remote_path)
        sftp.chmod(remote_path, 0o600)
    finally:
        sftp.close()

    trace("VPS_RUNNER", "Materialized remote Codex auth bundle")
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
    """Upload ignored round-scoped generated configs as run inputs, not code deployment."""
    rel_path = _relative_repo_path(config_path)
    if _is_git_tracked(rel_path):
        return rel_path.as_posix()

    parts = rel_path.parts
    is_round_selected_config = (
        len(parts) == 6
        and parts[0] == "runtime"
        and parts[1] == "jobs"
        and parts[2].startswith("job-")
        and parts[3] == "research"
        and parts[4].startswith("round-")
        and parts[5] == "selected_config.json"
    )
    if not is_round_selected_config:
        raise RuntimeError(
            "VPS Git deployment only accepts tracked config files or generated "
            "runtime/jobs/job-N/research/round-*/selected_config.json inputs; "
            f"refused untracked config: {rel_path.as_posix()}"
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
    except (paramiko.SSHException, OSError) as exc:
        client.close()
        raise RuntimeError(
            "VPS SSH connection failed with host-key verification enforced. "
            f"Add {vps_config.host} to ~/.ssh/known_hosts or set {KNOWN_HOSTS_ENV} "
            f"to a known_hosts file containing the VPS host key. Underlying error: {exc}"
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
    except (json.JSONDecodeError, OSError) as exc:
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


def _stream_remote_prepare_command(
    stdout: paramiko.channel.ChannelFile,
    stderr: paramiko.channel.ChannelStderrFile,
    *,
    success_marker: str = PREPARE_RESULT_MARKER,
    linger_after_success_seconds: float = PREPARE_SUCCESS_GRACE_SECONDS,
    timeout_seconds: float = PREPARE_COMMAND_TIMEOUT_SECONDS,
) -> tuple[int, str, str, bool]:
    channel = stdout.channel
    out_chunks: list[str] = []
    err_chunks: list[str] = []
    success_seen_at: float | None = None
    forced_close = False
    started_at = time.time()

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
            if success_seen_at is None and success_marker in "".join(out_chunks):
                success_seen_at = time.time()

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

        if timeout_seconds >= 0 and time.time() - started_at >= timeout_seconds:
            forced_close = True
            channel.close()
            err_chunks.append("prepare command timed out before reporting completion")
            break

        if (
            success_seen_at is not None
            and linger_after_success_seconds >= 0
            and time.time() - success_seen_at >= linger_after_success_seconds
        ):
            forced_close = True
            channel.close()
            err_chunks.append(
                "prepare command emitted success marker but did not exit within grace period"
            )
            break

        if not progressed:
            time.sleep(0.1)

    if forced_close:
        exit_code = 124
    else:
        exit_code = channel.recv_exit_status()
    return exit_code, "".join(out_chunks), "".join(err_chunks), forced_close


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run autoresearch controller on the VPS")
    parser.add_argument("--strategy", required=True, choices=sorted(STRATEGIES))
    parser.add_argument(
        "--git-ref",
        "--git-sha",
        dest="git_ref",
        required=True,
        help="Git branch, tag, or commit SHA to deploy on VPS",
    )
    parser.add_argument(
        "--fresh-job",
        action="store_true",
        help="Create a new job on the VPS and ignore prior job-scoped work queues",
    )
    parser.add_argument(
        "--resume-current-job",
        action="store_true",
        help="Resume a recoverable current job on the VPS instead of creating a new job",
    )
    parser.add_argument(
        "--skip-deploy-if-current",
        action="store_true",
        help=(
            "Skip Git checkout and dependency install if the VPS checkout already matches "
            "--git-ref."
        ),
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Override the VPS active-round safety check and deploy anyway.",
    )
    return parser


def main():
    _load_local_env_file()

    parser = _build_arg_parser()
    args = parser.parse_args()
    if args.fresh_job and args.resume_current_job:
        parser.error("--fresh-job and --resume-current-job are mutually exclusive")

    strategy_name = args.strategy
    family = load_family(strategy_name)
    vps_config = config_from_env(git_ref=args.git_ref)
    trace(
        "VPS_RUNNER",
        f"START strategy={strategy_name} git_ref={args.git_ref} remote_dir={vps_config.remote_dir}",
    )

    trace("VPS_RUNNER", f"Connecting to {vps_config.host} as {vps_config.user}")
    try:
        client = connect_verified_ssh_client(vps_config)
    except (FileNotFoundError, RuntimeError) as exc:
        log.error("SSH setup failed: %s", exc)
        trace("VPS_RUNNER", f"SSH setup failed: {exc}")
        print(str(exc), file=sys.stderr)
        sys.exit(1)
    trace("VPS_RUNNER", "Connected")

    if not args.force:
        activity_cmd = build_activity_probe_command(vps_config, family)
        trace("VPS_RUNNER", f"SSH ACTIVE_CHECK: {activity_cmd}")
        _, activity_stdout, activity_stderr = client.exec_command(activity_cmd, timeout=60)
        activity_out = activity_stdout.read().decode()
        activity_err = activity_stderr.read().decode()
        safe_activity_out = redact_secrets(activity_out, vps_config)
        safe_activity_err = redact_secrets(activity_err, vps_config)
        activity_exit = activity_stdout.channel.recv_exit_status()
        trace_ssh(activity_cmd, activity_exit, safe_activity_out, safe_activity_err)
        if activity_exit != 0:
            client.close()
            if safe_activity_out:
                print(safe_activity_out, end="")
            if safe_activity_err:
                print(safe_activity_err, end="", file=sys.stderr)
            sys.exit(activity_exit)
        try:
            activity = parse_activity_probe(activity_out)
        except (RuntimeError, json.JSONDecodeError) as exc:
            client.close()
            log.error("Failed to parse active-run probe: %s", exc)
            print(str(exc), file=sys.stderr)
            sys.exit(1)
        if activity.get("active"):
            client.close()
            reason = str(activity.get("reason") or "active round running")
            message = (
                "Refusing VPS deploy/resume while an active research/builder round is running. "
                f"family={family.name} job={activity.get('job')} state={activity.get('state')} "
                f"next_action={activity.get('next_action_type')} reason={reason}. "
                "Pass --force to override."
            )
            trace("VPS_RUNNER", message)
            print(message, file=sys.stderr)
            sys.exit(2)

    skip_dependency_install = False
    resolved_sha = ""
    status_cmd = build_git_status_command(vps_config)
    safe_status_cmd = build_git_status_command(
        replace(vps_config, git_repo=redact_git_repo_url(vps_config.git_repo))
    )
    trace("VPS_RUNNER", f"SSH STATUS: {safe_status_cmd}")
    _, status_stdout, status_stderr = client.exec_command(status_cmd, timeout=600)
    status_out = status_stdout.read().decode()
    status_err = status_stderr.read().decode()
    safe_status_out = redact_secrets(status_out, vps_config)
    safe_status_err = redact_secrets(status_err, vps_config)
    status_exit = status_stdout.channel.recv_exit_status()
    trace_ssh(safe_status_cmd, status_exit, safe_status_out, safe_status_err)
    if status_exit != 0:
        client.close()
        if safe_status_out:
            print(safe_status_out, end="")
        if safe_status_err:
            print(safe_status_err, end="", file=sys.stderr)
        sys.exit(status_exit)
    try:
        current_sha = parse_current_sha(status_out)
        resolved_sha = parse_resolved_sha(status_out) if current_sha else ""
    except RuntimeError as exc:
        client.close()
        log.error("Failed to parse Git status: %s", exc)
        print(str(exc), file=sys.stderr)
        sys.exit(1)
    if _should_skip_git_prepare(current_sha=current_sha, resolved_sha=resolved_sha):
        skip_dependency_install = True
        trace(
            "VPS_RUNNER",
            (
                f"Remote already at sha={resolved_sha}; "
                "skipping Git prepare and using warm checkout"
            ),
        )

    if not skip_dependency_install:
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
            log.error("Failed to parse resolved SHA: %s", exc)
            print(str(exc), file=sys.stderr)
            sys.exit(1)
        trace(
            "VPS_RUNNER",
            f"Git prepare complete sha={resolved_sha} elapsed={time.time() - t0:.1f}s",
        )

    try:
        materialize_remote_runtime_env(client, vps_config)
    except (OSError, RuntimeError) as exc:
        client.close()
        log.error("Runtime env upload failed: %s", exc)
        trace("VPS_RUNNER", f"Runtime env upload failed: {exc}")
        print(str(exc), file=sys.stderr)
        sys.exit(1)

    try:
        materialize_remote_codex_auth(client, vps_config)
    except Exception as exc:  # noqa: BLE001 - auth sync is optional and must fail open
        log.warning("Codex auth upload failed, continuing without it: %s", exc)
        trace("VPS_RUNNER", f"Codex auth upload failed: {exc}")

    prepare_cmd = build_remote_prepare_command(
        vps_config,
        family,
        resolved_sha,
        resume_current_job=args.resume_current_job,
        skip_dependency_install=skip_dependency_install,
    )
    safe_prepare_exec_cmd = prepare_cmd
    trace("VPS_RUNNER", f"SSH PREPARE_EXEC: {safe_prepare_exec_cmd}")
    t1 = time.time()
    try:
        _stdin, stdout, stderr = client.exec_command(prepare_cmd)
        prepare_exec_code, prepare_exec_out, prepare_exec_err, forced_close = (
            _stream_remote_prepare_command(stdout, stderr)
        )
    except Exception as exc:
        log.error("Remote prepare command stream failed: %s", exc)
        prepare_exec_code, prepare_exec_out, prepare_exec_err, forced_close = (
            1,
            "",
            str(exc),
            False,
        )
    trace_ssh(
        prepare_cmd,
        prepare_exec_code,
        redact_secrets(prepare_exec_out, vps_config),
        redact_secrets(prepare_exec_err, vps_config),
    )
    if prepare_exec_code != 0:
        client.close()
        if prepare_exec_out:
            print(redact_secrets(prepare_exec_out, vps_config), end="")
        if prepare_exec_err:
            print(redact_secrets(prepare_exec_err, vps_config), end="", file=sys.stderr)
        sys.exit(prepare_exec_code)
    try:
        prepare_result = parse_prepare_result(prepare_exec_out)
    except (RuntimeError, json.JSONDecodeError) as exc:
        client.close()
        log.error("Failed to parse prepare result: %s", exc)
        print(str(exc), file=sys.stderr)
        sys.exit(1)
    trace(
        "VPS_RUNNER",
        (
            f"Prepare complete job={prepare_result.get('job')} state={prepare_result.get('state')} "
            f"forced_close={forced_close} elapsed={time.time() - t1:.1f}s"
        ),
    )

    run_cmd = build_remote_run_command(
        vps_config,
        family,
        resolved_sha,
        skip_dependency_install=skip_dependency_install,
    )
    trace("VPS_RUNNER", f"SSH RUN_EXEC: {run_cmd}")
    t2 = time.time()
    # Controller runs are long-lived; do not enforce a 10-minute SSH timeout.
    try:
        _stdin, stdout, stderr = client.exec_command(run_cmd)
        exit_code, out, err = _stream_remote_command(stdout, stderr)
    except Exception as exc:
        log.error("Remote command stream failed: %s", exc)
        exit_code, out, err = 1, "", str(exc)
    finally:
        client.close()
    elapsed = time.time() - t2

    trace_ssh(run_cmd, exit_code, redact_secrets(out, vps_config), redact_secrets(err, vps_config))
    trace(
        "VPS_RUNNER",
        f"DONE exit={exit_code} elapsed={elapsed:.1f}s stdout_len={len(out)} stderr_len={len(err)}",
    )

    sys.exit(exit_code)


if __name__ == "__main__":
    main()
