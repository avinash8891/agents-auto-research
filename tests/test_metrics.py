from __future__ import annotations

import math

from metrics import _profit_factor_from_pnl


def test_profit_factor_is_infinite_when_there_are_only_winners() -> None:
    assert math.isinf(_profit_factor_from_pnl([1.0, 2.0, 3.0]))


def test_profit_factor_is_zero_when_there_are_no_winners_and_no_losses() -> None:
    assert _profit_factor_from_pnl([0.0, 0.0]) == 0.0
