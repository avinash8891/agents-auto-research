from __future__ import annotations

import re
from typing import Sequence

import pandas as pd

from feature_table import OUTCOME_COLUMNS

QUERY_KEYWORDS = frozenset({"and", "or", "not", "in", "True", "False", "None", "is", "abs"})
QUERY_NAME_RE = re.compile(r"`([^`]+)`|\b[A-Za-z_]\w*\b")
STRING_LITERAL_RE = re.compile(r"(?s)'(?:\\.|[^'\\])*'|\"(?:\\.|[^\"\\])*\"")
LEAKAGE_COLUMN_NAMES = frozenset(
    {
        "pnl",
        "pnl_abs",
        "pnl_pct",
        "mae",
        "mfe",
        "exit_reason",
        "hold_bars",
        "is_loss",
    }
)
LEAKAGE_COLUMN_PREFIXES = ("out_", "future_", "post_exit_", "post_trade_")
MAX_RULE_CHARS = 500


class RuleExpressionError(ValueError):
    """Raised when a causal rule cannot be safely evaluated on entry-time data."""


def evaluate_entry_rule(
    rule: str,
    features: pd.DataFrame,
    *,
    outcome_columns: Sequence[str] = OUTCOME_COLUMNS,
    max_rule_chars: int = MAX_RULE_CHARS,
) -> pd.Series:
    """Evaluate a pandas-query causal rule after one canonical leakage check."""

    if not rule.strip():
        raise RuleExpressionError("causal rule is empty")
    if len(rule) > max_rule_chars:
        raise RuleExpressionError(f"causal rule exceeds {max_rule_chars} characters")
    validate_entry_rule_references(rule, features.columns, outcome_columns=outcome_columns)
    try:
        matching = features.query(rule)
    except Exception as exc:
        raise RuleExpressionError(f"causal rule failed to evaluate: {rule}") from exc
    return pd.Series(features.index.isin(matching.index), index=features.index, dtype=bool)


def validate_entry_rule_references(
    rule: str,
    columns: Sequence[str],
    *,
    outcome_columns: Sequence[str] = OUTCOME_COLUMNS,
) -> None:
    available = set(columns)
    outcomes = set(outcome_columns)
    allowed_columns = available - outcomes
    for match in QUERY_NAME_RE.finditer(_strip_string_literals(rule)):
        name = match.group(1) or match.group(0)
        if name in QUERY_KEYWORDS:
            continue
        if _is_leakage_column_name(name):
            raise RuleExpressionError(f"causal rule references leakage column: {name}")
        if name in outcomes:
            raise RuleExpressionError(f"causal rule references outcome column: {name}")
        if name not in allowed_columns:
            raise RuleExpressionError(f"causal rule references unknown entry column: {name}")


def _strip_string_literals(rule: str) -> str:
    return STRING_LITERAL_RE.sub("", rule)


def _is_leakage_column_name(name: str) -> bool:
    lowered = name.lower()
    return lowered in LEAKAGE_COLUMN_NAMES or lowered.startswith(LEAKAGE_COLUMN_PREFIXES)
