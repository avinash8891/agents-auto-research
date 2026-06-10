from __future__ import annotations

from strategies import STRATEGIES
from strategies.contract import BacktestSemanticsContract


def test_every_strategy_declares_backtest_semantics_contract() -> None:
    for family_name, strategy in STRATEGIES.items():
        contract = strategy.backtest_contract

        assert isinstance(contract, BacktestSemanticsContract), family_name
        assert contract.family_name == family_name
        assert contract.result_schema_version
        assert contract.unbounded_run_policy in {"reject_without_explicit_flag", "allow"}
        assert contract.entry_bar_stop_policy in {
            "scan_entry_bar",
            "entry_next_bar_no_same_bar_scan",
        }
        assert contract.eod_exit_policy in {"force_exit_same_session", "none"}
        assert contract.stop_fill_policy in {"stop_price", "open_when_gapped"}


def test_ema_and_orb_make_different_semantics_explicit() -> None:
    ema = STRATEGIES["ema"].backtest_contract
    orb = STRATEGIES["orb"].backtest_contract

    assert ema.eod_exit_policy == "force_exit_same_session"
    assert orb.eod_exit_policy == "force_exit_same_session"
    assert ema.entry_bar_stop_policy == "scan_entry_bar"
    assert orb.entry_bar_stop_policy == "scan_entry_bar"
    assert ema.unbounded_run_policy == "reject_without_explicit_flag"
    assert orb.unbounded_run_policy == "reject_without_explicit_flag"
