"""openai-oauth proxy auth-probe: fail fast on an expired session instead of an
opaque RemoteProtocolError deep in the conductor."""

from __future__ import annotations

import urllib.error

import pytest

import agent_infra


class _FakeResp:
    def __init__(self, status: int) -> None:
        self.status = status

    def __enter__(self) -> "_FakeResp":
        return self

    def __exit__(self, *exc: object) -> bool:
        return False

    def getcode(self) -> int:
        return self.status


def test_probe_raises_with_remediation_on_non_200(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: _FakeResp(502))
    with pytest.raises(RuntimeError, match="not authenticated"):
        agent_infra._probe_oauth_proxy_auth(timeout_seconds=1)


def test_probe_raises_on_httperror(monkeypatch: pytest.MonkeyPatch) -> None:
    def raise_http(*a: object, **k: object) -> None:
        raise urllib.error.HTTPError(url="x", code=403, msg="forbidden", hdrs=None, fp=None)

    monkeypatch.setattr(urllib.request, "urlopen", raise_http)
    with pytest.raises(RuntimeError, match="not authenticated"):
        agent_infra._probe_oauth_proxy_auth(timeout_seconds=1)


def test_probe_passes_on_200(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: _FakeResp(200))
    agent_infra._probe_oauth_proxy_auth(timeout_seconds=1)  # no raise


def test_probe_is_lenient_on_connection_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*a: object, **k: object) -> None:
        raise OSError("connection refused")

    monkeypatch.setattr(urllib.request, "urlopen", boom)
    agent_infra._probe_oauth_proxy_auth(timeout_seconds=1)  # lenient: no raise
