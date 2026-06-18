"""Named constants for autoresearch.

Replaces inline literals scattered across the loop, research, and round
modules. Names preserve the behavior of the original code — truncation lengths
and color codes are NOT consolidated where the original used different values
at different log sites, because some downstream log scrapers may key off the
exact byte length. New constants here document the intent of each literal.
"""

from __future__ import annotations

# ── Subprocess timeouts ──────────────────────────────────────────
COMMAND_TIMEOUT_SECONDS = 1800  # 30 minutes — backtest hard cap
DISCORD_HTTP_TIMEOUT_SECONDS = 10

# ── Discord embed formatting ─────────────────────────────────────
# Discord limits the description field to 4096 chars; we truncate to
# 4000 to leave headroom for Markdown that could expand on render.
DISCORD_BODY_MAX_CHARS = 4000

# Discord embed color palette (24-bit RGB integers).
DISCORD_COLOR_ERROR = 0xFF0000  # default failure / unhandled
DISCORD_COLOR_SUCCESS = 0x00CC00  # green: keep / finished / accepted
DISCORD_COLOR_WARNING = 0xFFA500  # orange: blocked / non-fatal
DISCORD_COLOR_DISCARD = 0xFF4500  # red-orange: discard / halted

# ── Hashing ──────────────────────────────────────────────────────
# Truncated SHA-256 length used for run-output dirs and runtime-config hashes.
# Long enough for collision-free indexing within a single autoresearch run.
CONFIG_HASH_LENGTH = 12

# ── Command logging truncation ───────────────────────────────────
# Log lines truncate the shelled-out command at these widths. The values
# differ by call site — they reflect what each scraper expects, not a
# unified preference.
COMMAND_PREVIEW_TRUNCATION = 80  # short preview at "RUN_COMMAND start"
COMMAND_TIMEOUT_TRUNCATION = 100  # at "COMMAND TIMEOUT"
COMMAND_NOTIFICATION_TRUNCATION = 200  # in Discord embed body

# ── Time ─────────────────────────────────────────────────────────
MILLISECONDS_PER_SECOND = 1000

# ── Conductor + research ─────────────────────────────────────────
MAX_RESEARCH_ROUNDS = 100  # safeguard: max single-thesis research iterations
MAX_VALIDATION_RETRIES = 3  # total retry cap from research-engine config defaults

# Per-stage retry budgets. The retry loop exits when any live stage's counter is exhausted.
MAX_VALIDATION_RETRIES_STAGE_1 = 3  # structural / pre-compile rules
MAX_VALIDATION_RETRIES_COMPILE = 1  # compile failures rarely fix in-loop

# ── Model selection ───────────────────────────────────────────────
# Single source of truth for the OpenAI model used by the active conductor
# and compiler operationalize pipeline.
DEFAULT_AGENT_MODEL = "gpt-5.2"

# ── Improvement-loop feature flags (env var names) ───────────────
# All default off. Each flag gates exactly one arrow in the improvement
# loop. Default-off is byte-identical to pre-improvement-loop behavior.
ENV_IMPROVEMENT_HALO = "AUTORESEARCH_IMPROVEMENT_HALO"
ENV_IMPROVEMENT_HALO_APPLY = "AUTORESEARCH_IMPROVEMENT_HALO_APPLY"
ENV_IMPROVEMENT_REFLEXION = "AUTORESEARCH_IMPROVEMENT_REFLEXION"
ENV_IMPROVEMENT_RECURSIVE_IMPROVE = "AUTORESEARCH_IMPROVEMENT_RECURSIVE_IMPROVE"
ENV_IMPROVEMENT_RATCHET = "AUTORESEARCH_IMPROVEMENT_RATCHET"

# Tunable subprocess timeouts for improvement-loop tools (env var names).
# Defaults: 600s (halo CLI) and 1800s (Claude Code subprocess).
ENV_HALO_TIMEOUT_SECONDS = "AUTORESEARCH_HALO_TIMEOUT_SECONDS"
ENV_CLAUDE_TIMEOUT_SECONDS = "AUTORESEARCH_CLAUDE_TIMEOUT_SECONDS"
ENV_RECURSIVE_IMPROVE_INTERVAL = "AUTORESEARCH_RECURSIVE_IMPROVE_INTERVAL"
ENV_RECURSIVE_IMPROVE_MODEL = "AUTORESEARCH_RECURSIVE_IMPROVE_MODEL"
ENV_RECURSIVE_IMPROVE_COMMAND = "AUTORESEARCH_RECURSIVE_IMPROVE_COMMAND"
ENV_RECURSIVE_IMPROVE_TIMEOUT_SECONDS = "AUTORESEARCH_RECURSIVE_IMPROVE_TIMEOUT_SECONDS"

