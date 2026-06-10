from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

UnboundedRunPolicy = Literal["reject_without_explicit_flag", "allow"]
EntryBarStopPolicy = Literal["scan_entry_bar", "entry_next_bar_no_same_bar_scan"]
EodExitPolicy = Literal["force_exit_same_session", "none"]
StopFillPolicy = Literal["stop_price", "open_when_gapped"]


@dataclass(frozen=True)
class BacktestSemanticsContract:
    family_name: str
    result_schema_version: str
    unbounded_run_policy: UnboundedRunPolicy
    entry_bar_stop_policy: EntryBarStopPolicy
    eod_exit_policy: EodExitPolicy
    stop_fill_policy: StopFillPolicy
    notes: tuple[str, ...] = ()


BACKTEST_SEMANTICS_BY_FAMILY: dict[str, BacktestSemanticsContract] = {
    "ema": BacktestSemanticsContract(
        family_name="ema",
        result_schema_version="strategy-result-v1",
        unbounded_run_policy="reject_without_explicit_flag",
        entry_bar_stop_policy="scan_entry_bar",
        eod_exit_policy="force_exit_same_session",
        stop_fill_policy="open_when_gapped",
        notes=(
            "EMA scans the entry bar for stop hits and force-exits open positions "
            "before carrying them across sessions.",
        ),
    ),
    "orb": BacktestSemanticsContract(
        family_name="orb",
        result_schema_version="strategy-result-v1",
        unbounded_run_policy="reject_without_explicit_flag",
        entry_bar_stop_policy="scan_entry_bar",
        eod_exit_policy="force_exit_same_session",
        stop_fill_policy="open_when_gapped",
        notes=(
            "ORB scans same-session bars, force-exits at end of session, and fills "
            "stop hits at the next same-session open under conservative_sl_fill.",
        ),
    ),
    "_demo": BacktestSemanticsContract(
        family_name="_demo",
        result_schema_version="strategy-result-v1",
        unbounded_run_policy="reject_without_explicit_flag",
        entry_bar_stop_policy="scan_entry_bar",
        eod_exit_policy="force_exit_same_session",
        stop_fill_policy="open_when_gapped",
        notes=("Demo strategy follows the production contract shape for tests.",),
    ),
}


def backtest_semantics_for_family(family_name: str) -> BacktestSemanticsContract:
    try:
        return BACKTEST_SEMANTICS_BY_FAMILY[family_name]
    except KeyError as exc:
        raise ValueError(f"No backtest semantics contract registered for {family_name!r}") from exc


def validate_backtest_runtime_config(
    family_name: str,
    config: dict[str, Any],
    source_path: Path | None = None,
) -> dict[str, Any]:
    contract = backtest_semantics_for_family(family_name)
    if contract.unbounded_run_policy == "reject_without_explicit_flag" and not config.get(
        "allow_unbounded_research_backtest"
    ):
        missing = [key for key in ("validation_start", "validation_end") if not config.get(key)]
        if missing:
            source = f" for {source_path}" if source_path is not None else ""
            raise ValueError(
                f"Refusing unbounded {family_name.upper()} backtest"
                f"{source}: missing {', '.join(missing)}. "
                "Set validation_start and validation_end, or explicitly set "
                "allow_unbounded_research_backtest=true."
            )
    return config
