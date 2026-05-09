from __future__ import annotations

from typing import Any


def _is_int_value(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _is_number_value(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)
