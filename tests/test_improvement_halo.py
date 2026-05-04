"""Unit tests for improvement_halo.

Covers the flag matrix and external-tool degradation paths. The HALO
CLI is mocked end-to-end so tests don't need the binary on PATH.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

import improvement_halo
from autoresearch_constants import ENV_IMPROVEMENT_HALO


@pytest.fixture(autouse=True)
def _reset_env(monkeypatch):
    monkeypatch.delenv(ENV_IMPROVEMENT_HALO, raising=False)
    yield


def _make_jsonl(tmp_path: Path) -> Path:
    p = tmp_path / "trace-events.jsonl"
    p.write_text('{"event": 1}\n{"event": 2}\n', encoding="utf-8")
    return p


def test_flag_off_returns_none(tmp_path):
    jsonl = _make_jsonl(tmp_path)
    out = tmp_path / "reports"
    assert improvement_halo.run_halo_after_round(1, jsonl, out) is None


def test_flag_on_missing_binary_returns_none(tmp_path, monkeypatch):
    monkeypatch.setenv(ENV_IMPROVEMENT_HALO, "1")
    monkeypatch.setattr(improvement_halo.shutil, "which", lambda _b: None)
    jsonl = _make_jsonl(tmp_path)
    out = tmp_path / "reports"
    assert improvement_halo.run_halo_after_round(1, jsonl, out) is None
    # No report file written.
    assert not out.exists() or list(out.iterdir()) == []


def test_flag_on_missing_jsonl_returns_none(tmp_path, monkeypatch):
    monkeypatch.setenv(ENV_IMPROVEMENT_HALO, "1")
    monkeypatch.setattr(improvement_halo.shutil, "which", lambda _b: "/usr/bin/halo")
    out = tmp_path / "reports"
    result = improvement_halo.run_halo_after_round(1, tmp_path / "nope.jsonl", out)
    assert result is None


def test_flag_on_success_writes_report(tmp_path, monkeypatch):
    monkeypatch.setenv(ENV_IMPROVEMENT_HALO, "1")
    monkeypatch.setattr(improvement_halo.shutil, "which", lambda _b: "/usr/bin/halo")

    captured: dict[str, list] = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        captured["kwargs"] = kwargs
        return SimpleNamespace(returncode=0, stdout="# HALO report\n- finding 1\n", stderr="")

    monkeypatch.setattr(improvement_halo.subprocess, "run", fake_run)

    jsonl = _make_jsonl(tmp_path)
    out = tmp_path / "reports"
    report = improvement_halo.run_halo_after_round(7, jsonl, out)

    assert report is not None
    assert report.name == "round-007.md"
    assert report.read_text(encoding="utf-8") == "# HALO report\n- finding 1\n"
    # Command shape: halo <jsonl> -p <prompt>
    assert captured["cmd"][0] == "halo"
    assert captured["cmd"][1] == str(jsonl)
    assert captured["cmd"][2] == "-p"
    assert "Markdown" in captured["cmd"][3]
    assert captured["kwargs"]["timeout"] == improvement_halo.HALO_TIMEOUT_SECONDS
    assert captured["kwargs"]["check"] is False


def test_flag_on_nonzero_exit_returns_none(tmp_path, monkeypatch):
    monkeypatch.setenv(ENV_IMPROVEMENT_HALO, "1")
    monkeypatch.setattr(improvement_halo.shutil, "which", lambda _b: "/usr/bin/halo")
    monkeypatch.setattr(
        improvement_halo.subprocess,
        "run",
        lambda *a, **k: SimpleNamespace(returncode=2, stdout="", stderr="oops"),
    )
    jsonl = _make_jsonl(tmp_path)
    out = tmp_path / "reports"
    assert improvement_halo.run_halo_after_round(1, jsonl, out) is None
    # No report file.
    assert not (out / "round-001.md").exists()


def test_flag_on_timeout_returns_none(tmp_path, monkeypatch):
    monkeypatch.setenv(ENV_IMPROVEMENT_HALO, "1")
    monkeypatch.setattr(improvement_halo.shutil, "which", lambda _b: "/usr/bin/halo")

    def raise_timeout(*a, **k):
        raise subprocess.TimeoutExpired(cmd="halo", timeout=600)

    monkeypatch.setattr(improvement_halo.subprocess, "run", raise_timeout)
    jsonl = _make_jsonl(tmp_path)
    out = tmp_path / "reports"
    assert improvement_halo.run_halo_after_round(1, jsonl, out) is None


def test_flag_on_os_error_returns_none(tmp_path, monkeypatch):
    monkeypatch.setenv(ENV_IMPROVEMENT_HALO, "1")
    monkeypatch.setattr(improvement_halo.shutil, "which", lambda _b: "/usr/bin/halo")

    def raise_os(*a, **k):
        raise OSError("permission denied")

    monkeypatch.setattr(improvement_halo.subprocess, "run", raise_os)
    jsonl = _make_jsonl(tmp_path)
    out = tmp_path / "reports"
    assert improvement_halo.run_halo_after_round(1, jsonl, out) is None


def test_halo_timeout_overridable_via_env(monkeypatch):
    from autoresearch_constants import ENV_HALO_TIMEOUT_SECONDS

    monkeypatch.setenv(ENV_HALO_TIMEOUT_SECONDS, "42")
    import importlib

    import improvement_halo as m

    importlib.reload(m)
    assert m.HALO_TIMEOUT_SECONDS == 42
