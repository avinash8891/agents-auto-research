from __future__ import annotations

import json
import os
import shlex
from pathlib import Path, PureWindowsPath

import pytest

from strategy_family import load_family
from vps_runner import (
    VPSConfig,
    _build_arg_parser,
    _localize_remote_result_output,
    _sftp_mkdir_p,
    build_git_prepare_command,
    build_remote_command,
    config_from_env,
    create_verified_ssh_client,
    materialize_remote_codex_auth,
    materialize_remote_config_if_needed,
    materialize_remote_runtime_env,
    parse_resolved_sha,
    redact_git_repo_url,
    redact_secrets,
    render_runtime_env_file,
)

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_vps_config_reads_remote_details_from_environment(monkeypatch) -> None:
    monkeypatch.delenv("AUTORESEARCH_DATA_ROOT", raising=False)
    monkeypatch.setenv("AUTORESEARCH_VPS_HOST", "203.0.113.10")
    monkeypatch.setenv("AUTORESEARCH_VPS_USER", "researcher")
    monkeypatch.setenv("AUTORESEARCH_VPS_KEY", "~/.ssh/research_key")
    monkeypatch.setenv("AUTORESEARCH_GIT_REPO", "https://github.com/example/repo.git")
    monkeypatch.setenv("AUTORESEARCH_VPS_DIR", "/srv/autoresearch")
    config = config_from_env(git_ref="feature/ema")

    assert config.host == "203.0.113.10"
    assert config.user == "researcher"
    assert config.key == os.path.expanduser("~/.ssh/research_key")
    assert config.remote_dir == "/srv/autoresearch"
    assert config.git_repo == "https://github.com/example/repo.git"
    assert config.git_ref == "feature/ema"
    assert config.data_root == ""


def test_vps_config_reads_optional_remote_data_root_from_environment(monkeypatch) -> None:
    monkeypatch.setenv("AUTORESEARCH_VPS_HOST", "203.0.113.10")
    monkeypatch.setenv("AUTORESEARCH_VPS_USER", "researcher")
    monkeypatch.setenv("AUTORESEARCH_VPS_KEY", "~/.ssh/research_key")
    monkeypatch.setenv("AUTORESEARCH_GIT_REPO", "https://github.com/example/repo.git")
    monkeypatch.setenv("AUTORESEARCH_DATA_ROOT", "/data/autoresearch")
    monkeypatch.setenv("AUTORESEARCH_VPS_DIR", "/srv/autoresearch")
    config = config_from_env(git_ref="feature/ema")

    assert config.data_root == "/data/autoresearch"


def test_vps_config_expands_tilde_data_root_for_root_user(monkeypatch) -> None:
    monkeypatch.setenv("AUTORESEARCH_VPS_HOST", "203.0.113.10")
    monkeypatch.setenv("AUTORESEARCH_VPS_USER", "root")
    monkeypatch.setenv("AUTORESEARCH_VPS_KEY", "~/.ssh/research_key")
    monkeypatch.setenv("AUTORESEARCH_GIT_REPO", "https://github.com/example/repo.git")
    monkeypatch.setenv("AUTORESEARCH_DATA_ROOT", "~/autoresearch-data")
    monkeypatch.setenv("AUTORESEARCH_VPS_DIR", "/root/autoresearch")
    config = config_from_env(git_ref="feature/ema")

    assert config.data_root == "/root/autoresearch-data"


def test_vps_config_expands_tilde_data_root_for_non_root_user(monkeypatch) -> None:
    monkeypatch.setenv("AUTORESEARCH_VPS_HOST", "203.0.113.10")
    monkeypatch.setenv("AUTORESEARCH_VPS_USER", "researcher")
    monkeypatch.setenv("AUTORESEARCH_VPS_KEY", "~/.ssh/research_key")
    monkeypatch.setenv("AUTORESEARCH_GIT_REPO", "https://github.com/example/repo.git")
    monkeypatch.setenv("AUTORESEARCH_DATA_ROOT", "~/autoresearch-data")
    monkeypatch.setenv("AUTORESEARCH_VPS_DIR", "/home/researcher/autoresearch")
    config = config_from_env(git_ref="feature/ema")

    assert config.data_root == "/home/researcher/autoresearch-data"


