# Tier 2 Data Acquisition Request

Do not approximate these from OHLCV bars. They require new source data before the
feature table can expose them safely.

| Feature | Minimum data required | Candidate sources |
| --- | --- | --- |
| `order_flow_imbalance` | Tick-level trades with aggressor side or enough quote context to infer trade sign point-in-time. | Polygon tick trades + quotes, Nasdaq TotalView/ITCH, Databento trades + quotes. |
| `signed_volume` | Tick-level prints classified buy/sell at trade time. | Databento, Polygon, Nasdaq ITCH, broker execution feed. |
| `bid_ask_spread` | NBBO or venue quote snapshots at or before each entry bar. | Polygon quotes, Databento MBP-1, Nasdaq Basic/TotalView. |
| `book_depth` | Level 2 order-book depth snapshots at entry time. | Nasdaq TotalView, Databento MBP-10/MBO. |
| `vpin` | Tick-level volume buckets with signed trade volume. | Databento trades + Lee-Ready style signing, Nasdaq ITCH. |
