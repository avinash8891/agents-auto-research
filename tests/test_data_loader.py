"""Tests for data_loader missing-symbol guard (F3 / CORE-13)."""

from __future__ import annotations

import pandas as pd
import pytest

from data_loader import DataLoadError, load_data

_IDX = pd.date_range("2024-01-02 09:30", periods=3, freq="5min", tz="US/Eastern")
_FIELDS = ["close", "open", "high", "low", "volume"]


def _write_wide_fixture(tmp_path, symbols: list[str]) -> None:
    """Write wide-format parquet files for the given symbols."""
    for field in _FIELDS:
        df = pd.DataFrame(
            {sym: [100.0, 101.0, 102.0] for sym in symbols},
            index=_IDX,
        )
        df.to_parquet(tmp_path / f"{field}.parquet")


def _write_per_symbol_fixture(tmp_path, symbols: list[str]) -> None:
    """Write per-symbol subdirectory fixtures for the given symbols."""
    for sym in symbols:
        sym_dir = tmp_path / sym
        sym_dir.mkdir()
        df = pd.DataFrame(
            {
                "Open": [100.0, 101.0, 102.0],
                "High": [103.0, 104.0, 105.0],
                "Low": [99.0, 100.0, 101.0],
                "Close": [101.0, 102.0, 103.0],
                "Volume": [1000, 1100, 1200],
            },
            index=_IDX,
        )
        df.to_parquet(sym_dir / "data.parquet")


# ---------------------------------------------------------------------------
# Wide format tests
# ---------------------------------------------------------------------------


def test_load_data_raises_on_missing_symbol_wide(tmp_path):
    """Wide path: requesting a symbol absent from parquet raises DataLoadError."""
    _write_wide_fixture(tmp_path, symbols=["AAPL"])

    with pytest.raises(DataLoadError, match="MSFT"):
        load_data(str(tmp_path), symbols=["AAPL", "MSFT"])


def test_load_data_succeeds_when_all_symbols_present_wide(tmp_path):
    """Wide path: all requested symbols present returns data for both."""
    _write_wide_fixture(tmp_path, symbols=["AAPL", "MSFT"])

    result = load_data(str(tmp_path), symbols=["AAPL", "MSFT"])

    assert set(result.keys()) == {"close", "open", "high", "low", "volume"}
    for field, df in result.items():
        assert set(df.columns) == {
            "AAPL",
            "MSFT",
        }, f"Unexpected columns in {field}: {df.columns.tolist()}"


# ---------------------------------------------------------------------------
# Per-symbol format tests
# ---------------------------------------------------------------------------


def test_load_data_raises_on_missing_symbol_per_symbol(tmp_path):
    """Per-symbol path: requesting a symbol with no subdir raises DataLoadError."""
    _write_per_symbol_fixture(tmp_path, symbols=["AAPL"])

    with pytest.raises(DataLoadError, match="MSFT"):
        load_data(str(tmp_path), symbols=["AAPL", "MSFT"])