def test_vps_config_requires_explicit_environment(monkeypatch) -> None:
    for name in (
        "AUTORESEARCH_VPS_HOST",
        "AUTORESEARCH_VPS_USER",
        "AUTORESEARCH_VPS_KEY",
        "AUTORESEARCH_GIT_REPO",
    ):
        monkeypatch.delenv(name, raising=False)

    with pytest.raises(ValueError, match="AUTORESEARCH_VPS_HOST"):
        config_from_env(git_ref="feature/ema")


def test_vps_config_rejects_unsafe_git_refs(monkeypatch) -> None:
    monkeypatch.setenv("AUTORESEARCH_VPS_HOST", "203.0.113.10")
    monkeypatch.setenv("AUTORESEARCH_VPS_USER", "researcher")
    monkeypatch.setenv("AUTORESEARCH_VPS_KEY", "~/.ssh/research_key")
    monkeypatch.setenv("AUTORESEARCH_GIT_REPO", "https://github.com/example/repo.git")
    monkeypatch.setenv("AUTORESEARCH_VPS_DIR", "/srv/autoresearch")
    for bad_ref in ("feature/ema:refs/heads/main", "+main", "-main", "main..next", "main@{1}"):
        with pytest.raises(ValueError, match="AUTORESEARCH_GIT_REF"):
            config_from_env(git_ref=bad_ref)

    assert (
        config_from_env(git_ref="0123456789abcdef0123456789abcdef01234567").git_ref
        == "0123456789abcdef0123456789abcdef01234567"
    )


def test_vps_runner_parser_accepts_git_sha_alias() -> None:
    parser = _build_arg_parser()

    ref_args = parser.parse_args(["--strategy", "ema", "--git-ref", "main"])
    sha_args = parser.parse_args(
        ["--strategy", "ema", "--git-sha", "0123456789abcdef0123456789abcdef01234567"]
    )

    assert ref_args.git_ref == "main"
    assert sha_args.git_ref == "0123456789abcdef0123456789abcdef01234567"


def test_vps_config_rejects_unsafe_remote_dirs(monkeypatch) -> None:
    monkeypatch.setenv("AUTORESEARCH_VPS_HOST", "203.0.113.10")
    monkeypatch.setenv("AUTORESEARCH_VPS_USER", "researcher")
    monkeypatch.setenv("AUTORESEARCH_VPS_KEY", "~/.ssh/research_key")
    monkeypatch.setenv("AUTORESEARCH_GIT_REPO", "https://github.com/example/repo.git")
    for bad_dir in ("autoresearch", "/", "/root", "/root/orb-research", "/tmp/research"):
        monkeypatch.setenv("AUTORESEARCH_VPS_DIR", bad_dir)
        with pytest.raises(ValueError, match="AUTORESEARCH_VPS_DIR|legacy VPS root"):
            config_from_env(git_ref="feature/ema")

    monkeypatch.setenv("AUTORESEARCH_VPS_DIR", "/srv/autoresearch-2026-05-02")
    assert config_from_env(git_ref="feature/ema").remote_dir == "/srv/autoresearch-2026-05-02"


def test_remote_command_runs_controller_for_family() -> None:
    family = load_family("ema")
    config = VPSConfig(
        host="203.0.113.10",
        user="researcher",
        key="/tmp/key",
        remote_dir="/srv/autoresearch with spaces; rm -rf /",
        git_repo="https://github.com/example/repo.git",
        git_ref="feature/ema",
    )

    resolved_sha = "0123456789abcdef0123456789abcdef01234567"
    command = build_remote_command(config, family, resolved_sha)

    assert f"cd {shlex.quote(config.remote_dir)}" in command
    assert "deps_fingerprint=$(python3 -c" in command
    assert ".venv/.autoresearch-deps.sha256" in command
    assert "then rm -rf .venv; fi" in command
    assert 'if [ ! -x ".venv/bin/python" ]; then python3 -m venv .venv; fi' in command
    assert "python_bin=.venv/bin/python" in command
    assert f"export AUTORESEARCH_RESOLVED_SHA={resolved_sha}" in command
    assert '"$python_bin" -m pip install -e .' in command
    assert 'printf "%s\\n" "$deps_fingerprint" > .venv/.autoresearch-deps.sha256' in command
    assert (
        f'"$python_bin" autoresearch_controller.py --family {shlex.quote(family.name)}'
    ) in command
    assert command.index("python_bin=.venv/bin/python") < command.index(
        '"$python_bin" -m pip install -e .'
    )
    assert command.index('"$python_bin" -m pip install -e .') < command.index(
        f'"$python_bin" autoresearch_controller.py --family {shlex.quote(family.name)}'
    )
    assert "/root/orb-research" not in command
    assert "backtest_5ema.py" not in command
    assert "scp" not in command.lower()


