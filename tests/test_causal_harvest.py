from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from types import SimpleNamespace

import pytest

from causal_harvest import _drain_stream_with_timeout, attach_harvest_lesson
from research_types import HarvestVerdict


class _NeverEndingStream:
    async def stream_events(self):
        while True:
            await asyncio.sleep(1)
            yield object()


def test_drain_stream_with_timeout_bounds_hung_stream() -> None:
    with pytest.raises(asyncio.TimeoutError):
        asyncio.run(_drain_stream_with_timeout(_NeverEndingStream(), timeout_seconds=0.01))


def test_attach_harvest_lesson_falls_back_when_llm_timeout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    round_root = tmp_path / "runtime/jobs/job-1/research/round-1"
    run_output_dir = round_root / "backtest"
    run_output_dir.mkdir(parents=True)
    verdict = HarvestVerdict(
        thesis_id="ema-gap",
        status="supported",
        prediction_results=[{"metric": "profit_factor", "gap": 0.2}],
        summary="deterministic harvest summary",
    )
    controller = SimpleNamespace(family=SimpleNamespace(name="ema"))
    monkeypatch.setattr("causal_harvest._harvest_lesson_llm_enabled", lambda: True)
    monkeypatch.setattr(
        "causal_harvest._generate_harvest_lesson",
        lambda **kwargs: (_ for _ in ()).throw(asyncio.TimeoutError()),
    )

    caplog.set_level(logging.WARNING, logger="causal_harvest")
    updated = attach_harvest_lesson(controller, run_output_dir, verdict)

    assert updated.lesson == "deterministic harvest summary"
    assert "harvest lesson LLM synthesis timed out" in caplog.text
