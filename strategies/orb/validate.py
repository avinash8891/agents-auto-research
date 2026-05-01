from __future__ import annotations

from typing import Any


def validate_orb_runtime_config(config: dict[str, Any]) -> list[str]:
    violations: list[str] = []
    or_min = config.get("or_minutes", 30)
    if not isinstance(or_min, (int, float)):
        violations.append(f"or_minutes={or_min!r}: must be numeric")
    elif not (5 <= or_min <= 120):
        violations.append(f"or_minutes={or_min} out of range [5, 120]")
    tf = config.get("timeframe_minutes", 5)
    if not isinstance(tf, (int, float)):
        violations.append(f"timeframe_minutes={tf!r}: must be numeric")
    elif tf not in (1, 2, 5, 10, 15, 30):
        violations.append(f"timeframe_minutes={tf} not in {{1,2,5,10,15,30}}")
    rr = config.get("rr_ratio", 2.0)
    if not isinstance(rr, (int, float)):
        violations.append(f"rr_ratio={rr!r}: must be numeric")
    elif not (0.5 <= rr <= 20):
        violations.append(f"rr_ratio={rr} out of range [0.5, 20]")
    mhb = config.get("max_hold_bars", 78)
    if not isinstance(mhb, (int, float)):
        violations.append(f"max_hold_bars={mhb!r}: must be numeric")
    elif not (1 <= mhb <= 200):
        violations.append(f"max_hold_bars={mhb} out of range [1, 200]")
    slip = config.get("slippage_pct", 0.05)
    if not isinstance(slip, (int, float)):
        violations.append(f"slippage_pct={slip!r}: must be numeric")
    elif not (0 <= slip <= 1.0):
        violations.append(f"slippage_pct={slip} out of range [0, 1.0]")
    return violations