def test_remote_command_exports_optional_data_root() -> None:
    family = load_family("ema")
    config = VPSConfig(
        host="203.0.113.10",
        user="researcher",
        key="/tmp/key",
        remote_dir="/srv/autoresearch",
        git_repo="https://github.com/example/repo.git",
        git_ref="feature/ema",
        data_root="/data/autoresearch",
    )

    command = build_remote_command(config, family, "0" * 40)

    assert "export AUTORESEARCH_DATA_ROOT=/data/autoresearch &&" in command


def test_remote_command_sources_runtime_env_file() -> None:
    family = load_family("ema")
    config = VPSConfig(
        host="203.0.113.10",
        user="researcher",
        key="/tmp/key",
        remote_dir="/srv/autoresearch",
        git_repo="https://github.com/example/repo.git",
        git_ref="feature/ema",
    )

    command = build_remote_command(config, family, "0" * 40)

    assert (
        'if [ -f ".env.autoresearch" ]; then set -a; . ./.env.autoresearch; set +a; fi' in command
    )


def test_render_runtime_env_file_includes_runtime_env_and_skips_runner_only_keys(
    monkeypatch,
) -> None:
    monkeypatch.setenv("AUTORESEARCH_DISCORD_WEBHOOK_EMA", "https://example.com/webhook")
    monkeypatch.setenv("AUTORESEARCH_DATA_ROOT", "/data/autoresearch")
    monkeypatch.setenv("TRACELOOP_API_KEY", "trace-key")
    monkeypatch.setenv("AUTORESEARCH_VPS_KEY", "~/.ssh/research_key")
    monkeypatch.setenv("AUTORESEARCH_JOB", "9")

    content = render_runtime_env_file()

    assert "AUTORESEARCH_DATA_ROOT" in content
    assert "AUTORESEARCH_JOB" in content
    assert "AUTORESEARCH_DISCORD_WEBHOOK_EMA" not in content
    assert "TRACELOOP_API_KEY" not in content
    assert "AUTORESEARCH_VPS_KEY" not in content
    assert "AUTORESEARCH_GIT_REPO" not in content


def test_materialize_remote_runtime_env_uploads_autogenerated_env_file(monkeypatch) -> None:
    class FakeSFTP:
        def __init__(self) -> None:
            self.put_calls: list[tuple[str, str]] = []
            self.put_contents: list[str] = []
            self.mkdirs: list[str] = []
            self.chmod_calls: list[tuple[str, int]] = []

        def stat(self, remote_path: str) -> None:
            if remote_path not in {"/", "/srv", "/srv/autoresearch"} | set(self.mkdirs):
                raise OSError("missing")

        def mkdir(self, remote_path: str) -> None:
            self.mkdirs.append(remote_path)

        def put(self, local_path: str, remote_path: str) -> None:
            self.put_calls.append((local_path, remote_path))
            self.put_contents.append(Path(local_path).read_text(encoding="utf-8"))

        def chmod(self, remote_path: str, mode: int) -> None:
            self.chmod_calls.append((remote_path, mode))

        def close(self) -> None:
            pass

    class FakeClient:
        def __init__(self) -> None:
            self.sftp = FakeSFTP()

        def open_sftp(self):
            return self.sftp

    monkeypatch.setenv("AUTORESEARCH_DISCORD_WEBHOOK_EMA", "https://example.com/webhook")
    monkeypatch.setenv("AUTORESEARCH_DATA_ROOT", "/data/autoresearch")
    monkeypatch.setenv("AUTORESEARCH_JOB", "9")

    config = VPSConfig(
        host="203.0.113.10",
        user="researcher",
        key="/tmp/key",
        remote_dir="/srv/autoresearch",
        git_repo="https://github.com/example/repo.git",
        git_ref="feature/ema",
    )

    client = FakeClient()
    remote_path = materialize_remote_runtime_env(client, config)

    assert remote_path == "/srv/autoresearch/.env.autoresearch"
    assert client.sftp.put_calls
    assert client.sftp.put_calls[0][1] == remote_path
    assert client.sftp.chmod_calls == [(remote_path, 0o600)]
    content = client.sftp.put_contents[0]
    assert "AUTORESEARCH_DISCORD_WEBHOOK_EMA" not in content
    assert "AUTORESEARCH_DATA_ROOT" in content
    assert "AUTORESEARCH_JOB" in content


