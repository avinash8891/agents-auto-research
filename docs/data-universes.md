# Data Universes

Backtests identify market data by `data_universe`, not by checkout-local paths
such as `data`.

## Directory Layout

Set `AUTORESEARCH_DATA_ROOT` on each machine. If it is unset, the runtime uses
`~/autoresearch-data`.

```text
~/autoresearch-data/
  universes/
    nasdaq8/
      open.parquet
      high.parquet
      low.parquet
      close.parquet
      volume.parquet
      manifest.json
    nasdaq143/
      open.parquet
      high.parquet
      low.parquet
      close.parquet
      volume.parquet
      manifest.json
```

## Config

Configs use `data_universe`:

```yaml
family: "ema"
data_universe: "nasdaq8"
symbols: null
validation_start: "2020-01-01"
validation_end: "2023-12-31"
```

The runtime resolves this internally to:

```text
$AUTORESEARCH_DATA_ROOT/universes/nasdaq8
```

The only config entry point for market data is `data_universe`.

## Manifest

`manifest.json` should be a small JSON object:

```json
{
  "data_universe": "nasdaq8",
  "symbol_count": 8,
  "symbols": ["SPY"],
  "start": "2020-01-01",
  "end": "2023-12-31"
}
```

The runtime records `data_provenance` in `result.json`. Machine-specific paths
are included for debugging, but config hashes ignore those paths when a named
`data_universe` is used.
