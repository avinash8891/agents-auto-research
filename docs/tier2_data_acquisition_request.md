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
| `days_to_fomc` | Forward-known FOMC calendar with announcement dates and times. | Federal Reserve calendar archive/API scrape. |
| `is_earnings_window` | Point-in-time earnings announcement calendar per symbol. | Nasdaq earnings calendar, IEX Cloud, Polygon reference calendar. |
| `days_to_econ_release` | Forward-known macro release calendar with release date/time and event type. | FRED/ALFRED metadata plus BLS/BEA/ISM release calendars. |