def test_materialize_remote_codex_auth_uploads_local_auth_bundle(monkeypatch, tmp_path) -> None:
    local_codex = tmp_path / ".codex"
    local_codex.mkdir()
    (local_codex / "auth.json").write_text(
        json.dumps(
            {
                "OPENAI_API_KEY": None,
                "tokens": {
                    "access_token": "redacted",
                    "refresh_token": "redacted",
                    "id_token": "redacted",
                    "account_id": "redacted",
                },
            }
        )
    )

    class FakeSFTP:
        def __init__(self) -> None:
            self.put_calls: list[tuple[str, str]] = []
            self.mkdirs: list[str] = []
            self.chmod_calls: list[tuple[str, int]] = []

        def stat(self, remote_path: str) -> None:
            if remote_path not in {
                "/",
                "/home",
                "/home/researcher",
                "/home/researcher/.codex",
            } | set(self.mkdirs):
                raise OSError("missing")

        def mkdir(self, remote_path: str) -> None:
            self.mkdirs.append(remote_path)

        def put(self, local_path: str, remote_path: str) -> None:
            self.put_calls.append((local_path, remote_path))

        def chmod(self, remote_path: str, mode: int) -> None:
            self.chmod_calls.append((remote_path, mode))

        def close(self) -> None:
            pass

    class FakeClient:
        def __init__(self) -> None:
            self.sftp = FakeSFTP()

        def open_sftp(self):
            return self.sftp

    config = VPSConfig(
        host="203.0.113.10",
        user="researcher",
        key="/tmp/key",
        remote_dir="/srv/autoresearch",
        git_repo="https://github.com/example/repo.git",
        git_ref="feature/ema",
    )

    monkeypatch.setattr("vps_runner.Path.home", lambda: tmp_path)

    client = FakeClient()
    remote_path = materialize_remote_codex_auth(client, config)

    assert remote_path == "/home/researcher/.codex/auth.json"
    assert client.sftp.put_calls == [
        (str(local_codex / "auth.json"), "/home/researcher/.codex/auth.json")
    ]
    assert client.sftp.chmod_calls == [
        ("/home/researcher/.codex", 0o700),
        ("/home/researcher/.codex/auth.json", 0o600),
    ]


def test_git_prepare_command_clones_fetches_and_preserves_runtime_artifacts() -> None:
    config = VPSConfig(
        host="203.0.113.10",
        user="researcher",
        key="/tmp/key",
        remote_dir="/srv/autoresearch code",
        git_repo="https://github.com/example/repo.git",
        git_ref="feature/ema",
    )

    command = build_git_prepare_command(config)

    assert "git clone --no-checkout" in command
    assert f"git fetch --prune origin {shlex.quote(config.git_ref)}" in command
    assert "resolved=$(git rev-parse --verify FETCH_HEAD^{commit})" in command
    assert 'git checkout --detach "$resolved"' in command
    assert "git clean -ffdx" in command
    assert "-e '*_autoresearch-runs'" in command
    assert "-e '.venv'" in command
    assert "-e '.venv/**'" in command
    assert "-e 'data'" in command
    assert "-e 'experiments'" in command
    assert "-e 'proposals'" in command
    assert "-e '*-proposals'" in command
    assert "-e 'run-queue'" in command
    assert "-e '*-run-queue'" in command
    assert "-e '*-contracts'" in command
    assert "-e '*-builder-requests'" in command
    assert "-e '*_experiments.db'" in command
    assert "AUTORESEARCH_RESOLVED_SHA %s" in command
    assert "scp" not in command.lower()


