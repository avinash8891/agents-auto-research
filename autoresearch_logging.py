"""Structured-logging helper for autoresearch (rule H).

Emits to stdout in a format that preserves the existing line prefixes
(HEARTBEAT, LOOP_STOP, LOOP_HALT, RUN_COMMAND, BASELINE_RERUN, CONDUCTOR,
THESIS, VERDICT, EVAL, DECISION, RESEARCH_RAW, COMMAND, etc.) so that
downstream log scrapers continue to function byte-identically.

The formatter is just `%(message)s` — no timestamp, no level token, no
logger name. Level filtering still happens via the standard logging
calls (log.info / log.warning / log.error), so rule H's "every ERROR
line answers 'what do I do about this?'" can be enforced by reviewing
log.error call sites.
"""

from __future__ import annotations

import logging
import sys


class _SafeStdoutHandler(logging.StreamHandler):
    def handleError(self, record: logging.LogRecord) -> None:  # noqa: N802
        exc_type, exc, _tb = sys.exc_info()
        if exc_type is BrokenPipeError:
            return
        if isinstance(exc, OSError) and getattr(exc, "errno", None) == 32:
            return
        super().handleError(record)


def get_logger(name: str) -> logging.Logger:
    """Return a logger that writes `%(message)s\\n` to stdout.

    Idempotent: re-importing modules in tests does not duplicate the
    StreamHandler. Propagation is left enabled so pytest's caplog fixture
    can capture records.
    """
    logger = logging.getLogger(name)
    already_attached = any(
        isinstance(h, logging.StreamHandler) and getattr(h, "stream", None) is sys.stdout
        for h in logger.handlers
    )
    if not already_attached:
        handler = _SafeStdoutHandler(sys.stdout)
        handler.setFormatter(logging.Formatter("%(message)s"))
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
    return logger
