from __future__ import annotations

import subprocess

import web_research_cli


def test_run_codex_web_research_uses_live_web_search_and_output_file(monkeypatch, tmp_path):
    captured: dict[str, object] = {}

    monkeypatch.setattr(web_research_cli, "_find_codex_cli", lambda: "codex")

    def fake_run(cmd, *, input, capture_output, text, cwd, timeout, check):
        captured["cmd"] = cmd
        captured["input"] = input
        captured["capture_output"] = capture_output
        captured["text"] = text
        captured["cwd"] = cwd
        captured["timeout"] = timeout
        captured["check"] = check
        output_path = cmd[cmd.index("--output-last-message") + 1]
        with open(output_path, "w") as f:
            f.write('{"findings":[{"topic":"x","finding":"y"}],"summary":"ok"}')
        return subprocess.CompletedProcess(cmd, 0, stdout="ignored stdout", stderr="")

    monkeypatch.setattr(web_research_cli.subprocess, "run", fake_run)

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
    assert captured["cmd"][-4:] == ["--model", "gpt-5.2", "--config", 'web_search="live"']
    assert "--output-last-message" in captured["cmd"]
    assert "system instructions" in captured["input"]
    assert "USER REQUEST:\nquestion" in captured["input"]
    assert captured["cwd"] == tmp_path
    assert captured["timeout"] == 12
    assert output == '{"findings":[{"topic":"x","finding":"y"}],"summary":"ok"}'
    assert metadata["exit_code"] == 0
    assert metadata["output_path_used"] is True
