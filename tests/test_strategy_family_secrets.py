"""Tests for the env-var-driven Discord webhook contract.

Project rule 2: no secrets in source. The webhook URL must come from an
environment variable; an unset variable produces an empty string, which
makes notify_discord a no-op.
"""

from __future__ import annotations

import importlib

import pytest


@pytest.fixture
def reload_family(monkeypatch):
    """Reload strategy_family in a clean env so the FAMILIES dict picks up
    whatever AUTORESEARCH_DISCORD_WEBHOOK_* the test sets."""

    def _reload():
        import strategy_family

        return importlib.reload(strategy_family)

    return _reload


def test_orb_webhook_empty_when_env_unset(reload_family, monkeypatch):
    monkeypatch.delenv("AUTORESEARCH_DISCORD_WEBHOOK_ORB", raising=False)
    sf = reload_family()
    assert sf.load_family("orb").discord_webhook == ""


def test_ema_webhook_empty_when_env_unset(reload_family, monkeypatch):
    monkeypatch.delenv("AUTORESEARCH_DISCORD_WEBHOOK_EMA", raising=False)
    sf = reload_family()
    assert sf.load_family("ema").discord_webhook == ""


def test_webhook_picked_up_from_env_var(reload_family, monkeypatch):
    monkeypatch.setenv(
        "AUTORESEARCH_DISCORD_WEBHOOK_ORB",
        "https://discord.example.invalid/hook/test-orb-token",
    )
    sf = reload_family()
    assert (
        sf.load_family("orb").discord_webhook
        == "https://discord.example.invalid/hook/test-orb-token"
    )


def test_no_real_secrets_in_strategy_family_source(repo_root):
    """Snapshot test: the production webhook tokens that were in git
    history pre-PR-3 must not reappear in source."""
    src = (repo_root / "strategy_family.py").read_text()
    # Token characters from the leaked URLs (deliberately partial fragments
    # so this file itself cannot be used as a credential dump).
    leaked_orb_fragment = "8te44ljBeskS2jdlEEEAy5mvORW5IaiOhsH6"
    leaked_ema_fragment = "FkBLmVdPQtBuXXJE63xuZqJ4JemtWflgWE3ioGT"
    assert leaked_orb_fragment not in src
    assert leaked_ema_fragment not in src


def test_notify_discord_is_no_op_when_webhook_empty(reload_family, monkeypatch):
    """End-to-end: with no env var set, notify_discord performs no HTTP."""
    import urllib.request

    monkeypatch.delenv("AUTORESEARCH_DISCORD_WEBHOOK_ORB", raising=False)
    sf = reload_family()
    family = sf.load_family("orb")
    assert family.discord_webhook == ""

    called = {"count": 0}

    def boom(*args, **kwargs):
        called["count"] += 1
        raise AssertionError("urlopen should not be called when webhook is empty")

    monkeypatch.setattr(urllib.request, "urlopen", boom)
    from autoresearch_research import notify_discord

    notify_discord("title", "body", webhook=family.discord_webhook)
    assert called["count"] == 0
