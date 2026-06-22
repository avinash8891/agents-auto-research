from __future__ import annotations

from typing import Any

AMBIGUOUS_PATTERNS = {
    "stocks_in_play": ("stocks in play", "stocks-in-play", "stocks_in_play"),
    "narrow_or": ("narrow or", "narrow-or", "narrow_or", "narrow opening range"),
    "wide_or": ("wide or", "wide-or", "wide_or", "wide opening range"),
}


# ---------------------------------------------------------------------------
# Operationalization: ambiguous thesis → exact contract
# ---------------------------------------------------------------------------


def thesis_needs_operationalization(thesis: dict[str, Any]) -> bool:
    """Check if a thesis contains ambiguous terms needing resolution."""
    haystack = " ".join(str(thesis.get(key, "")) for key in ("hypothesis", "mechanism")).lower()
    return any(term in haystack for terms in AMBIGUOUS_PATTERNS.values() for term in terms)
