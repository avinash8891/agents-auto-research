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
MAX_VALIDATION_RETRIES = 3  # legacy alias = total retries cap (stage_1 + stage_2 + compile)

# Per-stage retry budgets. The retry loop exits when any stage's counter is
# exhausted. Total worst-case attempts = sum of the three budgets.
MAX_VALIDATION_RETRIES_STAGE_1 = 3  # structural / pre-compile rules
MAX_VALIDATION_RETRIES_STAGE_2 = 2  # post-compile semantic rules (currently no-op)
MAX_VALIDATION_RETRIES_COMPILE = 1  # compile failures rarely fix in-loop

# ── Model selection ───────────────────────────────────────────────
# Single source of truth for the OpenAI model used by research agents and
# the compiler operationalize pipeline. Replaces _CONDUCTOR_MODEL in
# research_paths.py and _OPENAI_AGENT_MODEL in agent_openai_calls.py.
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


def research_engine_min_sample(config: dict) -> int:
    value = _research_engine_block(config).get("min_sample", 30)
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("research_engine.min_sample must be an integer") from exc
    if parsed < 1:
        raise ValueError("research_engine.min_sample must be >= 1")
    return parsed


def research_engine_min_abs_lift(config: dict) -> float:
    value = _research_engine_block(config).get("min_abs_lift", 0.10)
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("research_engine.min_abs_lift must be a number") from exc
    if parsed < 0.0 or parsed > 1.0:
        raise ValueError("research_engine.min_abs_lift must be between 0 and 1")
    return parsed


def research_engine_max_p_value(config: dict) -> float:
    value = _research_engine_block(config).get("max_p_value", 0.05)
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("research_engine.max_p_value must be a number") from exc
    if parsed < 0.0 or parsed > 1.0:
        raise ValueError("research_engine.max_p_value must be between 0 and 1")
    return parsed


def research_engine_max_population_overlap(config: dict) -> float:
    value = _research_engine_block(config).get("max_population_overlap", 0.70)
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("research_engine.max_population_overlap must be a number") from exc
    if parsed < 0.0 or parsed > 1.0:
        raise ValueError("research_engine.max_population_overlap must be between 0 and 1")
    return parsed


def research_engine_max_retries(config: dict) -> int:
    value = _research_engine_block(config).get("max_retries", 3)
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("research_engine.max_retries must be an integer") from exc
    if parsed < 1:
        raise ValueError("research_engine.max_retries must be >= 1")
    return parsed


def research_engine_prediction_tolerance_pct(config: dict) -> float:
    value = _research_engine_block(config).get("prediction_tolerance_pct", 20)
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("research_engine.prediction_tolerance_pct must be a number") from exc
    if parsed < 0.0:
        raise ValueError("research_engine.prediction_tolerance_pct must be >= 0")
    return parsed


def research_engine_noise_floor_pct(config: dict) -> float:
    value = _research_engine_block(config).get("noise_floor_pct", 2)
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("research_engine.noise_floor_pct must be a number") from exc
    if parsed < 0.0:
        raise ValueError("research_engine.noise_floor_pct must be >= 0")
    return parsed


def research_engine_retest_extension_months(config: dict) -> int:
    value = _research_engine_block(config).get("retest_extension_months", 6)
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("research_engine.retest_extension_months must be an integer") from exc
    if parsed < 1:
        raise ValueError("research_engine.retest_extension_months must be >= 1")
    return parsed


def research_engine_min_trades(config: dict) -> int:
    value = _research_engine_block(config).get("min_trades", 20)
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("research_engine.min_trades must be an integer") from exc
    if parsed < 1:
        raise ValueError("research_engine.min_trades must be >= 1")
    return parsed


def research_engine_plateau_rounds(config: dict) -> int:
    value = _research_engine_block(config).get("plateau_rounds", 5)
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("research_engine.plateau_rounds must be an integer") from exc
    if parsed < 1:
        raise ValueError("research_engine.plateau_rounds must be >= 1")
    return parsed


def research_engine_plateau_min_skill_gain(config: dict) -> float:
    value = _research_engine_block(config).get("plateau_min_skill_gain", 0.01)
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("research_engine.plateau_min_skill_gain must be a number") from exc
    if parsed < 0.0:
        raise ValueError("research_engine.plateau_min_skill_gain must be >= 0")
    return parsed


def research_engine_holdout_fraction(config: dict) -> float:
    value = _research_engine_block(config).get("holdout_fraction", 0.25)
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("research_engine.holdout_fraction must be a number") from exc
    if parsed <= 0.0 or parsed >= 1.0:
        raise ValueError("research_engine.holdout_fraction must be between 0 and 1")
    return parsed


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
