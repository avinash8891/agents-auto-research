from __future__ import annotations

import asyncio
import json

import pytest

import research_conductor as conductor
from research_prompts import _build_mechanism_system_prompt
from research_types import MechanismProposal


class _StreamedResult:
    def __init__(
        self,
        text: str,
        agent: object | None = None,
        *,
        final_output: object | None = None,
        final_output_as_error: bool = False,
        stream_event_count: int = 0,
    ) -> None:
        self.final_output = final_output if final_output is not None else text
        self.agent = agent
        self.final_output_as_error = final_output_as_error
        self.stream_event_count = stream_event_count

    async def stream_events(self):
        for _ in range(self.stream_event_count):
            yield None

    def final_output_as(self, output_type):
        if self.final_output_as_error:
            raise RuntimeError("final output coercion failed")
        return self.final_output


def _patch_conductor_runner(monkeypatch: pytest.MonkeyPatch, output: dict | str) -> list[str]:
    captured_prompts: list[str] = []
    text = output if isinstance(output, str) else json.dumps(output)
    monkeypatch.setattr(conductor, "_ensure_oauth_proxy", lambda: None)
    monkeypatch.setattr(conductor, "_get_openai_client", lambda url: object())

    def _run_streamed(agent, user_prompt, max_turns, run_config):
        captured_prompts.append(user_prompt)
        assert agent.name == "research-conductor"
        assert getattr(agent, "tools", []) == []
        assert getattr(agent, "output_type", None) is MechanismProposal
        assert run_config.tracing_disabled is True
        return _StreamedResult(text, agent)

    monkeypatch.setattr(conductor.OAIRunner, "run_streamed", _run_streamed)
    return captured_prompts


def test_conductor_requires_rendered_corpus() -> None:
    with pytest.raises(ValueError, match="rendered_corpus"):
        conductor.run_research_conductor_sync("", {}, 1, "ema")


def test_conductor_returns_timeout_error_when_runner_times_out(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(conductor, "_ensure_oauth_proxy", lambda: None)
    monkeypatch.setattr(conductor, "_get_openai_client", lambda url: object())

    def _raise_timeout(agent, user_prompt, max_turns, run_config):
        assert agent.name == "research-conductor"
        assert getattr(agent, "tools", []) == []
        assert run_config.tracing_disabled is True
        raise asyncio.TimeoutError

    monkeypatch.setattr(conductor.OAIRunner, "run_streamed", _raise_timeout)

    out = conductor.run_research_conductor_sync("", {}, 1, "ema", rendered_corpus="## Corpus\n")

    assert out is not None
    assert out.status == "conductor_error"
    assert out.error == "timeout"


def test_conductor_marks_oauth_proxy_failure_as_proxy_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        conductor,
        "_ensure_oauth_proxy",
        lambda: (_ for _ in ()).throw(RuntimeError("openai-oauth proxy unavailable")),
    )

    out = conductor.run_research_conductor_sync("", {}, 1, "ema", rendered_corpus="## Corpus\n")

    assert out is not None
    assert out.status == "conductor_error"
    assert out.error == "proxy_unavailable"


