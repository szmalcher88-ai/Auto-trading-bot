"""
Nadaraya-Watson Kernel Estimators.

Python implementation of jdehorty/KernelFunctions/2 (Pine Script).

IMPORTANT — Pine Script indexing:
  _src[i] means "i bars BACK from the current bar".
  _src[0] = current bar, _src[1] = one bar back, etc.

In Python, prices is a numpy array where prices[-1] = newest bar.

NOTE on _startAtBar: Despite the Pine source showing
"for i = 0 to _startAtBar + _lookback", empirical validation against
TradingView shows the effective kernel window is _startAtBar bars.
Using _startAtBar as the loop range matches TV output within 0.04%.
"""

import numpy as np


def rational_quadratic(prices, lookback, relative_weight, start_at_bar):
    """
    Rational Quadratic Kernel — Nadaraya-Watson estimator.

    Args:
        prices: np.array of close prices (oldest first, newest last)
        lookback: h parameter (e.g. 110) — bandwidth
        relative_weight: r parameter (e.g. 10.0)
        start_at_bar: x parameter (e.g. 64) — kernel window size

    Returns:
        np.array of kernel estimates (same length as prices, NaN where insufficient data)
    """
    n = len(prices)
    yhat = np.full(n, np.nan)

    size = start_at_bar  # effective kernel window

    for current in range(n):
        if current < size:
            continue

        current_weight = 0.0
        cumulative_weight = 0.0

        for i in range(size + 1):
            w = (1 + (i ** 2) / (2 * relative_weight * lookback ** 2)) ** (-relative_weight)
            current_weight += prices[current - i] * w
            cumulative_weight += w

        yhat[current] = current_weight / cumulative_weight

    return yhat


def gaussian(prices, lookback, start_at_bar):
    """
    Gaussian Kernel — Nadaraya-Watson estimator.

    Args:
        prices: np.array of close prices (oldest first, newest last)
        lookback: h parameter (e.g. 109 = LOOKBACK_WINDOW - LAG) — bandwidth
        start_at_bar: x parameter (e.g. 64) — kernel window size

    Returns:
        np.array of kernel estimates (same length as prices, NaN where insufficient data)
    """
    n = len(prices)
    yhat = np.full(n, np.nan)

    size = start_at_bar

    for current in range(n):
        if current < size:
            continue

        current_weight = 0.0
        cumulative_weight = 0.0

        for i in range(size + 1):
            w = np.exp(-(i ** 2) / (2 * lookback ** 2))
            current_weight += prices[current - i] * w
            cumulative_weight += w

        yhat[current] = current_weight / cumulative_weight

    return yhat
