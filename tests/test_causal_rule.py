from __future__ import annotations

import pandas as pd
import pytest

from feature_table import OUTCOME_COLUMNS


def _features() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "trade_id": ["a", "b", "c"],
            "symbol": ["AAA", "BBB", "AAA"],
            "gap_pct": [-1.0, 0.5, -0.2],
            "out_is_loss": [True, False, True],
            "out_pnl": [-1.0, 2.0, -0.5],
        }
    )


def test_canonical_rule_evaluator_accepts_string_literals_and_dynamic_entry_columns() -> None:
    from causal_rule import evaluate_entry_rule

    result = evaluate_entry_rule("symbol == 'AAA' and gap_pct < 0", _features())

    assert result.tolist() == [True, False, True]
    assert result.index.equals(_features().index)


def test_canonical_rule_evaluator_rejects_outcome_and_outcome_like_columns() -> None:
    from causal_rule import RuleExpressionError, evaluate_entry_rule

    with pytest.raises(RuleExpressionError, match="out_is_loss"):
        evaluate_entry_rule("out_is_loss == True", _features(), outcome_columns=OUTCOME_COLUMNS)

    leaked = _features().assign(future_pnl=1.0)
    with pytest.raises(RuleExpressionError, match="leakage column"):
        evaluate_entry_rule("future_pnl > 0", leaked, outcome_columns=OUTCOME_COLUMNS)