def test_build_remote_command_exports_codex_home_for_remote_user() -> None:
    config = VPSConfig(
        host="203.0.113.10",
        user="researcher",
        key="/tmp/key",
        remote_dir="/srv/autoresearch",
        git_repo="https://github.com/example/repo.git",
        git_ref="feature/ema",
    )

    family = load_family("ema")
    command = build_remote_command(config, family, "0123456789abcdef0123456789abcdef01234567")

    assert "export CODEX_HOME=/home/researcher/.codex" in command
    assert "export AUTORESEARCH_RESOLVED_SHA=0123456789abcdef0123456789abcdef01234567" in command


def test_git_prepare_command_uses_commit_ref_resolution_for_sha() -> None:
    sha = "0123456789abcdef0123456789abcdef01234567"
    config = VPSConfig(
        host="203.0.113.10",
        user="researcher",
        key="/tmp/key",
        remote_dir="/srv/autoresearch code",
        git_repo="https://github.com/example/repo.git",
        git_ref=sha,
    )

    command = build_git_prepare_command(config)

    # Full SHA deploys fetch all refs (SHAs cannot be used as refspecs) then resolve locally.
    assert "git fetch --prune origin &&" in command
    assert f"resolved=$(git rev-parse --verify {shlex.quote(sha)}^{{commit}})" in command


def test_git_prepare_command_treats_abbreviated_hex_ref_as_sha() -> None:
    short_sha = "284e8c9"
    config = VPSConfig(
        host="203.0.113.10",
        user="researcher",
        key="/tmp/key",
        remote_dir="/srv/autoresearch code",
        git_repo="https://github.com/example/repo.git",
        git_ref=short_sha,
    )

    command = build_git_prepare_command(config)

    assert "git fetch --prune origin &&" in command
    assert f"resolved=$(git rev-parse --verify {shlex.quote(short_sha)}^{{commit}})" in command
    assert f"git fetch --prune origin {shlex.quote(short_sha)}" not in command


def test_git_prepare_command_uses_posix_remote_parent_on_windows(monkeypatch) -> None:
    config = VPSConfig(
        host="203.0.113.10",
        user="researcher",
        key="/tmp/key",
        remote_dir="/srv/autoresearch",
        git_repo="https://github.com/example/repo.git",
        git_ref="feature/ema",
    )
    monkeypatch.setattr("vps_runner.Path", PureWindowsPath)

    command = build_git_prepare_command(config)

    assert "mkdir -p /srv" in command
    assert "\\srv" not in command


def test_parse_resolved_sha_requires_exact_marker() -> None:
    sha = "0123456789abcdef0123456789abcdef01234567"

    assert parse_resolved_sha(f"noise\nAUTORESEARCH_RESOLVED_SHA {sha}\n") == sha

    with pytest.raises(RuntimeError, match="resolved commit SHA"):
        parse_resolved_sha("AUTORESEARCH_RESOLVED_SHA not-a-sha\n")


def test_redact_git_repo_url_hides_https_credentials() -> None:
    assert (
        redact_git_repo_url("https://token123@github.com/example/repo.git")
        == "https://***@github.com/example/repo.git"
    )
    assert (
        redact_git_repo_url("https://user:token123@github.com:443/example/repo.git")
        == "https://***@github.com:443/example/repo.git"
    )
    assert (
        redact_git_repo_url("git@github.com:example/repo.git") == "git@github.com:example/repo.git"
    )


def test_redact_secrets_hides_git_credentials_from_output() -> None:
    config = VPSConfig(
        host="203.0.113.10",
        user="researcher",
        key="/tmp/key",
        remote_dir="/srv/autoresearch",
        git_repo="https://token123@github.com/example/repo.git",
        git_ref="feature/ema",
    )
    output = (
        "fatal: unable to access 'https://token123@github.com/example/repo.git/'\n"
        "remote https://other-token@github.com/other/repo.git failed\n"
    )

    redacted = redact_secrets(output, config)

    assert "token123" not in redacted
    assert "other-token" not in redacted
    assert "https://***@github.com/example/repo.git" in redacted
    assert "https://***@github.com/other/repo.git" in redacted


