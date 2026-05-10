"""LLM-as-judge: classify a thesis hypothesis as 'mechanism' or 'param_value_in_prose'.

Recovers the legacy §17 requirement that the hypothesis describe a market
mechanism, not just dress up a parameter change in mechanistic-sounding prose.

The classifier is deliberately injectable so tests can supply a deterministic
fake without an OpenAI call. Production code installs a real classifier (which
calls a cheap completion model) via `set_classifier`.

Caching: results are memoized by SHA-1 of (hypothesis + mechanism) so the same
thesis content is never billed twice within a process.
"""

from __future__ import annotations

import hashlib
from typing import Callable, Literal

JudgeVerdict = Literal["mechanism", "param_value_in_prose"]
ClassifierFn = Callable[[str, str], JudgeVerdict]

_classifier_fn: ClassifierFn | None = None
_cache: dict[str, JudgeVerdict] = {}


def set_classifier(fn: ClassifierFn | None) -> None:
    """Install (or clear) the active classifier. Tests use this to inject a fake."""
    global _classifier_fn
    _classifier_fn = fn


def reset_cache() -> None:
    """Clear the memoization cache. Tests should call this between cases."""
    _cache.clear()


def _cache_key(hypothesis: str, mechanism: str) -> str:
    digest = hashlib.sha1()
    digest.update(hypothesis.strip().encode("utf-8"))
    digest.update(b"||")
    digest.update(mechanism.strip().encode("utf-8"))
    return digest.hexdigest()


def classify_hypothesis(hypothesis: str, mechanism: str) -> JudgeVerdict | None:
    """Return the classifier's verdict, or None when no classifier is installed.

    Returning None means the rule cannot be evaluated; the caller should treat
    that as a soft skip (do not reject the thesis). This makes the rule fail
    open if the LLM is unavailable in development environments.
    """
    if _classifier_fn is None:
        return None
    key = _cache_key(hypothesis, mechanism)
    if key in _cache:
        return _cache[key]
    verdict = _classifier_fn(hypothesis, mechanism)
    _cache[key] = verdict
    return verdict
