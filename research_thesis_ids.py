from __future__ import annotations


def research_thesis_attempt_id(research_round_id: str, attempt_number: int) -> str:
    """Return the system-assigned thesis id for one round attempt."""
    return f"{research_round_id}-attempt-{int(attempt_number)}"