def test_ssh_client_requires_pretrusted_host_keys(monkeypatch, tmp_path) -> None:
    calls: list[str] = []
    known_hosts = tmp_path / "known_hosts"
    known_hosts.write_text("example.invalid ssh-ed25519 AAAATEST\n")

    class FakeSSHClient:
        def load_system_host_keys(self) -> None:
            calls.append("load_system_host_keys")

        def load_host_keys(self, filename: str) -> None:
            calls.append(f"load_host_keys:{filename}")

        def set_missing_host_key_policy(self, policy) -> None:
            calls.append(f"policy:{type(policy).__name__}")

    monkeypatch.setattr("vps_runner.paramiko.SSHClient", FakeSSHClient)
    monkeypatch.setattr("vps_runner.paramiko.RejectPolicy", lambda: "reject-policy")
    monkeypatch.setenv("AUTORESEARCH_KNOWN_HOSTS", str(known_hosts))

    client = create_verified_ssh_client()

    assert isinstance(client, FakeSSHClient)
    assert calls == [
        "load_system_host_keys",
        f"load_host_keys:{known_hosts}",
        "policy:str",
    ]


def test_ssh_client_rejects_missing_known_hosts_file(monkeypatch, tmp_path) -> None:
    missing = tmp_path / "missing_known_hosts"
    monkeypatch.setenv("AUTORESEARCH_KNOWN_HOSTS", str(missing))

    with pytest.raises(FileNotFoundError, match="AUTORESEARCH_KNOWN_HOSTS"):
        create_verified_ssh_client()


def test_sftp_mkdir_p_uses_posix_remote_parts_on_windows(monkeypatch) -> None:
    created_dirs: list[str] = []

    class FakeSFTP:
        def stat(self, remote_path: str) -> None:
            if remote_path not in {"/", "/srv", "/srv/autoresearch"} | set(created_dirs):
                raise OSError("missing")

        def mkdir(self, remote_path: str) -> None:
            created_dirs.append(remote_path)

    monkeypatch.setattr("vps_runner.Path", PureWindowsPath)

    _sftp_mkdir_p(FakeSFTP(), "/srv/autoresearch/experiments/ema5")

    assert created_dirs == [
        "/srv/autoresearch/experiments",
        "/srv/autoresearch/experiments/ema5",
    ]


def test_materialize_remote_config_skips_tracked_configs(monkeypatch, tmp_path) -> None:
    config_file = tmp_path / "configs" / "ema_base.yaml"
    config_file.parent.mkdir()
    config_file.write_text("strategy: ema\n")

    class FakeClient:
        def open_sftp(self):  # pragma: no cover - should not be called
            raise AssertionError("tracked configs must come from git, not SFTP")

    config = VPSConfig(
        host="203.0.113.10",
        user="researcher",
        key="/tmp/key",
        remote_dir="/srv/autoresearch",
        git_repo="https://github.com/example/repo.git",
        git_ref="feature/ema",
    )

    monkeypatch.setattr("vps_runner._repo_root", lambda: tmp_path)
    monkeypatch.setattr("vps_runner._is_git_tracked", lambda rel_path: True)

    assert (
        materialize_remote_config_if_needed(FakeClient(), config, "configs/ema_base.yaml")
        == "configs/ema_base.yaml"
    )


