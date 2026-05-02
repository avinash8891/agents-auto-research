"""Shared OHLCV data loading for all strategy backtests.

Loads wide-format parquets (open/high/low/close/volume) from a filesystem path.
Also supports per-symbol subdirectories and flat parquets for ORB compatibility.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd


class DataLoadError(RuntimeError):
    """Recoverable data loading failure for callers to handle."""


def load_data(
    source_path: str,
    symbols: list[str] | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
) -> dict[str, pd.DataFrame]:
    """Load OHLCV data from a filesystem path.

    Tries wide format first (close.parquet, open.parquet, ...),
    then per-symbol subdirectories, then flat parquets.
    """
    data_path = Path(source_path)
    if not data_path.exists():
        # Try relative to caller's parent (for orb-research/data)
        repo_relative = Path(__file__).resolve().parent.parent / data_path
        if repo_relative.exists():
            data_path = repo_relative

    wide_files = ["close", "open", "high", "low", "volume"]

    # Wide format: close.parquet, open.parquet, etc.
    if all((data_path / f"{f}.parquet").exists() for f in wide_files):
        return _load_wide(data_path, wide_files, symbols, start_date, end_date)

    # Per-symbol subdirectories
    if data_path.exists() and any(d.is_dir() for d in data_path.iterdir()):
        return _load_per_symbol(data_path, symbols, start_date, end_date)

    # Flat parquets
    parquets = list(data_path.glob("*.parquet")) if data_path.exists() else []
    if parquets:
        return _load_flat(parquets, start_date, end_date)

    raise FileNotFoundError(f"Could not load data from {source_path}")


def _load_wide(
    data_path: Path,
    fields: list[str],
    symbols: list[str] | None,
    start_date: str | None,
    end_date: str | None,
) -> dict[str, pd.DataFrame]:
    batch: dict[str, pd.DataFrame] = {}
    for name in fields:
        df = pd.read_parquet(data_path / f"{name}.parquet")
        # Normalize timestamps to US/Eastern, no tz
        if df.index.tz is not None:
            df.index = df.index.tz_convert("US/Eastern").tz_localize(None)
        elif df.index.dtype == "datetime64[ns]":
            # Assume UTC if no tz, convert
            try:
                df.index = df.index.tz_localize("UTC").tz_convert("US/Eastern").tz_localize(None)
            except TypeError:
                pass  # already handled
        df = df.between_time("09:30", "15:55")
        if symbols:
            cols = [s for s in symbols if s in df.columns]
            df = df[cols]
        if start_date:
            df = df[df.index >= start_date]
        if end_date:
            df = df[df.index <= end_date]
        batch[name] = df
    return batch


def _load_per_symbol(
    data_path: Path,
    symbols: list[str] | None,
    start_date: str | None,
    end_date: str | None,
) -> dict[str, pd.DataFrame]:
    subdirs = sorted(d.name for d in data_path.iterdir() if d.is_dir())
    if symbols:
        subdirs = [s for s in subdirs if s in symbols]

    all_dfs: dict[str, pd.DataFrame] = {}
    for sym in subdirs:
        sym_dir = data_path / sym
        parquets = list(sym_dir.glob("*.parquet"))
        if parquets:
            df = pd.concat([pd.read_parquet(p) for p in sorted(parquets)])
            all_dfs[sym] = df

    if not all_dfs:
        raise DataLoadError(f"No data found in {data_path}")

    fields = ["Open", "High", "Low", "Close", "Volume"]
    field_map = {"Open": "open", "High": "high", "Low": "low", "Close": "close", "Volume": "volume"}
    batch: dict[str, pd.DataFrame] = {}
    for field in fields:
        series = {sym: df[field] for sym, df in all_dfs.items() if field in df.columns}
        if series:
            wide = pd.DataFrame(series)
            if start_date:
                wide = wide[wide.index >= start_date]
            if end_date:
                wide = wide[wide.index <= end_date]
            batch[field_map[field]] = wide
    return batch


def _load_flat(
    parquets: list[Path],
    start_date: str | None,
    end_date: str | None,
) -> dict[str, pd.DataFrame]:
    df = pd.concat([pd.read_parquet(p) for p in sorted(parquets)])
    if start_date:
        df = df[df.index >= start_date]
    if end_date:
        df = df[df.index <= end_date]
    batch: dict[str, pd.DataFrame] = {}
    for field, key in [
        ("Open", "open"),
        ("High", "high"),
        ("Low", "low"),
        ("Close", "close"),
        ("Volume", "volume"),
    ]:
        if field in df.columns:
            batch[key] = (
                df.pivot(columns="Symbol", values=field) if "Symbol" in df.columns else df[[field]]
            )
    return batch