# ── Control-plane / tracing ─────────────────────────────────────
ENV_TRACE_MODE = "AUTORESEARCH_TRACE_MODE"
TRACE_MODE_TRANSACTION = "transaction"
PREPARE_RESULT_MARKER = "AUTORESEARCH_PREPARE_RESULT"

# Default location of the held-out task list relative to the repo root.
HOLDOUT_TASKS_PATH = "configs/eval/holdout_tasks.yaml"


def _research_engine_block(config: dict) -> dict:
    block = config.get("research_engine") if isinstance(config, dict) else None
    return block if isinstance(block, dict) else {}


def _research_engine_walkforward_block(config: dict) -> dict:
    block = _research_engine_block(config).get("walkforward")
    return block if isinstance(block, dict) else {}


def _research_engine_int(config: dict, key: str, default: int, *, minimum: int = 1) -> int:
    value = _research_engine_block(config).get(key, default)
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"research_engine.{key} must be an integer") from exc
    if parsed < minimum:
        raise ValueError(f"research_engine.{key} must be >= {minimum}")
    return parsed


def _research_engine_float(
    config: dict,
    key: str,
    default: float,
    *,
    minimum: float | None = None,
    maximum: float | None = None,
    exclusive_minimum: bool = False,
) -> float:
    value = _research_engine_block(config).get(key, default)
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"research_engine.{key} must be a number") from exc
    if minimum is not None:
        below_minimum = parsed <= minimum if exclusive_minimum else parsed < minimum
        if below_minimum:
            comparator = ">" if exclusive_minimum else ">="
            raise ValueError(f"research_engine.{key} must be {comparator} {minimum:g}")
    if maximum is not None and parsed > maximum:
        if minimum == 0.0 and maximum == 1.0:
            raise ValueError(f"research_engine.{key} must be between 0 and 1")
        raise ValueError(f"research_engine.{key} must be <= {maximum:g}")
    return parsed


def research_engine_min_sample(config: dict) -> int:
    return _research_engine_int(config, "min_sample", 30)


def research_engine_min_abs_lift(config: dict) -> float:
    return _research_engine_float(config, "min_abs_lift", 0.10, minimum=0.0, maximum=1.0)


def research_engine_max_p_value(config: dict) -> float:
    return _research_engine_float(config, "max_p_value", 0.05, minimum=0.0, maximum=1.0)


def research_engine_max_population_overlap(config: dict) -> float:
    return _research_engine_float(
        config,
        "max_population_overlap",
        0.70,
        minimum=0.0,
        maximum=1.0,
    )


def research_engine_max_retries(config: dict) -> int:
    return _research_engine_int(config, "max_retries", 3)


def research_engine_prediction_tolerance_pct(config: dict) -> float:
    return _research_engine_float(config, "prediction_tolerance_pct", 20, minimum=0.0)


def research_engine_noise_floor_pct(config: dict) -> float:
    return _research_engine_float(config, "noise_floor_pct", 2, minimum=0.0)


def research_engine_retest_extension_months(config: dict) -> int:
    return _research_engine_int(config, "retest_extension_months", 6)


def research_engine_min_trades(config: dict) -> int:
    return _research_engine_int(config, "min_trades", 20)


def research_engine_plateau_rounds(config: dict) -> int:
    return _research_engine_int(config, "plateau_rounds", 5)


def research_engine_plateau_min_skill_gain(config: dict) -> float:
    return _research_engine_float(config, "plateau_min_skill_gain", 0.01, minimum=0.0)


def research_engine_holdout_fraction(config: dict) -> float:
    return _research_engine_float(
        config,
        "holdout_fraction",
        0.25,
        minimum=0.0,
        maximum=1.0,
        exclusive_minimum=True,
    )


def research_engine_walkforward_train_months(config: dict) -> int:
    return _research_engine_walkforward_int(config, "train_months", 6)


def research_engine_walkforward_test_months(config: dict) -> int:
    return _research_engine_walkforward_int(config, "test_months", 3)


def research_engine_walkforward_step_months(config: dict) -> int:
    return _research_engine_walkforward_int(config, "step_months", 3)


def research_engine_walkforward_survival_pct(config: dict) -> float:
    value = _research_engine_walkforward_block(config).get("survival_pct", 0.60)
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("research_engine.walkforward.survival_pct must be a number") from exc
    if parsed < 0.0 or parsed > 1.0:
        raise ValueError("research_engine.walkforward.survival_pct must be between 0 and 1")
    return parsed


def _research_engine_walkforward_int(config: dict, key: str, default: int) -> int:
    value = _research_engine_walkforward_block(config).get(key, default)
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"research_engine.walkforward.{key} must be an integer") from exc
    if parsed < 1:
        raise ValueError(f"research_engine.walkforward.{key} must be >= 1")
    return parsed