def test_materialize_remote_config_uploads_generated_experiment_input(
    monkeypatch, tmp_path
) -> None:
    config_file = tmp_path / "experiments" / "ema5" / "runtime_config.json"
    config_file.parent.mkdir(parents=True)
    config_file.write_text('{"strategy": "ema"}\n')
    uploaded: list[tuple[str, str]] = []
    created_dirs: list[str] = []

    class FakeSFTP:
        def stat(self, remote_path: str) -> None:
            if remote_path not in {"/", "/srv", "/srv/autoresearch"} | set(created_dirs):
                raise OSError("missing")

        def mkdir(self, remote_path: str) -> None:
            created_dirs.append(remote_path)

        def put(self, local_path: str, remote_path: str) -> None:
            uploaded.append((local_path, remote_path))

        def close(self) -> None:
            pass

    class FakeClient:
        def open_sftp(self):
            return FakeSFTP()

    config = VPSConfig(
        host="203.0.113.10",
        user="researcher",
        key="/tmp/key",
        remote_dir="/srv/autoresearch",
        git_repo="https://github.com/example/repo.git",
        git_ref="feature/ema",
    )

    monkeypatch.setattr("vps_runner._repo_root", lambda: tmp_path)
    monkeypatch.setattr("vps_runner._is_git_tracked", lambda rel_path: False)

    remote_config = materialize_remote_config_if_needed(
        FakeClient(),
        config,
        "experiments/ema5/runtime_config.json",
    )

    assert remote_config == "experiments/ema5/runtime_config.json"
    assert uploaded == [
        (
            str(config_file),
            "/srv/autoresearch/experiments/ema5/runtime_config.json",
        )
    ]
    assert "/srv/autoresearch/experiments" in created_dirs
    assert "/srv/autoresearch/experiments/ema5" in created_dirs


def test_materialize_remote_config_uses_posix_remote_parent_on_windows(
    monkeypatch, tmp_path
) -> None:
    config_file = tmp_path / "experiments" / "ema5" / "runtime_config.json"
    config_file.parent.mkdir(parents=True)
    config_file.write_text('{"strategy": "ema"}\n')
    created_dirs: list[str] = []

    class FakeSFTP:
        def stat(self, remote_path: str) -> None:
            if remote_path not in {"/", "/srv", "/srv/autoresearch"} | set(created_dirs):
                raise OSError("missing")

        def mkdir(self, remote_path: str) -> None:
            created_dirs.append(remote_path)

        def put(self, local_path: str, remote_path: str) -> None:
            assert local_path == str(config_file)
            assert remote_path == "/srv/autoresearch/experiments/ema5/runtime_config.json"

        def close(self) -> None:
            pass

    class FakeClient:
        def open_sftp(self):
            return FakeSFTP()

    config = VPSConfig(
        host="203.0.113.10",
        user="researcher",
        key="/tmp/key",
        remote_dir="/srv/autoresearch",
        git_repo="https://github.com/example/repo.git",
        git_ref="feature/ema",
    )

    monkeypatch.setattr("vps_runner._repo_root", lambda: tmp_path)
    monkeypatch.setattr("vps_runner._relative_repo_path", lambda path: Path(path))
    monkeypatch.setattr("vps_runner._is_git_tracked", lambda rel_path: False)
    monkeypatch.setattr("vps_runner.Path", PureWindowsPath)

    materialize_remote_config_if_needed(
        FakeClient(),
        config,
        "experiments/ema5/runtime_config.json",
    )

    assert created_dirs == [
        "/srv/autoresearch/experiments",
        "/srv/autoresearch/experiments/ema5",
    ]


def test_materialize_remote_config_rejects_untracked_non_experiment_configs(
    monkeypatch, tmp_path
) -> None:
    config_file = tmp_path / "tmp" / "runtime_config.json"
    config_file.parent.mkdir()
    config_file.write_text("{}\n")

    class FakeClient:
        def open_sftp(self):  # pragma: no cover - should not be called
            raise AssertionError("unexpected upload")

    config = VPSConfig(
        host="203.0.113.10",
        user="researcher",
        key="/tmp/key",
        remote_dir="/srv/autoresearch",
        git_repo="https://github.com/example/repo.git",
        git_ref="feature/ema",
    )

    monkeypatch.setattr("vps_runner._repo_root", lambda: tmp_path)
    monkeypatch.setattr("vps_runner._is_git_tracked", lambda rel_path: False)

    with pytest.raises(
        RuntimeError, match="tracked config files or generated experiments/ configs"
    ):
        materialize_remote_config_if_needed(FakeClient(), config, "tmp/runtime_config.json")


