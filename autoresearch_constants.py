"""Named constants for autoresearch.

Replaces inline literals scattered across the loop, research, and experiment
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
MAX_VALIDATION_RETRIES = 3

# ── Model selection ───────────────────────────────────────────────
# Single source of truth for the OpenAI model used by research agents and
# the compiler operationalize pipeline. Replaces _CONDUCTOR_MODEL in
# research_paths.py and _OPENAI_AGENT_MODEL in agent_openai_calls.py.
DEFAULT_AGENT_MODEL = "gpt-5.5"
