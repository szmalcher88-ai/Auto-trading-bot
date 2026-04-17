"""
Volatility filter — replica of ml.filter_volatility() from
jdehorty/MLExtensions/2 (Pine Script).
"""

import numpy as np


def atr(high, low, close, period):
    """Average True Range — Wilder's RMA smoothing."""
    n = len(close)
    tr = np.zeros(n)
    tr[0] = high[0] - low[0]
    for i in range(1, n):
        tr[i] = max(
            high[i] - low[i],
            abs(high[i] - close[i - 1]),
            abs(low[i] - close[i - 1]),
        )

    # RMA (Wilder's smoothing)
    atr_vals = np.zeros(n)
    atr_vals[period - 1] = np.mean(tr[:period])
    for i in range(period, n):
        atr_vals[i] = (atr_vals[i - 1] * (period - 1) + tr[i]) / period

    return atr_vals


def filter_volatility(high, low, close, min_length, max_length, use_filter):
    """
    Replica of ml.filter_volatility(minLength, maxLength, useVolatilityFilter).

    Compares short-term ATR (min_length) vs long-term ATR (max_length).
    Returns True when short-term volatility > long-term (elevated conditions).
    Returns array of bools, one per bar.
    """
    if not use_filter:
        return np.ones(len(close), dtype=bool)

    atr_short = atr(high, low, close, min_length)
    atr_long = atr(high, low, close, max_length)

    return atr_short > atr_long