def test_localize_remote_result_output_fetches_remote_artifacts(monkeypatch, tmp_path) -> None:
    remote_dir = tmp_path / "remote"
    remote_dir.mkdir()
    local_dir = tmp_path / "local"
    local_dir.mkdir()

    remote_result = remote_dir / "result.json"
    remote_trades = remote_dir / "trades.csv"
    remote_events = remote_dir / "strategy_events.parquet"
    remote_diag = remote_dir / "diagnostics.json"
    remote_trades.write_text("t")
    remote_events.write_text("e")
    remote_diag.write_text("d")
    remote_result.write_text(
        json.dumps(
            {
                "trades_file": str(remote_trades),
                "strategy_events_file": str(remote_events),
                "diagnostics_file": str(remote_diag),
            }
        )
    )

    copied: list[tuple[str, str]] = []

    class FakeSFTP:
        def get(self, remote_path: str, local_path: str) -> None:
            copied.append((remote_path, local_path))
            Path(local_path).write_text(Path(remote_path).read_text())

    monkeypatch.setattr("vps_runner.tempfile.mkdtemp", lambda prefix: str(local_dir))

    out = _localize_remote_result_output(f"RESULT_JSON {remote_result}\n", FakeSFTP())

    assert f"RESULT_JSON {local_dir / 'result.json'}" in out
    copied_remote_paths = {remote for remote, _ in copied}
    assert str(remote_result) in copied_remote_paths
    assert str(remote_trades) in copied_remote_paths
    assert str(remote_events) in copied_remote_paths
    assert str(remote_diag) in copied_remote_paths
    localized = json.loads((local_dir / "result.json").read_text())
    assert localized["trades_file"] == str(local_dir / "trades.csv")
    assert localized["strategy_events_file"] == str(local_dir / "strategy_events.parquet")
    assert localized["diagnostics_file"] == str(local_dir / "diagnostics.json")


def test_localize_remote_result_output_raises_when_result_json_missing(
    monkeypatch, tmp_path
) -> None:
    remote_dir = tmp_path / "remote"
    remote_dir.mkdir()
    local_dir = tmp_path / "local"
    local_dir.mkdir()

    remote_result = remote_dir / "result.json"

    class FakeSFTP:
        def get(self, remote_path: str, local_path: str) -> None:
            raise OSError("missing remote artifact")

    monkeypatch.setattr("vps_runner.tempfile.mkdtemp", lambda prefix: str(local_dir))

    with pytest.raises(RuntimeError, match="Failed to fetch remote RESULT_JSON artifact"):
        _localize_remote_result_output(f"RESULT_JSON {remote_result}\n", FakeSFTP())

    assert not (local_dir / "result.json").exists()


def test_localize_remote_result_output_fails_closed_on_partial_artifact_fetch(
    monkeypatch, tmp_path
) -> None:
    remote_dir = tmp_path / "remote"
    remote_dir.mkdir()
    local_dir = tmp_path / "local"
    local_dir.mkdir()

    remote_result = remote_dir / "result.json"
    remote_trades = remote_dir / "trades.csv"
    remote_events = remote_dir / "strategy_events.parquet"
    remote_diag = remote_dir / "diagnostics.json"
    remote_trades.write_text("t")
    remote_events.write_text("e")
    remote_diag.write_text("d")
    remote_result.write_text(
        json.dumps(
            {
                "trades_file": str(remote_trades),
                "strategy_events_file": str(remote_events),
                "diagnostics_file": str(remote_diag),
            }
        )
    )

    class FakeSFTP:
        def get(self, remote_path: str, local_path: str) -> None:
            if remote_path == str(remote_diag):
                raise OSError("diagnostics missing")
            Path(local_path).write_text(Path(remote_path).read_text())

    monkeypatch.setattr("vps_runner.tempfile.mkdtemp", lambda prefix: str(local_dir))

    with pytest.raises(RuntimeError, match="Failed to fetch remote result artifact referenced by"):
        _localize_remote_result_output(f"RESULT_JSON {remote_result}\n", FakeSFTP())

    assert not (local_dir / "result.json").exists()
    assert not (local_dir / "trades.csv").exists()
    assert not (local_dir / "strategy_events.parquet").exists()
