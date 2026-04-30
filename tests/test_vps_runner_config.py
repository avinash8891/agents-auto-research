from __future__ import annotations

import ast
import os
from pathlib import Path

import pytest

from strategy_family import load_family
from vps_runner import (
    VPSConfig,
    build_remote_command,
    config_from_env,
    create_verified_ssh_client,
    sync_relative_paths,
)

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_vps_config_reads_remote_details_from_environment(monkeypatch) -> None:
    monkeypatch.setenv("AUTORESEARCH_VPS_HOST", "203.0.113.10")
    monkeypatch.setenv("AUTORESEARCH_VPS_USER", "researcher")
    monkeypatch.setenv("AUTORESEARCH_VPS_KEY", "~/.ssh/research_key")
    monkeypatch.setenv("AUTORESEARCH_VPS_DIR", "/srv/autoresearch")

    config = config_from_env()

    assert config.host == "203.0.113.10"
    assert config.user == "researcher"
    assert config.key == os.path.expanduser("~/.ssh/research_key")
    assert config.remote_dir == "/srv/autoresearch"


def test_vps_config_requires_explicit_environment(monkeypatch) -> None:
    for name in (
        "AUTORESEARCH_VPS_HOST",
        "AUTORESEARCH_VPS_USER",
        "AUTORESEARCH_VPS_KEY",
        "AUTORESEARCH_VPS_DIR",
    ):
        monkeypatch.delenv(name, raising=False)

    with pytest.raises(ValueError, match="AUTORESEARCH_VPS_HOST"):
        config_from_env()


def test_remote_command_uses_generic_runner_and_family_metadata() -> None:
    family = load_family("ema")
    config = VPSConfig(
        host="203.0.113.10",
        user="researcher",
        key="/tmp/key",
        remote_dir="/srv/autoresearch",
    )

    command = build_remote_command(config, family, "configs/ema_base.yaml")

    assert 'python3 backtest/runner.py --strategy "ema" --config "configs/ema_base.yaml"' in command
    assert "/root/orb-research" not in command
    assert "backtest_5ema.py" not in command


def test_sync_manifest_includes_autoresearch_loop_imported_modules() -> None:
    tree = ast.parse((REPO_ROOT / "autoresearch_controller.py").read_text())
    imported_local_modules = {
        node.module
        for node in tree.body
        if isinstance(node, ast.ImportFrom)
        and node.module is not None
        and node.module.startswith("autoresearch_")
    }

    synced = sync_relative_paths(REPO_ROOT)

    assert imported_local_modules
    for module_name in imported_local_modules:
        assert f"{module_name}.py" in synced


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
