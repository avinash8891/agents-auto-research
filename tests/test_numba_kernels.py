from __future__ import annotations

import numpy as np

from numba_kernels import compute_atr_nb


def test_compute_atr_recovers_after_nan_bar() -> None:
    atr = compute_atr_nb(
        np.array([[10.0], [np.nan], [12.0], [13.0]]),
        np.array([[9.0], [np.nan], [11.0], [12.0]]),
        np.array([[9.5], [np.nan], [11.5], [12.5]]),
        2,
    )

    assert np.isfinite(atr[0, 0])
    assert np.isfinite(atr[2, 0])
    assert np.isfinite(atr[3, 0])
