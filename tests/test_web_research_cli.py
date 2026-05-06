from __future__ import annotations

import subprocess

import pytest

import web_research_cli


def test_run_codex_web_research_uses_live_web_search_and_output_file(monkeypatch, tmp_path):
    captured: dict[str, object] = {}

    monkeypatch.setattr(web_research_cli, "_find_codex_cli", lambda: "codex")

    class FakeProcess:
        returncode = 0
        pid = 12345

        def __init__(self, cmd, *, stdin, stdout, stderr, text, cwd, start_new_session):
            captured["cmd"] = cmd
            captured["stdin"] = stdin
            captured["stdout"] = stdout
            captured["stderr"] = stderr
            captured["text"] = text
            captured["cwd"] = cwd
            captured["start_new_session"] = start_new_session

        def communicate(self, *, input, timeout):
            captured["input"] = input
            captured["timeout"] = timeout
            output_path = captured["cmd"][captured["cmd"].index("--output-last-message") + 1]
            with open(output_path, "w") as f:
                f.write('{"findings":[{"topic":"x","finding":"y"}],"summary":"ok"}')
            return "ignored stdout", ""

    def fake_popen(cmd, *, stdin, stdout, stderr, text, cwd, start_new_session):
        return FakeProcess(
            cmd,
            stdin=stdin,
            stdout=stdout,
            stderr=stderr,
            text=text,
            cwd=cwd,
            start_new_session=start_new_session,
        )

    monkeypatch.setattr(web_research_cli.subprocess, "Popen", fake_popen)

    output, metadata = web_research_cli.run_codex_web_research(
        "question",
        instructions="system instructions",
        model="gpt-5.2",
        cwd=tmp_path,
        timeout_seconds=12,
    )

    assert captured["cmd"][:5] == [
        "codex",
        "exec",
        "--ephemeral",
        "--sandbox",
        "workspace-write",
    ]
    assert captured["cmd"][-4:] == [
        "--config",
        'web_search="live"',
        "--config",
        'model_reasoning_effort="low"',
    ]
    assert "--json" in captured["cmd"]
    assert "--model" in captured["cmd"]
    assert captured["cmd"][captured["cmd"].index("--model") + 1] == "gpt-5.2"
    assert "--output-last-message" in captured["cmd"]
    assert "system instructions" in captured["input"]
    assert "USER REQUEST:\nquestion" in captured["input"]
    assert captured["cwd"] == tmp_path
    assert captured["timeout"] == 12
    assert captured["stdin"] == subprocess.PIPE
    assert captured["stdout"] == subprocess.PIPE
    assert captured["stderr"] == subprocess.PIPE
    assert captured["start_new_session"] is True
    assert output == '{"findings":[{"topic":"x","finding":"y"}],"summary":"ok"}'
    assert metadata["exit_code"] == 0
    assert metadata["output_path_used"] is True


def test_run_codex_web_research_extracts_usage_from_json_stdout(monkeypatch, tmp_path):
    monkeypatch.setattr(web_research_cli, "_find_codex_cli", lambda: "codex")

    class FakeProcess:
        returncode = 0
        pid = 12345

        def __init__(self, cmd, **kwargs):
            self.cmd = cmd

        def communicate(self, *, input, timeout):
            output_path = self.cmd[self.cmd.index("--output-last-message") + 1]
            with open(output_path, "w") as f:
                f.write('{"findings":[],"summary":"ok"}')
            return (
                '{"type":"event_msg","payload":{"type":"token_count","info":null}}\n'
                '{"type":"event_msg","payload":{"type":"token_count","info":{'
                '"last_token_usage":{"input_tokens":101,"cached_input_tokens":20,'
                '"output_tokens":11,"reasoning_output_tokens":3,"total_tokens":112},'
                '"total_token_usage":{"input_tokens":303,"output_tokens":33,"total_tokens":336}'
                "}}}\n",
                "",
            )

    monkeypatch.setattr(web_research_cli.subprocess, "Popen", lambda *a, **k: FakeProcess(*a, **k))

    _output, metadata = web_research_cli.run_codex_web_research(
        "question",
        instructions="system instructions",
        model="gpt-5.2",
        cwd=tmp_path,
        timeout_seconds=12,
    )

    assert metadata["usage"] == {
        "input_tokens": 101,
        "cached_input_tokens": 20,
        "output_tokens": 11,
        "reasoning_output_tokens": 3,
        "total_tokens": 112,
    }
    assert metadata["usage_source"] == "codex_json_last_token_usage"


def test_run_codex_web_research_terminates_process_group_on_timeout(monkeypatch, tmp_path):
    captured: dict[str, object] = {}

    monkeypatch.setattr(web_research_cli, "_find_codex_cli", lambda: "codex")

    class FakeProcess:
        returncode = None
        pid = 12345

        def __init__(self, *args, **kwargs):
            self.calls = 0

        def communicate(self, *, input=None, timeout=None):
            self.calls += 1
            if self.calls == 1:
                raise subprocess.TimeoutExpired(cmd="codex", timeout=timeout)
            return "", "stderr"

        def kill(self):
            captured["killed"] = True

        def terminate(self):
            captured["terminated"] = True

    monkeypatch.setattr(web_research_cli.subprocess, "Popen", lambda *a, **k: FakeProcess())
    monkeypatch.setattr(
        web_research_cli.os,
        "killpg",
        lambda pid, sig: captured.update({"pid": pid, "sig": sig}),
    )

    with pytest.raises(web_research_cli.WebResearchCliError, match="timed out after 1s"):
        web_research_cli.run_codex_web_research(
            "question",
            instructions="system instructions",
            cwd=tmp_path,
            timeout_seconds=1,
        )

    assert captured["pid"] == 12345
    assert captured["sig"] == web_research_cli.signal.SIGTERM


def test_resolve_timeout_seconds_handles_invalid_env(monkeypatch):
    monkeypatch.setenv(web_research_cli.WEB_RESEARCH_CLI_TIMEOUT_ENV, "not-an-int")

    assert (
        web_research_cli._resolve_timeout_seconds(None)
        == web_research_cli.DEFAULT_WEB_RESEARCH_CLI_TIMEOUT_SECONDS
    )
