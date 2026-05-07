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

SERVER_REPLAY_REJECT_OLD = """  if (usesServerReplayState(body)) {
    return toErrorResponse(
      "Stateless Codex responses endpoint does not support `previous_response_id` or `item_reference`. Replay the full conversation history in `input` on each request."
    );
  }
"""

SERVER_REPLAY_REJECT_NEW = """  // HALO/OpenAI Agents SDK uses previous_response_id/item_reference between turns.
  // Let CodexResponsesState expand those references before forwarding upstream.
"""

SHARED_STATE_OLD = """  const sharedSettings = {
    ...settings,
    responsesState: false
  };
"""

SHARED_STATE_NEW = """  const sharedSettings = {
    ...settings,
    responsesState: settings.responsesState
  };
"""

COLLECT_SSE_OLD = """var collectCompletedResponseFromSse = async (stream) => {
  let latestResponse;
  let latestError;
  for await (const event of iterateServerSentEvents(stream)) {
    if (typeof event.data !== "string" || event.data.length === 0) {
      continue;
    }
    try {
      const parsed = JSON.parse(event.data);
      if (!isRecord2(parsed)) {
        continue;
      }
      if (event.event === "error") {
        latestError = parsed;
        continue;
      }
      const response = parsed.response;
      if (isRecord2(response)) {
        latestResponse = response;
      }
    } catch {
    }
  }
  if (latestResponse) {
    return latestResponse;
  }
  throw new Error(
    `No completed response found in SSE stream.${latestError ? ` Last error: ${JSON.stringify(latestError)}` : ""}`
  );
};
"""

COLLECT_SSE_NEW = """var collectCompletedResponseFromSse = async (stream) => {
  let latestResponse;
  let latestError;
  let responseId;
  const output = [];
  for await (const event of iterateServerSentEvents(stream)) {
    if (typeof event.data !== "string" || event.data.length === 0) {
      continue;
    }
    try {
      const parsed = JSON.parse(event.data);
      if (!isRecord2(parsed)) {
        continue;
      }
      if (event.event === "error" || parsed.type === "error") {
        latestError = parsed;
        continue;
      }
      const response = parsed.response;
      if (isRecord2(response)) {
        latestResponse = response;
        if (typeof response.id === "string") {
          responseId = response.id;
        }
        if (Array.isArray(response.output)) {
          for (const item of response.output) {
            if (isRecord2(item)) output.push(cloneValue(item));
          }
        }
      }
      if (parsed.type === "response.created" && isRecord2(parsed.response) && typeof parsed.response.id === "string") {
        responseId = parsed.response.id;
      }
      if (parsed.type === "response.output_item.done" && isRecord2(parsed.item)) {
        output.push(cloneValue(parsed.item));
      }
      if ((parsed.type === "response.completed" || parsed.type === "response.incomplete") && isRecord2(parsed.response)) {
        latestResponse = parsed.response;
        if (Array.isArray(parsed.response.output)) {
          for (const item of parsed.response.output) {
            if (isRecord2(item)) output.push(cloneValue(item));
          }
        }
      }
    } catch {
    }
  }
  if (latestResponse) {
    if (!Array.isArray(latestResponse.output) || latestResponse.output.length === 0) {
      latestResponse = { ...latestResponse, output };
    }
    return latestResponse;
  }
  if (responseId || output.length > 0) {
    return { id: responseId, output };
  }
  throw new Error(
    `No completed response found in SSE stream.${latestError ? ` Last error: ${JSON.stringify(latestError)}` : ""}`
  );
};
"""

EXPAND_INPUT_OLD = """  expandInput(input) {
    return input.map((item) => {
      if (isRecord3(item) && item.type === "item_reference" && typeof item.id === "string") {
        const cachedItem = this.items.get(item.id);
        if (cachedItem != null) {
          return cloneValue(cachedItem);
        }
      }
      return cloneValue(item);
    });
  }
"""

EXPAND_INPUT_NEW = """  expandInput(input) {
    return input.flatMap((item) => {
      if (isRecord3(item) && item.type === "reasoning" && typeof item.id === "string" && item.id.startsWith("rs_")) {
        return [];
      }
      if (isRecord3(item) && item.type === "item_reference" && typeof item.id === "string") {
        const cachedItem = this.items.get(item.id);
        if (cachedItem != null) {
          return [cloneValue(cachedItem)];
        }
        if (item.id.startsWith("rs_")) {
          return [];
        }
        return [cloneValue(item)];
      }
      return [cloneValue(item)];
    });
  }
"""

REPLACEMENTS = (
    ("doGenerate usage shape", DO_GENERATE_OLD, DO_GENERATE_NEW),
    ("stream usage init", STREAM_INIT_OLD, STREAM_INIT_NEW),
    ("stream finish usage shape", STREAM_FINISH_OLD, STREAM_FINISH_NEW),
    ("responses replay rejection", SERVER_REPLAY_REJECT_OLD, SERVER_REPLAY_REJECT_NEW),
    ("responses shared state", SHARED_STATE_OLD, SHARED_STATE_NEW),
    ("responses SSE state capture", COLLECT_SSE_OLD, COLLECT_SSE_NEW),
    ("responses input replay expansion", EXPAND_INPUT_OLD, EXPAND_INPUT_NEW),
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
