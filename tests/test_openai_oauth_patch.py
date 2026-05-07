from __future__ import annotations

import importlib.util
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = REPO_ROOT / "scripts" / "patch_openai_oauth_ai_sdk_v6_usage.py"

spec = importlib.util.spec_from_file_location("patch_openai_oauth_ai_sdk_v6_usage", SCRIPT_PATH)
assert spec and spec.loader
patch_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(patch_module)


def _fixture_source() -> str:
    return "\n".join(
        [
            patch_module.DO_GENERATE_OLD,
            patch_module.STREAM_INIT_OLD,
            patch_module.STREAM_FINISH_OLD,
            patch_module.SERVER_REPLAY_REJECT_OLD,
            patch_module.SHARED_STATE_OLD,
            patch_module.COLLECT_SSE_OLD,
            patch_module.EXPAND_INPUT_OLD,
        ]
    )


def test_patch_openai_oauth_usage_shape_rewrites_all_targets() -> None:
    patched, changed, already = patch_module.patch_text(_fixture_source())

    assert changed == [
        "doGenerate usage shape",
        "stream usage init",
        "stream finish usage shape",
        "responses replay rejection",
        "responses shared state",
        "responses SSE state capture",
        "responses input replay expansion",
    ]
    assert already == []
    assert "inputTokens: {" in patched
    assert "outputTokens: {" in patched
    assert "raw: usage" in patched
    assert "usage.inputTokens.total = value.response.usage.input_tokens" in patched
    assert "CodexResponsesState expand those references" in patched
    assert "responsesState: settings.responsesState" in patched
    assert 'parsed.type === "response.output_item.done"' in patched
    assert 'item.id.startsWith("rs_")' in patched
    assert "return [];" in patched


def test_patch_openai_oauth_usage_shape_is_idempotent() -> None:
    patched, _, _ = patch_module.patch_text(_fixture_source())
    patched_again, changed, already = patch_module.patch_text(patched)

    assert patched_again == patched
    assert changed == []
    assert already == [
        "doGenerate usage shape",
        "stream usage init",
        "stream finish usage shape",
        "responses replay rejection",
        "responses shared state",
        "responses SSE state capture",
        "responses input replay expansion",
    ]