def test_conductor_uses_rendered_corpus_only_and_skips_thesis_validator(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rendered_corpus = "## Corpus\n- family: ema\n\n## Causal Factors\n- f001\n"
    captured: dict[str, object] = {}
    monkeypatch.setattr(conductor, "_ensure_oauth_proxy", lambda: None)
    monkeypatch.setattr(conductor, "_get_openai_client", lambda url: object())
    assert not hasattr(conductor, "validate_thesis_dict")

    def _run_streamed(agent, user_prompt, max_turns, run_config):
        captured["user_prompt"] = user_prompt
        captured["system_prompt"] = agent.instructions
        captured["output_type"] = getattr(agent, "output_type", None)
        captured["tools"] = list(getattr(agent, "tools", []))
        return _StreamedResult(
            json.dumps(
                {
                    "story": "Gap-down entries reveal loss-prone inventory.",
                    "rule": "gap_pct < 0",
                    "competitor_rule": "gap_pct > 0",
                    "competitor_story": "Gap-up entries are the real adverse-selection source.",
                    "actionable": False,
                    "proposed_change": None,
                    "predictions": None,
                }
            ),
            agent,
        )

    monkeypatch.setattr(conductor.OAIRunner, "run_streamed", _run_streamed)

    out = conductor.run_research_conductor_sync(
        "",
        {"status": "keep"},
        research_round=12,
        family_name="ema",
        rejection_feedback="prior screening killed this rule",
        rendered_corpus=rendered_corpus,
    )

    assert out is not None
    assert out.status == "ok"
    assert out.thesis is not None
    assert out.thesis["story"] == "Gap-down entries reveal loss-prone inventory."
    assert str(captured["user_prompt"]).startswith(rendered_corpus)
    assert "prior screening killed this rule" in str(captured["user_prompt"])
    assert "legacy round results" not in str(captured["user_prompt"])
    assert captured["tools"] == []
    assert "feature_table" not in str(captured["system_prompt"])
    assert "residual" in str(captured["system_prompt"]).lower()
    assert getattr(captured["output_type"], "__name__", "") == "MechanismProposal"


def test_conductor_exposes_analyst_tool_when_trades_are_available(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}
    monkeypatch.setattr(conductor, "_ensure_oauth_proxy", lambda: None)
    monkeypatch.setattr(conductor, "_get_openai_client", lambda url: object())

    def _run_streamed(agent, user_prompt, max_turns, run_config):
        captured["tools"] = list(getattr(agent, "tools", []))
        return _StreamedResult(
            json.dumps(
                {
                    "story": "Gap-down entries reveal loss-prone inventory.",
                    "rule": "gap_pct < 0",
                    "competitor_rule": "gap_pct > 0",
                    "competitor_story": "Gap-up entries are the real adverse-selection source.",
                    "actionable": False,
                    "proposed_change": None,
                    "predictions": None,
                }
            ),
            agent,
        )

    monkeypatch.setattr(conductor.OAIRunner, "run_streamed", _run_streamed)

    out = conductor.run_research_conductor_sync(
        "trades.parquet",
        {"status": "keep"},
        research_round=12,
        family_name="ema",
        rendered_corpus="## Corpus\n",
    )

    assert out is not None
    assert out.status == "ok"
    tools = captured["tools"]
    assert isinstance(tools, list)
    assert len(tools) == 1
    assert tools[0].name == "analyze_trades"


def test_conductor_accepts_structured_final_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(conductor, "_ensure_oauth_proxy", lambda: None)
    monkeypatch.setattr(conductor, "_get_openai_client", lambda url: object())
    proposal = MechanismProposal(
        story="Gap-down entries reveal loss-prone inventory.",
        rule="gap_pct < 0",
        competitor_rule="gap_pct > 0",
        competitor_story="Gap-up entries are the real adverse-selection source.",
        actionable=False,
        proposed_change=None,
        predictions=None,
    )

    def _run_streamed(agent, user_prompt, max_turns, run_config):
        return _StreamedResult("", agent, final_output=proposal)

    monkeypatch.setattr(conductor.OAIRunner, "run_streamed", _run_streamed)

    out = conductor.run_research_conductor_sync(
        "",
        {"status": "keep"},
        research_round=12,
        family_name="ema",
        rendered_corpus="## Corpus\n- family: ema\n",
    )

    assert out is not None
    assert out.status == "ok"
    assert out.thesis is not None
    assert out.thesis["story"] == "Gap-down entries reveal loss-prone inventory."


def test_conductor_prefers_final_output_as_over_raw_structured_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(conductor, "_ensure_oauth_proxy", lambda: None)
    monkeypatch.setattr(conductor, "_get_openai_client", lambda url: object())

    class NonCanonicalOutput:
        def __str__(self) -> str:
            return "not-json"

    class CoercedResult(_StreamedResult):
        def __init__(self, agent: object | None = None) -> None:
            super().__init__("", agent, final_output=NonCanonicalOutput())

        def final_output_as(self, output_type):
            return json.dumps(
                {
                    "story": "Canonical SDK output should win.",
                    "rule": "gap_pct < 0",
                    "competitor_rule": "gap_pct > 0",
                    "competitor_story": "Opposite gap sign explains the effect.",
                    "actionable": False,
                    "proposed_change": None,
                    "predictions": None,
                }
            )

    def _run_streamed(agent, user_prompt, max_turns, run_config):
        return CoercedResult(agent)

    monkeypatch.setattr(conductor.OAIRunner, "run_streamed", _run_streamed)

    out = conductor.run_research_conductor_sync(
        "",
        {},
        research_round=12,
        family_name="ema",
        rendered_corpus="## Corpus\n- family: ema\n",
    )

    assert out is not None
    assert out.status == "ok"
    assert out.thesis is not None
    assert out.thesis["story"] == "Canonical SDK output should win."


def test_conductor_reports_parse_failed_for_non_json_runner_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_conductor_runner(monkeypatch, "not json")

    out = conductor.run_research_conductor_sync(
        "",
        {},
        research_round=1,
        family_name="ema",
        rendered_corpus="## Corpus\n",
    )

    assert out is not None
    assert out.status == "conductor_error"
    assert out.error == "parse_failed"


def test_conductor_rejects_retired_should_stop_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_conductor_runner(monkeypatch, {"reasoning": "nothing left", "should_stop": True})

    out = conductor.run_research_conductor_sync(
        "",
        {},
        research_round=1,
        family_name="ema",
        rendered_corpus="## Corpus\n",
    )

    assert out is not None
    assert out.status == "conductor_error"
    assert out.error == "validation_failed"
    assert out.thesis == {"reasoning": "nothing left", "should_stop": True}


def test_mechanism_system_prompt_mentions_corpus_not_feature_table_schema() -> None:
    prompt = _build_mechanism_system_prompt()

    assert "rendered corpus" in prompt.lower()
    assert "story, rule, competitor_rule" in prompt
    assert "feature_table" not in prompt
