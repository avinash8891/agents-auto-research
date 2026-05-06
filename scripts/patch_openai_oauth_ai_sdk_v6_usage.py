#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

DO_GENERATE_OLD = """      usage: {
        inputTokens: usage.input_tokens,
        outputTokens: usage.output_tokens,
        totalTokens: usage.input_tokens + usage.output_tokens,
        reasoningTokens: (_z = (_y = usage.output_tokens_details) == null ? void 0 : _y.reasoning_tokens) != null ? _z : void 0,
        cachedInputTokens: (_B = (_A = usage.input_tokens_details) == null ? void 0 : _A.cached_tokens) != null ? _B : void 0
      },
"""

DO_GENERATE_NEW = """      usage: {
        inputTokens: {
          total: usage.input_tokens,
          noCache: usage.input_tokens - (((_B = (_A = usage.input_tokens_details) == null ? void 0 : _A.cached_tokens) != null ? _B : 0)),
          cacheRead: (_B = (_A = usage.input_tokens_details) == null ? void 0 : _A.cached_tokens) != null ? _B : void 0,
          cacheWrite: void 0
        },
        outputTokens: {
          total: usage.output_tokens,
          text: usage.output_tokens - (((_z = (_y = usage.output_tokens_details) == null ? void 0 : _y.reasoning_tokens) != null ? _z : 0)),
          reasoning: (_z = (_y = usage.output_tokens_details) == null ? void 0 : _y.reasoning_tokens) != null ? _z : void 0
        },
        raw: usage
      },
"""

STREAM_INIT_OLD = """    const usage = {
      inputTokens: void 0,
      outputTokens: void 0,
      totalTokens: void 0
    };
"""

STREAM_INIT_NEW = """    const usage = {
      inputTokens: { total: void 0, noCache: void 0, cacheRead: void 0, cacheWrite: void 0 },
      outputTokens: { total: void 0, text: void 0, reasoning: void 0 },
      raw: void 0
    };
"""

STREAM_FINISH_OLD = """              usage.inputTokens = value.response.usage.input_tokens;
              usage.outputTokens = value.response.usage.output_tokens;
              usage.totalTokens = value.response.usage.input_tokens + value.response.usage.output_tokens;
              usage.reasoningTokens = (_k = (_j = value.response.usage.output_tokens_details) == null ? void 0 : _j.reasoning_tokens) != null ? _k : void 0;
              usage.cachedInputTokens = (_m = (_l = value.response.usage.input_tokens_details) == null ? void 0 : _l.cached_tokens) != null ? _m : void 0;
"""

STREAM_FINISH_NEW = """              usage.inputTokens.total = value.response.usage.input_tokens;
              usage.inputTokens.cacheRead = (_m = (_l = value.response.usage.input_tokens_details) == null ? void 0 : _l.cached_tokens) != null ? _m : void 0;
              usage.inputTokens.noCache = value.response.usage.input_tokens - (usage.inputTokens.cacheRead ?? 0);
              usage.outputTokens.total = value.response.usage.output_tokens;
              usage.outputTokens.reasoning = (_k = (_j = value.response.usage.output_tokens_details) == null ? void 0 : _j.reasoning_tokens) != null ? _k : void 0;
              usage.outputTokens.text = value.response.usage.output_tokens - (usage.outputTokens.reasoning ?? 0);
              usage.raw = value.response.usage;
"""

REPLACEMENTS = (
    ("doGenerate usage shape", DO_GENERATE_OLD, DO_GENERATE_NEW),
    ("stream usage init", STREAM_INIT_OLD, STREAM_INIT_NEW),
    ("stream finish usage shape", STREAM_FINISH_OLD, STREAM_FINISH_NEW),
)


def patch_text(source: str) -> tuple[str, list[str], list[str]]:
    patched = source
    changed: list[str] = []
    already_patched: list[str] = []
    for label, old, new in REPLACEMENTS:
        if new in patched:
            already_patched.append(label)
            continue
        if old not in patched:
            raise ValueError(f"openai-oauth patch target not found: {label}")
        patched = patched.replace(old, new, 1)
        changed.append(label)
    return patched, changed, already_patched


def resolve_chunk_path(path: Path) -> Path:
    if path.is_file():
        return path
    chunk = path / "dist" / "chunk-2AENSHRT.js"
    if chunk.is_file():
        return chunk
    raise FileNotFoundError(f"could not find openai-oauth dist chunk under {path}")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Patch openai-oauth 1.0.2 for AI SDK v6 usage accounting."
    )
    parser.add_argument(
        "path",
        type=Path,
        help="Path to openai-oauth package dir or dist/chunk-2AENSHRT.js",
    )
    parser.add_argument("--check", action="store_true", help="Verify patchability without writing")
    args = parser.parse_args()

    chunk_path = resolve_chunk_path(args.path)
    source = chunk_path.read_text(encoding="utf-8")
    patched, changed, already_patched = patch_text(source)

    if args.check:
        status = "patched" if not changed else "patchable"
        print(
            f"{status}: {chunk_path} "
            f"changed={','.join(changed) or '-'} "
            f"already={','.join(already_patched) or '-'}"
        )
        return 0

    if changed:
        chunk_path.write_text(patched, encoding="utf-8")
    print(
        f"ok: {chunk_path} "
        f"changed={','.join(changed) or '-'} "
        f"already={','.join(already_patched) or '-'}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
